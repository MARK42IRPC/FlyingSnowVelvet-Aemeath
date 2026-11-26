"""工具调度模块

职责：
- 订阅流式消息的最终回调事件（STREAM_FINAL）
- 只消费 STREAM_FINAL.tool_call 里已校验的原生 Function Calling 调用；正文不再解析命令标记
- 当前支持的指令：
    音乐 <歌名>  →  搜索并播放歌曲，场上无音响时自动生成；
                     多个歌名（换行或、分隔）时按顺序播放首首、追加其余到队列
    下一曲       →  播放下一首（无参数）
    暂停         →  播放/暂停切换（无参数）
    回忆 <参数>  →  从 memory.txt 提取历史信息并再次发送给大模型
    窥屏         →  截取当前主屏幕并发送给大模型分析一次
    雪豹 <数量>  →  在屏幕底部生成指定数量的雪豹
    沙发 <数量>  →  在宠物位置生成指定数量的沙发
    摩托 <数量>  →  在宠物位置生成指定数量的摩托
    闹钟 <时 分 秒|分 秒|秒>  →  在宠物位置生成倒计时闹钟（默认 30 秒）
    计时 <时 分 秒|分 秒|秒>  →  在宠物位置生成倒计时闹钟（默认 30 秒）
    音量 <值>    →  调整音量（+N 增加，-N 减少，N 设为绝对值，范围 0.0-1.0）
    瞬移 <x y>   →  瞬移主宠物（1=屏幕左/上，0=屏幕右/下）
    浏览器 <网址> →  使用系统默认浏览器打开指定链接
    曲库         →  读取用户 musiclist.txt 曲库，把可选曲目发给大模型供其推荐

事件依赖：
  订阅：STREAM_FINAL          （流式消息最终完整文本，由 ChatHandler 发布）
  发布：MUSIC_PLAY_TOP        （立即播放指定歌曲）
  发布：MUSIC_NEXT_TRACK      （播放下一首）
  发布：MUSIC_PLAY_PAUSE      （播放/暂停切换）
  发布：MUSIC_VOLUME          （调整音量）
    发布：MANAGER_SPAWN_REQUEST （请求生成管理器对象，如音响、雪豹、沙发、闹钟）
  查询：SPEAKER_WINDOW_REQUEST / SPEAKER_WINDOW_RESPONSE （检查场上音响数量）
"""

import re
import random
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING
from urllib.parse import urlparse

from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.logger import get_logger
from lib.script.chat.native_tools import native_tool_to_dispatch, split_music_queries
from lib.script.chat.context_markers import is_auto_companion_topic
from lib.script.music import get_music_service
from config.config import TOOL_DISPATCHER, DRAW, ANIMATION
from config.user_storage_paths import (
    get_user_musiclist_path,
    get_user_state_dir,
)

logger = get_logger(__name__)

if TYPE_CHECKING:
    from lib.script.office.mode import InteractionModeService

_PLAY_INDEX = TOOL_DISPATCHER.get('play_index', 0)
_AUTO_SPAWN_COUNT = TOOL_DISPATCHER.get('auto_spawn_speaker_count', 1)
_DEFAULT_MUSIC_CHOICES = ('靛青宇宙', '碎花', '纸飞机', '小小奇迹', '星炬不息')
# 音乐搜索结果重排权重：歌名像不像 > 作者是不是鸣潮，平手时保持接口热度顺序。
_SONG_SCORE_EXACT_NAME = 2
_SONG_SCORE_PARTIAL_NAME = 1
_SONG_SCORE_MINGCHAO_ARTIST = 2
_MINGCHAO_ARTIST_KEYWORD = '鸣潮'
# 比对歌名时丢掉的噪声字符：接口歌名常带书名号、括号后缀与全角空格。
_SONG_TEXT_NOISE = str.maketrans(
    '', '',
    ' \t\u3000「」『』（）()【】[]{}《》〈〉“”"\'‘’·•-–—_~!！?？,，.。:：;；/\\|',
)
_DEFAULT_TIMER_SECONDS = 30
_MAX_TIMER_SECONDS = 99 * 3600 + 59 * 60 + 59
_MAX_SPAWN_COUNT = 20
_LEGACY_USER_DIR = Path(__file__).resolve().parents[3] / 'resc' / 'user'
_MEMORY_DIR = get_user_state_dir('chat', 'memory')
_MEMORY_FILE_PREFIX_DISPATCHER = 'memory_'
_MEMORY_FILE_SUFFIX = '.txt'
_MEMORY_TIMESTAMP_FORMAT = '%Y-%m-%d %H:%M:%S'
_MEMORY_RECENT_COUNT_DEFAULT = 5
_MEMORY_RANGE_MAX_RATIO = 2


def _get_memory_recall_count() -> int:
    try:
        import config.ollama_config as oc
        raw = oc.OLLAMA.get("memory_recall_count", _MEMORY_RECENT_COUNT_DEFAULT)
        count = int(raw)
    except Exception:
        count = _MEMORY_RECENT_COUNT_DEFAULT
    return max(5, min(50, count))


def _get_memory_recall_recent_count() -> int:
    return _get_memory_recall_count()


def _get_memory_recall_range_count() -> int:
    return _get_memory_recall_count() * _MEMORY_RANGE_MAX_RATIO
_MEMORY_RECALL_REDISPATCH_DELAY_SEC = 5.0
_SCREEN_PEEK_PROMPT = (
    '这是“窥屏工具”刚刚截取的当前主屏幕。请结合截图和已有对话上下文，'
    '直接用自然语言描述你观察到的内容并回应，不要在正文里写命令标记。'
)
_DEFAULT_TOPIC = '日常'
_DEFAULT_MUSICLIST_PATH = Path(__file__).resolve().parents[3] / 'resc' / 'musiclist.txt'
_MUSICLIST_MAX_ITEMS = 60
_MUSICLIST_REDISPATCH_DELAY_SEC = 1.0
_DATETIME_RANGE_PATTERN = re.compile(
    r'\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{1,2}:\d{1,2}'
)
_TIME_ONLY_PATTERN = re.compile(r'\b\d{1,2}:\d{1,2}:\d{1,2}\b')
_MEMORY_LINE_WITH_TOPIC_PATTERN = re.compile(
    r'^\[(?P<ts>[^\]]+)\]\[(?P<topic>[^\]]*)\]\[(?P<role>user|you):\](?P<content>.*)$',
    re.IGNORECASE,
)
_MEMORY_LINE_NO_TOPIC_PATTERN = re.compile(
    r'^\[(?P<ts>[^\]]+)\]\[(?P<role>user|you):\](?P<content>.*)$',
    re.IGNORECASE,
)
def _parse_timer_seconds(arg: str) -> int:
    """解析计时秒数；支持 时分秒/分秒/秒，缺省或无效时回退默认 30 秒。"""
    def _clamp_seconds(value: int) -> int:
        return max(1, min(_MAX_TIMER_SECONDS, int(value)))

    def _try_parse_number(text: str) -> int | None:
        try:
            return int(float(text))
        except (TypeError, ValueError):
            return None

    text = (arg or '').strip()
    if not text:
        return _DEFAULT_TIMER_SECONDS

    normalized = text
    if normalized.endswith('秒钟'):
        normalized = normalized[:-2].strip()
    if normalized.endswith('秒'):
        normalized = normalized[:-1].strip()
    if not normalized:
        return _DEFAULT_TIMER_SECONDS

    # 兼容旧版：闹钟 45
    direct_seconds = _try_parse_number(normalized)
    if direct_seconds is not None:
        return _clamp_seconds(direct_seconds)

    parts = [p for p in re.split(r"\s+", normalized) if p]
    if not parts:
        return _DEFAULT_TIMER_SECONDS

    values: list[int] = []
    for part in parts:
        token = str(part).strip()
        if token.endswith('秒钟'):
            token = token[:-2].strip()
        elif token.endswith('秒'):
            token = token[:-1].strip()
        if not token:
            return _DEFAULT_TIMER_SECONDS
        parsed = _try_parse_number(token)
        if parsed is None or parsed < 0:
            return _DEFAULT_TIMER_SECONDS
        values.append(parsed)

    if len(values) == 1:
        total_seconds = values[0]
    elif len(values) == 2:
        mm, ss = values
        total_seconds = mm * 60 + ss
    elif len(values) == 3:
        hh, mm, ss = values
        total_seconds = hh * 3600 + mm * 60 + ss
    else:
        return _DEFAULT_TIMER_SECONDS

    return _clamp_seconds(total_seconds)


def _parse_datetime_loose(raw: str) -> datetime | None:
    text = str(raw or '').strip()
    if not text:
        return None

    match = re.match(
        r'^(\d{4})[-/](\d{1,2})[-/](\d{1,2})[ T](\d{1,2}):(\d{1,2}):(\d{1,2})$',
        text,
    )
    if not match:
        return None

    try:
        year, month, day, hour, minute, second = (int(part) for part in match.groups())
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def _parse_time_only(raw: str) -> datetime | None:
    text = str(raw or '').strip()
    match = re.match(r'^(\d{1,2}):(\d{1,2}):(\d{1,2})$', text)
    if not match:
        return None

    now = datetime.now()
    try:
        hour, minute, second = (int(part) for part in match.groups())
        return datetime(now.year, now.month, now.day, hour, minute, second)
    except ValueError:
        return None


def _normalize_topic(raw: str) -> str:
    topic = str(raw or '').replace('[', '').replace(']', '').strip()
    return topic or _DEFAULT_TOPIC


def _split_recall_arg(arg: str) -> tuple[str, str]:
    """解析回忆参数，返回 (模式, 主题过滤)。模式: recent / range / invalid"""
    text = str(arg or '').strip().strip('#').strip()
    text = text.rstrip('。！？!?；;，,').strip()
    if not text:
        return 'recent', ''

    if text.startswith('刚刚'):
        topic = text[len('刚刚'):].strip()
        return 'recent', topic

    dt_matches = _DATETIME_RANGE_PATTERN.findall(text)
    time_matches = _TIME_ONLY_PATTERN.findall(text)
    if dt_matches or time_matches:
        return 'range', text
    return 'recent', text



def _normalize_url_arg(raw: str) -> str:
    text = str(raw or '').strip()
    if not text:
        return ""
    text = re.sub(r'\s+', '', text)
    explicit_scheme = re.match(r'^([a-zA-Z][a-zA-Z0-9+\-.]*):', text)
    host_with_port = re.match(r'^[a-zA-Z0-9.-]+:\d+(?:/|$)', text)
    if explicit_scheme and not host_with_port:
        scheme = explicit_scheme.group(1).lower()
        if scheme not in ('http', 'https') or '://' not in text:
            return ''
    if text.startswith('www.'):
        text = f'https://{text}'
    elif text.startswith('//'):
        text = f'https:{text}'
    elif '://' not in text:
        text = f'https://{text}'
    try:
        parsed = urlparse(text)
        parsed.port
    except ValueError:
        return ''
    if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname:
        return ''
    return text


def _parse_spawn_count(arg: str) -> int:
    try:
        count = int(str(arg or '').strip()) if str(arg or '').strip() else 1
    except ValueError:
        count = 1
    return max(1, min(_MAX_SPAWN_COUNT, count))


def _normalize_song_text(text) -> str:
    """把歌名与关键词压成同一形态：去掉标点空白后再比大小写。"""
    return str(text or "").translate(_SONG_TEXT_NOISE).casefold()


class ToolDispatcher:
    """
    工具调度器（全局单例）

    订阅 STREAM_FINAL 事件 → 解析工具调用标记 → 执行对应操作
    """

    def __init__(
        self,
        *,
        defer=None,
        mode_service: "InteractionModeService | None" = None,
    ):
        self._ec = get_event_center()
        self._defer = defer
        self._mode_service = mode_service
        self._ec.subscribe(EventType.STREAM_FINAL, self._on_stream_final)
        logger.info("[ToolDispatcher] 工具调度器已初始化")

    def _accepts_mode_generation(self, mode_generation: int | None) -> bool:
        """判断一次指令调用是否仍属于当前陪伴模式代次。

        `mode_generation is None` 表示调用方没有陪伴模式代次：办公模式的桌宠工具走这条
        路（它们不属于任何一次陪伴对话），只要桌面还停在办公模式就放行；切回陪伴模式后，
        办公发出但还没跑完的异步工作同样作废。
        """
        if self._mode_service is None:
            return True
        if mode_generation is None:
            return self._mode_service.is_office
        return self._mode_service.accepts_companion_generation(mode_generation)

    def _event_mode_generation(self, event: Event) -> tuple[bool, int | None]:
        if self._mode_service is None:
            return True, None
        data = event.data if isinstance(event.data, dict) else {}
        _, current_generation = self._mode_service.snapshot()
        try:
            generation = int(data.get("mode_generation", current_generation))
        except (TypeError, ValueError):
            return False, None
        return self._accepts_mode_generation(generation), generation

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------

    def _on_stream_final(self, event: Event):
        """执行 STREAM_FINAL 携带的原生工具调用。

        工具只走 OpenAI 兼容的 Function Calling：正文里不再解析命令标记，没有 ``tool_call``
        就什么也不做，模型少写一个标记也不会被执行成别的意思。
        """
        accepted, mode_generation = self._event_mode_generation(event)
        if not accepted:
            return
        if event.data.get('allow_tool_commands', True) is False:
            return

        native_tool_call = event.data.get('tool_call')
        if native_tool_call is None:
            return
        parsed = native_tool_to_dispatch(native_tool_call)
        if parsed is None:
            logger.warning("[ToolDispatcher] 忽略无效原生工具调用: %r", native_tool_call)
            return

        cmd, arg = parsed
        logger.info("[ToolDispatcher] 工具调用: cmd=%s arg=%s", cmd, arg or '<无>')

        self.execute_command(cmd, arg, mode_generation=mode_generation)

    def execute_command(
        self,
        command: str,
        argument: str = '',
        *,
        mode_generation: int | None = None,
    ) -> bool:
        """执行一条桌宠指令，返回是否命中已知指令。

        聊天工具调用与办公模式的桌宠工具共用这条路径：调用方负责来源鉴权，
        这里只做指令分派（陪伴模式的 generation 校验留在 `_on_stream_final`）。
        """
        cmd = str(command or '').strip()
        arg = str(argument or '').strip()
        if not cmd:
            return False

        if cmd == '音乐':
            queries = split_music_queries(arg)
            if not queries:
                queries = [random.choice(_DEFAULT_MUSIC_CHOICES)]
                logger.info("[ToolDispatcher] 音乐指令缺少歌名，已改为随机播放: %s", queries[0])
            get_compute_hub().submit_io(self._handle_music_request, queries, mode_generation)

        elif cmd == '下一曲':
            self._ec.publish(Event(EventType.MUSIC_NEXT_TRACK, {}))

        elif cmd == '暂停':
            self._ec.publish(Event(EventType.MUSIC_PLAY_PAUSE, {}))

        elif cmd == '雪豹':
            count = _parse_spawn_count(arg)
            self._ec.publish(Event(EventType.MANAGER_SPAWN_REQUEST, {
                'manager_id': 'snow_leopard',
                'spawn_type': 'command',
                'count': count,
            }))

        elif cmd == '沙发':
            count = _parse_spawn_count(arg)
            self._ec.publish(Event(EventType.MANAGER_SPAWN_REQUEST, {
                'manager_id': 'sofa',
                'spawn_type': 'command',
                'count': count,
            }))

        elif cmd == '摩托':
            count = _parse_spawn_count(arg)
            self._ec.publish(Event(EventType.MANAGER_SPAWN_REQUEST, {
                'manager_id': 'mortor',
                'spawn_type': 'command',
                'count': count,
            }))

        elif cmd in ('闹钟', '计时'):
            seconds = _parse_timer_seconds(arg)
            self._ec.publish(Event(EventType.MANAGER_SPAWN_REQUEST, {
                'manager_id': 'clock',
                'spawn_type': 'command',
                'count': 1,
                'seconds': seconds,
            }))

        elif cmd == '音量':
            self._handle_volume_request(arg)

        elif cmd == '瞬移':
            self._handle_teleport_request(arg)

        elif cmd == '回忆':
            self._handle_memory_recall(arg, mode_generation=mode_generation)

        elif cmd == '窥屏':
            self._handle_screen_peek(mode_generation=mode_generation)

        elif cmd == '浏览器':
            self._handle_browser_request(arg, mode_generation=mode_generation)

        elif cmd == '曲库':
            self._handle_musiclist_request(arg, mode_generation=mode_generation)

        else:
            logger.warning("[ToolDispatcher] 未知指令: %s", cmd)
            return False
        return True

    def _handle_screen_peek(self, *, mode_generation: int | None = None) -> None:
        """截取当前主屏幕，并仅向模型追加一次多模态请求。"""
        if not self._accepts_mode_generation(mode_generation):
            return
        self._ec.publish(Event(EventType.INPUT_CHAT, {
            'text': _SCREEN_PEEK_PROMPT,
            'raw': '窥屏',
            'source': 'tool_screen_peek',
            'capture_screen': True,
            'allow_tool_commands': False,
            'mode_generation': mode_generation,
        }))

    def _handle_volume_request(self, arg: str):
        """
        处理音量调整指令。

        AI 输出百分比整数，内部转换为 0.0-1.0 范围：
        - "+N"  → delta = +N/100（增加 N%）
        - "-N"  → delta = -N/100（减少 N%）
        - "N"   → 设为绝对值 N/100（0-100 → 0.0-1.0）
        """
        if not arg:
            logger.warning("[ToolDispatcher] 音量指令缺少参数")
            return
        try:
            value = float(arg)
        except ValueError:
            logger.warning("[ToolDispatcher] 音量参数无效: %s", arg)
            return

        if arg.startswith('+') or arg.startswith('-'):
            self._ec.publish(Event(EventType.MUSIC_VOLUME, {'delta': value / 100}))
        else:
            # 绝对值：百分比转小数，限制在 0.0-1.0 范围内
            self._ec.publish(Event(EventType.MUSIC_VOLUME, {'volume': max(0.0, min(1.0, value / 100))}))

    def _handle_music_request(
        self,
        queries: str | list[str],
        mode_generation: int | None = None,
    ):
        """
        后台线程：搜索音乐并播放。

        1. 检查场上是否有存活音响
        2. 若无，先请求生成音响
        3. 逐个搜索歌名，各取第一首结果
        4. 首首发布 MUSIC_PLAY_TOP 立即播放，其余按顺序发布 MUSIC_ENQUEUE 追加到队列末尾
        """
        requested = split_music_queries(queries) or ['']
        label = '、'.join(requested)

        if not self._accepts_mode_generation(mode_generation):
            return
        has_speaker = self._check_has_speaker()

        if not self._accepts_mode_generation(mode_generation):
            return

        if not has_speaker:
            logger.info("[ToolDispatcher] 场上无音响，自动生成 %d 个", _AUTO_SPAWN_COUNT)
            self._ec.publish(Event(EventType.MANAGER_SPAWN_REQUEST, {
                'manager_id': 'speaker',
                'count': _AUTO_SPAWN_COUNT,
            }))
            # 等待音响窗口初始化
            threading.Event().wait(timeout=0.5)

        if not self._accepts_mode_generation(mode_generation):
            return

        found: list[tuple[str, str]] = []
        for keyword in requested:
            track_ref, display = self._search_music(keyword)
            if not self._accepts_mode_generation(mode_generation):
                return
            if track_ref is None:
                logger.warning("[ToolDispatcher] 搜索 '%s' 无结果", keyword)
                self._ec.publish(Event(EventType.INFORMATION, {
                    'text': f'没有找到"{keyword}"相关的歌曲',
                    'min': 10,
                    'max': 100,
                }))
                continue
            found.append((track_ref, display))

        if not found:
            return

        if len(requested) > 1:
            logger.info("[ToolDispatcher] 播放 %d 首: %s", len(found), label)
            self._ec.publish(Event(EventType.INFORMATION, {
                'text': f'正在播放{len(found)}首歌曲',
                'min': 10,
                'max': 80,
            }))

        for position, (track_ref, display) in enumerate(found):
            if not self._accepts_mode_generation(mode_generation):
                return
            if position == 0:
                logger.info("[ToolDispatcher] 播放: %s", display)
                self._ec.publish(Event(EventType.MUSIC_PLAY_TOP, {
                    'song_id': track_ref,
                    'track_ref': track_ref,
                    'display': display,
                }))
            else:
                logger.info("[ToolDispatcher] 加入队列: %s", display)
                self._ec.publish(Event(EventType.MUSIC_ENQUEUE, {
                    'song_id': track_ref,
                    'track_ref': track_ref,
                    'display': display,
                    # 批量点歌由上面的一条汇总气泡交代，逐首再弹会连成一串同文案提示。
                    'silent': True,
                }))

    def _check_has_speaker(self) -> bool:
        """
        检查场上是否有存活的音响。

        发布 SPEAKER_WINDOW_REQUEST，等待 SPEAKER_WINDOW_RESPONSE 响应（超时 0.3 秒）。
        """
        result_event = threading.Event()
        has_speaker = [False]

        def _on_response(event: Event):
            rects = event.data.get('rects', [])
            has_speaker[0] = len(rects) > 0
            result_event.set()

        self._ec.subscribe(EventType.SPEAKER_WINDOW_RESPONSE, _on_response)
        self._ec.publish(Event(EventType.SPEAKER_WINDOW_REQUEST, {}))
        result_event.wait(timeout=0.3)
        self._ec.unsubscribe(EventType.SPEAKER_WINDOW_RESPONSE, _on_response)

        return has_speaker[0]

    def _handle_browser_request(
        self,
        arg: str,
        *,
        mode_generation: int | None = None,
    ) -> None:
        url = _normalize_url_arg(arg)
        if not url:
            logger.warning("[ToolDispatcher] 浏览器指令缺少有效网址: %r", arg)
            self._ec.publish(Event(EventType.INFORMATION, {
                'text': '浏览器指令缺少有效网址',
                'min': 10,
                'max': 60,
            }))
            return

        def _open():
            if not self._accepts_mode_generation(mode_generation):
                return
            try:
                opened = webbrowser.open(url, new=2, autoraise=True)
                if not self._accepts_mode_generation(mode_generation):
                    return
                if not opened:
                    logger.warning("[ToolDispatcher] webbrowser.open 返回 False: %s", url)
                    self._ec.publish(Event(EventType.INFORMATION, {
                        'text': f'浏览器未能打开：{url}',
                        'min': 20,
                        'max': 100,
                    }))
                    return
                self._ec.publish(Event(EventType.INFORMATION, {
                    'text': f'已在浏览器打开:\n{url}',
                    'min': 10,
                    'max': 80,
                }))
            except Exception as exc:
                logger.error("[ToolDispatcher] 打开浏览器失败: %s %s", url, exc)
                self._ec.publish(Event(EventType.INFORMATION, {
                    'text': f'打开浏览器失败：{url}\n{exc}',
                    'min': 20,
                    'max': 100,
                }))

        get_compute_hub().submit_io(_open)

    def _musiclist_path(self) -> Path:
        """用户可编辑曲库优先；用户还没建立曲库时回退到仓库内内置曲库。"""
        try:
            user_path = get_user_musiclist_path()
        except Exception:
            user_path = None
        if user_path is not None and user_path.exists():
            return user_path
        return _DEFAULT_MUSICLIST_PATH

    def _seed_musiclist_if_missing(self) -> None:
        """首次使用曲库时，把内置曲库复制成用户可编辑副本，之后不再覆盖。"""
        try:
            user_path = get_user_musiclist_path()
        except Exception:
            return
        if user_path.exists() or not _DEFAULT_MUSICLIST_PATH.is_file():
            return
        try:
            content = _DEFAULT_MUSICLIST_PATH.read_text(encoding='utf-8-sig')
            user_path.parent.mkdir(parents=True, exist_ok=True)
            with user_path.open('x', encoding='utf-8', newline='') as handle:
                handle.write(content)
            logger.info("[ToolDispatcher] 已初始化用户曲库: %s", user_path)
        except OSError:
            logger.warning("[ToolDispatcher] 初始化用户曲库失败: %s", user_path)

    def _read_musiclist_entries(self) -> list[str]:
        """读取曲库：忽略空行、# 与 // 注释，按原顺序去重。"""
        path = self._musiclist_path()
        try:
            text = path.read_text(encoding='utf-8-sig')
        except OSError:
            logger.warning("[ToolDispatcher] 读取曲库失败: %s", path)
            return []
        entries: list[str] = []
        seen: set[str] = set()
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith('#') or line.startswith('//'):
                continue
            key = line.casefold()
            if key in seen:
                continue
            seen.add(key)
            entries.append(line)
        return entries

    def _handle_musiclist_request(self, arg: str, *, mode_generation: int | None = None) -> None:
        """曲库工具：把 musiclist.txt 的候选曲目转交大模型挑选。

        调度器只负责读取与投递，选曲交给模型；模型随后可直接调用播放工具。
        """
        if not self._accepts_mode_generation(mode_generation):
            return
        self._seed_musiclist_if_missing()
        entries = self._read_musiclist_entries()
        if not entries:
            self._ec.publish(Event(EventType.INFORMATION, {
                'text': f'曲库为空：请编辑 {get_user_musiclist_path()}，每行一首曲目',
                'min': 14,
                'max': 120,
            }))
            return

        limited = entries[:_MUSICLIST_MAX_ITEMS]
        listing = '\n'.join(f'{index}. {title}' for index, title in enumerate(limited, start=1))
        prompt = (
            '以上是歌单，现在挑一首歌使用音乐工具直接播放：\n'
            f'{listing}\n'
            '这就是全部可选曲目。只通过音乐工具调用播放，不要在正文里复述选曲过程。'
        )
        self._ec.publish(Event(EventType.INFORMATION, {
            'text': f'曲库 {len(limited)} 首，交给大模型挑选',
            'min': 10,
            'max': 80,
        }))

        def _dispatch_musiclist():
            if not self._accepts_mode_generation(mode_generation):
                return
            self._ec.publish(Event(EventType.INPUT_CHAT, {
                'text': prompt,
                'raw': '曲库',
                'source': 'tool_musiclist',
                'allow_tool_commands': True,
                'mode_generation': mode_generation,
            }))

        self._defer_call(
            int(_MUSICLIST_REDISPATCH_DELAY_SEC * 1000),
            _dispatch_musiclist,
        )

    def _search_music(self, keyword: str):
        """
        使用音乐抽象层搜索音乐，返回 (track_ref, display) 或 (None, None)。

        API 返回结果本身按热度排序，客户端按「歌名像不像 + 是不是鸣潮曲」重排：
        1. 归一化后歌名完全相同 +2，歌名与关键词互相包含 +1
        2. 作者名包含“鸣潮” +2
        平手时保持接口原本的热度顺序（稳定排序）。

        这里不再用「歌名更短优先」当次级键：歌名长短和点歌意图无关，
        它会把接口排在第一位的正确结果挤掉（实测点「拉海洛之心」播成同作者的
        「纸飞机」、点「逆潮」播成「远航星的告别」）。
        """
        try:
            tracks = get_music_service().search(keyword, mode='song', limit=20)
            if not tracks:
                return None, None

            normalized_keyword = _normalize_song_text(keyword)

            def _song_priority(track) -> int:
                """得分越高越靠前；同分保持接口热度顺序。"""
                title = _normalize_song_text(getattr(track, 'title', ''))
                score = 0
                if title and normalized_keyword:
                    if title == normalized_keyword:
                        score += _SONG_SCORE_EXACT_NAME
                    elif normalized_keyword in title or (
                        len(title) >= 2 and title in normalized_keyword
                    ):
                        score += _SONG_SCORE_PARTIAL_NAME
                artist_blob = str(getattr(track, 'artist', '') or '')
                if _MINGCHAO_ARTIST_KEYWORD in artist_blob:
                    score += _SONG_SCORE_MINGCHAO_ARTIST
                return score

            tracks.sort(key=_song_priority, reverse=True)

            idx = min(_PLAY_INDEX, len(tracks) - 1)
            track = tracks[idx]
            track_ref = track.track_id
            display = str(track.display or '').strip()
            if not display:
                name = str(track.title or '未知歌曲').strip() or '未知歌曲'
                artist = str(track.artist or '').strip()
                display = f"--:-- {name} - {artist}" if artist else f"--:-- {name}"

            return track_ref, display

        except Exception as e:
            logger.error("[ToolDispatcher] 搜索音乐失败: %s", e)
            return None, None

    @staticmethod
    def _read_memory_entries() -> list[tuple[datetime, str, str, str]]:
        entries: list[tuple[datetime, str, str, str]] = []
        # 优先读取分日文件目录；兼容旧版单文件 memory.txt
        if _MEMORY_DIR.exists() and _MEMORY_DIR.is_dir():
            memory_files = sorted(
                f for f in _MEMORY_DIR.iterdir()
                if f.is_file() and f.name.startswith(_MEMORY_FILE_PREFIX_DISPATCHER) and f.name.endswith(_MEMORY_FILE_SUFFIX)
            )
        elif (_LEGACY_USER_DIR / 'memory').exists():
            legacy_dir = _LEGACY_USER_DIR / 'memory'
            memory_files = sorted(
                f for f in legacy_dir.iterdir()
                if f.is_file() and f.name.startswith(_MEMORY_FILE_PREFIX_DISPATCHER) and f.name.endswith(_MEMORY_FILE_SUFFIX)
            )
        elif (_LEGACY_USER_DIR / 'memory.txt').exists():
            memory_files = [_LEGACY_USER_DIR / 'memory.txt']
        else:
            return entries

        for file_path in memory_files:
            try:
                with file_path.open('r', encoding='utf-8') as f:
                    for raw_line in f:
                        line = raw_line.strip()
                        if not line:
                            continue

                        # 新格式：[时间][主题][user:/you:]内容
                        match = _MEMORY_LINE_WITH_TOPIC_PATTERN.match(line)
                        if match:
                            ts_text = (match.group('ts') or '').strip()
                            topic = _normalize_topic(match.group('topic') or '')
                            role = (match.group('role') or '').strip().lower()
                            content = (match.group('content') or '').strip()
                            ts = _parse_datetime_loose(ts_text)
                            if ts is None or not content:
                                continue
                            entries.append((ts, topic, role, content))
                            continue

                        # 兼容上一版格式：[时间][user:/you:]内容（无主题）
                        match = _MEMORY_LINE_NO_TOPIC_PATTERN.match(line)
                        if match:
                            ts_text = (match.group('ts') or '').strip()
                            role = (match.group('role') or '').strip().lower()
                            content = (match.group('content') or '').strip()
                            ts = _parse_datetime_loose(ts_text)
                            if ts is None or not content:
                                continue
                            entries.append((ts, _DEFAULT_TOPIC, role, content))
                            continue

                        # 兼容旧格式：YYYY-MM-DD HH:MM:SS,内容（默认按 you + 日常 处理）
                        if ',' in line:
                            ts_text, content = line.split(',', 1)
                            ts = _parse_datetime_loose(ts_text)
                            content = content.strip()
                            if ts is None or not content:
                                continue
                            entries.append((ts, _DEFAULT_TOPIC, 'you', content))
            except OSError as e:
                logger.error("[ToolDispatcher] 读取 memory 文件失败 %s: %s", file_path.name, e)
        return entries

    @staticmethod
    def _parse_recall_range(arg: str) -> tuple[datetime | None, datetime | None, str]:
        text = str(arg or '').strip().strip('#').strip().rstrip('。！？!?；;，,').strip()
        if not text:
            return None, None, ''

        matches = _DATETIME_RANGE_PATTERN.findall(text)
        if len(matches) >= 2:
            start_dt = _parse_datetime_loose(matches[0])
            end_dt = _parse_datetime_loose(matches[1])
            remain = text.replace(matches[0], '', 1).replace(matches[1], '', 1).strip()
            remain = re.sub(r'^[\s,，。;；:：~\-到至]+|[\s,，。;；:：~\-到至]+$', '', remain)
            return start_dt, end_dt, remain

        # 兼容仅输入时分秒：按当天日期补全
        time_matches = _TIME_ONLY_PATTERN.findall(text)
        if len(time_matches) >= 2:
            start_dt = _parse_time_only(time_matches[0])
            end_dt = _parse_time_only(time_matches[1])
            remain = text.replace(time_matches[0], '', 1).replace(time_matches[1], '', 1).strip()
            remain = re.sub(r'^[\s,，。;；:：~\-到至]+|[\s,，。;；:：~\-到至]+$', '', remain)
            return start_dt, end_dt, remain

        # 兼容单时间点：只给一个时间（主题可选）
        if len(matches) == 1:
            only = _parse_datetime_loose(matches[0])
            remain = text.replace(matches[0], '', 1).strip()
            remain = re.sub(r'^[\s,，。;；:：~\-到至]+|[\s,，。;；:：~\-到至]+$', '', remain)
            return only, only, remain
        if len(time_matches) == 1:
            only = _parse_time_only(time_matches[0])
            remain = text.replace(time_matches[0], '', 1).strip()
            remain = re.sub(r'^[\s,，。;；:：~\-到至]+|[\s,，。;；:：~\-到至]+$', '', remain)
            return only, only, remain

        return None, None, ''

    @staticmethod
    def _score_recall_items(
        items: list[tuple[datetime, str, str, str]],
        topic_filter: str,
    ) -> list[tuple[float, tuple[datetime, str, str, str]]]:
        """对匹配条目加权评分：匹配质量 × 时间衰减，返回 (score, item) 列表按分降序。"""
        tf = topic_filter.lower()
        now = datetime.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)

        scored: list[tuple[float, tuple[datetime, str, str, str]]] = []
        for ts, topic, role, content in items:
            topic_lower = topic.lower()
            content_lower = content.lower()

            if topic_lower == tf:
                match_score = 3.0
            elif tf in topic_lower:
                match_score = 2.0
            elif tf in content_lower:
                match_score = 1.0
            else:
                continue

            days_ago = (today - ts.replace(hour=0, minute=0, second=0, microsecond=0)).days
            if days_ago <= 0:
                decay = 1.0
            elif days_ago <= 7:
                decay = 0.8
            elif days_ago <= 30:
                decay = 0.5
            else:
                decay = 0.3

            scored.append((match_score * decay, (ts, topic, role, content)))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored

    @staticmethod
    def _build_recall_message(scope: str, items: list[tuple[datetime, str, str, str]]) -> str:
        lines = [
            f"- [{ts.strftime(_MEMORY_TIMESTAMP_FORMAT)}][{topic}][{role}:] {content}"
            for ts, topic, role, content in items
        ]
        return (
            "以下是“回忆工具”提取的历史信息，请基于这些信息继续回复。\n"
            f"回忆范围：{scope}\n"
            "回忆内容：\n"
            f"{chr(10).join(lines)}\n"
            "请直接输出自然语言。"
        )

    def _handle_memory_recall(
        self,
        arg: str,
        *,
        mode_generation: int | None = None,
    ):
        """
        回忆工具：
        - topic：最近某个主题
        - start_time/end_time：起止时间
        """
        arg_text = str(arg or '').strip().strip('#').strip()
        arg_text = arg_text.rstrip('。！？!?；;，,').strip()
        entries = self._read_memory_entries()

        if not entries:
            self._ec.publish(Event(EventType.INFORMATION, {
                'text': '回忆为空：暂无可用记忆',
                'min': 12,
                'max': 80,
            }))
            return

        mode, mode_arg = _split_recall_arg(arg_text)
        is_recent_mode = mode == 'recent'
        selected: list[tuple[datetime, str, str, str]] = []
        scope_text = ''
        topic_filter = ''

        if is_recent_mode:
            topic_filter = mode_arg.strip()
            limit = _get_memory_recall_recent_count()
            if topic_filter:
                scored = self._score_recall_items(entries, topic_filter)
                selected = [item for _, item in scored[:limit]]
            else:
                selected = [item for item in entries if not is_auto_companion_topic(item[1])][-limit:]
            scope_text = '最近'
            if topic_filter:
                scope_text += f' / 主题:{topic_filter}'
        elif mode == 'range':
            start_dt, end_dt, topic_filter = self._parse_recall_range(mode_arg)
            if start_dt is None or end_dt is None:
                self._ec.publish(Event(EventType.INFORMATION, {
                    'text': '回忆参数无效：请说明要回忆的主题或时间范围',
                    'min': 12,
                    'max': 120,
                }))
                return
            if end_dt < start_dt:
                start_dt, end_dt = end_dt, start_dt

            pool = [
                item for item in entries
                if start_dt <= item[0] <= end_dt
                and (topic_filter or not is_auto_companion_topic(item[1]))
            ]
            limit = _get_memory_recall_range_count()
            if topic_filter:
                scored = self._score_recall_items(pool, topic_filter)
                selected = [item for _, item in scored[:limit]]
            else:
                selected = pool[-limit:]
            scope_text = f'{start_dt.strftime(_MEMORY_TIMESTAMP_FORMAT)} ~ {end_dt.strftime(_MEMORY_TIMESTAMP_FORMAT)}'
            if topic_filter:
                scope_text += f' / 主题:{topic_filter}'
        else:
            self._ec.publish(Event(EventType.INFORMATION, {
                'text': '回忆参数无效：请说明要回忆的主题或时间范围',
                'min': 12,
                'max': 120,
            }))
            return

        if not selected:
            self._ec.publish(Event(EventType.INFORMATION, {
                'text': '指定范围内没有可用回忆',
                'min': 12,
                'max': 80,
            }))
            return

        message = self._build_recall_message(scope_text, selected)
        self._ec.publish(Event(EventType.INFORMATION, {
            'text': f'回忆命中 {len(selected)} 条，{int(_MEMORY_RECALL_REDISPATCH_DELAY_SEC)}秒后发送给大模型',
            'min': 10,
            'max': 80,
        }))

        def _dispatch_recall_message():
            if not self._accepts_mode_generation(mode_generation):
                return
            self._ec.publish(Event(EventType.INPUT_CHAT, {
                'text': message,
                'raw': f'回忆 {arg_text or "刚刚"}',
                'source': 'tool_recall',
                'allow_tool_commands': False,
                'mode_generation': mode_generation,
            }))

        self._defer_call(
            int(_MEMORY_RECALL_REDISPATCH_DELAY_SEC * 1000),
            _dispatch_recall_message,
        )

    def _defer_call(self, delay_ms: int, callback) -> None:
        if self._defer is not None:
            self._defer(delay_ms, callback)
            return
        from lib.core.render.registry import get_deferred_call

        deferred_call = get_deferred_call()
        if deferred_call is not None:
            deferred_call(delay_ms, callback)
        else:
            callback()

    def _parse_teleport_position(self, arg: str) -> tuple[int, int] | None:
        """
        解析瞬移参数并映射到屏幕坐标。

        输入格式：x y
        映射规则：1=屏幕左/上，0=屏幕右/下（反向归一化）
        """
        numbers = re.findall(r'[-+]?\d+(?:\.\d+)?', arg or '')
        if len(numbers) < 2:
            return None

        try:
            nx = float(numbers[0])
            ny = float(numbers[1])
        except (TypeError, ValueError):
            return None

        # 只接受归一化坐标，避免误把像素值当参数。
        if not (0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0):
            return None

        screen_w = int(DRAW.get('screen_width', 1920))
        screen_h = int(DRAW.get('screen_height', 1080))
        pet_w, pet_h = ANIMATION.get('pet_size', (150, 150))
        max_x = max(0, screen_w - int(pet_w))
        max_y = max(0, screen_h - int(pet_h))

        # 1 => 左/上(0px), 0 => 右/下(maxpx)
        x = int(round((1.0 - nx) * max_x))
        y = int(round((1.0 - ny) * max_y))
        return x, y

    def _handle_teleport_request(self, arg: str):
        """处理瞬移指令，发布项目内置 PET_TELEPORT 事件。"""
        pos = self._parse_teleport_position(arg)
        if pos is None:
            logger.warning("[ToolDispatcher] 瞬移参数无效: %s", arg)
            self._ec.publish(Event(EventType.INFORMATION, {
                'text': '瞬移参数无效：需要 x/y 两个 0~1 的坐标',
                'min': 10,
                'max': 100,
            }))
            return

        x, y = pos
        self._ec.publish(Event(EventType.PET_TELEPORT, {
            'entity_id': 'pet_window',
            'x': x,
            'y': y,
        }))

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------

    def cleanup(self):
        """取消事件订阅"""
        self._ec.unsubscribe(EventType.STREAM_FINAL, self._on_stream_final)
        logger.info("[ToolDispatcher] 已清理")


# ----------------------------------------------------------------------
# 全局单例
# ----------------------------------------------------------------------

_instance: Optional[ToolDispatcher] = None


def get_tool_dispatcher(
    *,
    mode_service: "InteractionModeService | None" = None,
) -> ToolDispatcher:
    """获取全局 ToolDispatcher 实例（单例）"""
    global _instance
    if _instance is None:
        if mode_service is None:
            from lib.script.office.mode import get_interaction_mode_service

            mode_service = get_interaction_mode_service()
        _instance = ToolDispatcher(mode_service=mode_service)
    return _instance


def cleanup_tool_dispatcher():
    """清理全局 ToolDispatcher 实例"""
    global _instance
    if _instance is not None:
        _instance.cleanup()
        _instance = None
