"""Bilibili live song-request service.

一个进程内守护线程完成：解析房间号、取弹幕 token、连接直播 WebSocket、
心跳/重连、把「点歌-歌名（歌手）」识别的结果交给音乐服务入队。

设计边界：
- 不 import PyQt；事件一律经事件中心广播。
- 网络回调只做解析与入队排队，搜索走 ``ComputeHub`` 的 IO 线程池。
- ``set_enabled(False)`` / ``cleanup()`` 会立即置停止事件并关闭 socket，保证退出不悬挂。
"""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass

import requests

from config.config import LIVE_STREAM
from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.logger import get_logger

from .bilibili_live import (
    build_auth_packet,
    build_heartbeat_packet,
    iter_messages,
    normalize_room_id,
    parse_auth_reply,
    parse_danmaku,
)
from .gift_alerts import get_gift_alert_service
from .service import get_music_service

logger = get_logger(__name__)

_ROOM_INIT_URL = "https://api.live.bilibili.com/room/v1/Room/room_init"
_DANMU_INFO_URL = "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo"
#: 旧版弹幕配置接口：`getDanmuInfo` 对无浏览器指纹的请求回 `-352` 时的回退路径。
_DANMU_CONF_URL = "https://api.live.bilibili.com/room/v1/Danmu/getConf"
_DEFAULT_WS_URL = "wss://broadcastlv.chat.bilibili.com/sub"
_REQUEST_TIMEOUT = 8.0
#: B 站接口按「像不像浏览器」判定请求；缺 UA / Referer 的裸请求更容易被风控拦下。
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_API_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Referer": "https://live.bilibili.com/",
    "Origin": "https://live.bilibili.com",
}
_HEARTBEAT_SECONDS = 30.0
_RECONNECT_DELAYS = (1.0, 2.0, 5.0, 10.0, 30.0)

_DEFAULT_COMMAND_PREFIXES = ("点歌", "點歌")
#: 分隔符允许连写：观众常手打「点歌——歌名」（两个全角破折号）或「点歌--歌名」。
#: 只吃一个会把多余的横线留在歌名里，搜索出来是另一首歌。
_COMMAND_SEPARATOR = r"[-－—–]+"
_BRACKET_RE = re.compile(r"^(.*?)\s*[（(]\s*([^（）()]*?)\s*[）)]\s*$")


@dataclass(frozen=True)
class SongRequest:
    """一条通过语法校验的点歌请求。"""

    title: str
    artist: str = ""
    user_id: str = ""
    username: str = ""

    @property
    def keyword(self) -> str:
        return f"{self.title} {self.artist}".strip()


@dataclass
class LiveStatus:
    """对外可见的直播点歌状态快照。

    ``enabled`` 是总开关（直播模式）的运行时状态；``song_enabled`` 是弹幕点歌自己的开关。
    托盘与设置页的「直播模式」只认 ``enabled``——礼物提醒与点歌共用同一条连接，
    总开关关掉时两者都不工作。
    """

    enabled: bool = False
    connected: bool = False
    room_id: str = ""
    last_error: str = ""
    queued_count: int = 0
    skipped_count: int = 0
    song_enabled: bool = False
    live_status: int = -1


def parse_song_request(
    text: str,
    *,
    prefix: str = "点歌",
    title_max: int = 50,
    artist_max: int = 30,
) -> SongRequest | None:
    """解析 ``点歌-歌名（歌手）``；只接受纯文本，并限制长度。

    默认兼容简体/繁体「点歌」前缀；自定义配置只替换主指令，
    仍然保留默认前缀兼容，避免改配置后旧观众口令失效。
    兼容全角横线与圆括号。歌名不能为空；歌手可省略。
    """
    configured = str(prefix or "").strip() or "点歌"
    prefixes = tuple(dict.fromkeys((configured, *_DEFAULT_COMMAND_PREFIXES)))
    command_re = re.compile(
        rf"^\s*(?:{'|'.join(re.escape(item) for item in prefixes)})\s*{_COMMAND_SEPARATOR}\s*(.+?)\s*$"
    )
    match = command_re.match(str(text or ""))
    if not match:
        return None
    body = match.group(1).strip()
    if not body:
        return None
    artist = ""
    title = body
    bracket = _BRACKET_RE.match(body)
    if bracket:
        candidate_title = bracket.group(1).strip()
        candidate_artist = bracket.group(2).strip()
        if candidate_title:
            title = candidate_title
            artist = candidate_artist
    elif any(char in body for char in "（）()"):
        return None
    title = " ".join(title.split())
    artist = " ".join(artist.split())
    if not title or len(title) > max(1, int(title_max)):
        return None
    if artist and len(artist) > max(1, int(artist_max)):
        return None
    if any(ord(ch) < 32 for ch in title + artist):
        return None
    return SongRequest(title=title, artist=artist)


@dataclass(frozen=True)
class LiveProbeResult:
    """「测试直播间」的一次连通性检查结果。"""

    ok: bool
    room_id: str = ""
    message: str = ""


#: `room_init` 的已知错误码：房间号根本不存在时 B 站回 60004。
_ROOM_NOT_FOUND_CODES = {60004}


def _fetch_room_state(raw_room_id) -> tuple[str, int]:
    """解析真实房间号并带上开播状态（``0`` 未开播 / ``1`` 直播中 / ``2`` 轮播）。

    **必须看 ``code`` 与 ``live_status``**：``room_init`` 对不存在的房间返回
    ``code=60004`` + ``data=[]``，旧实现直接沿用原房间号，于是「测试直播间」把
    不存在的房间报成 ✓ 已连接。旧版 `Danmu/getConf` 对任何房间（含不存在、
    未开播）都发得出 token，WebSocket 也会回 ``{"code":0}``，所以那两处拦不住，
    校验只能在解析房间号这一步做完。

    返回值里的 ``live_status`` 是 ``-1``（接口没给）或 B 站原始整数，供调用方决定
    「未开播」要不要当作错误——探测与真正的连接对它的要求不同。
    """
    configured = normalize_room_id(raw_room_id)
    if not configured:
        raise RuntimeError("请先填写直播间 ID（房间号或 live.bilibili.com 地址）")
    response = requests.get(
        _ROOM_INIT_URL,
        params={"id": configured},
        timeout=_REQUEST_TIMEOUT,
        headers=_API_HEADERS,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("直播间解析失败：接口返回格式异常")
    code = payload.get("code")
    if code not in (0, None):
        message = str(payload.get("msg") or payload.get("message") or "").strip()
        if code in _ROOM_NOT_FOUND_CODES:
            raise RuntimeError(f"直播间 {configured} 不存在")
        detail = f"（code={code}{'，' + message if message else ''}）"
        raise RuntimeError(f"直播间解析失败{detail}")
    data = payload.get("data")
    if not isinstance(data, dict):
        # code=0 却没有 data：只可能是房间号被服务端拒了，不能沿用原号蒙混过去。
        raise RuntimeError(f"直播间 {configured} 不存在")
    real_id = normalize_room_id(data.get("room_id", configured))
    if not real_id:
        raise RuntimeError("直播间 ID 无效")
    try:
        live_status = int(data.get("live_status", -1))
    except (TypeError, ValueError):
        live_status = -1
    return real_id, live_status


def _live_status_hint(live_status: int) -> str:
    """把 B 站的 ``live_status`` 翻成一句人话；未知值返回空串。"""
    if live_status == 0:
        return "该直播间当前未开播"
    if live_status == 2:
        return "该直播间正在轮播（非直播中）"
    return ""


def _fetch_danmaku_endpoint(room_id: str) -> tuple[str, list[dict]]:
    """取弹幕 token 与服务器列表。

    先走新版 `getDanmuInfo`；该接口现在对「没有浏览器指纹」的裸请求一律回
    ``code=-352``（风控），此时回退到仍在服务的旧接口 `room/v1/Danmu/getConf`。
    两者返回的 token 形状一致，认证包可直接复用。

    未开播的直播间在两个接口上都拿不到 token，错误信息要说清这一点，否则用户会以为
    是网络问题。
    """
    payload = _get_json(_DANMU_INFO_URL, {"id": room_id})
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict) and str(data.get("token") or "").strip():
        hosts = data.get("host_list")
        return str(data["token"]).strip(), list(hosts) if isinstance(hosts, list) else []

    fallback = _get_json(_DANMU_CONF_URL, {
        "room_id": room_id,
        "platform": "pc",
        "player": "web",
    })
    fallback_data = fallback.get("data") if isinstance(fallback, dict) else None
    if isinstance(fallback_data, dict):
        token = str(fallback_data.get("token") or "").strip()
        if token:
            hosts = fallback_data.get("host_server_list")
            if not isinstance(hosts, list):
                hosts = fallback_data.get("server_list")
            return token, list(hosts) if isinstance(hosts, list) else []

    code = (payload or {}).get("code") if isinstance(payload, dict) else None
    message = str((payload or {}).get("message") or "").strip() if isinstance(payload, dict) else ""
    detail = f"（code={code}{'，' + message if message else ''}）" if code is not None else ""
    raise RuntimeError(
        f"获取弹幕 token 失败{detail}：请确认直播间存在且正在开播"
    )


def _get_json(url: str, params: dict) -> dict:
    """GET 一次 B 站接口，返回解析后的 JSON（失败抛 RuntimeError）。

    请求头按浏览器补齐：B 站对缺少 ``Referer``/``Origin`` 的裸请求更容易判成机器。
    实测 ``getDanmuInfo`` 现在对裸请求一律回 ``-352``，真正解决问题的是回退到旧接口，
    这里只是让两个接口都拿得到正常响应。
    """
    try:
        response = requests.get(url, params=params, timeout=_REQUEST_TIMEOUT, headers=_API_HEADERS)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"请求失败：{exc}") from exc
    except ValueError as exc:
        raise RuntimeError("接口返回的不是 JSON") from exc
    return payload if isinstance(payload, dict) else {}


def _danmaku_url(hosts: list[dict]) -> str:
    if not hosts:
        return _DEFAULT_WS_URL
    host = str((hosts[0] or {}).get("host") or "").strip()
    return f"wss://{host}/sub" if host else _DEFAULT_WS_URL


def _require_auth_ok(socket) -> None:
    """读一包认证回复；``code`` 非 0 视为连接失败。

    读超时不算失败：认证回复可能晚到，此时连接本身是好的。但一旦收到明确的非 0
    ``code``（如 token 失效、房间号不匹配），必须抛出去——否则界面会显示「已连接」
    却永远收不到弹幕，用户只能看到一片安静。
    """
    raw = socket.recv()
    if not isinstance(raw, (bytes, bytearray)):
        return
    for operation, payload in iter_messages(bytes(raw)):
        if operation != 8:
            continue
        code = parse_auth_reply(payload)
        if code is None or code == 0:
            return
        raise RuntimeError(f"弹幕认证失败（code={code}）")


def probe_live_room(raw_room_id) -> LiveProbeResult:
    """跑一次「测试直播间」：解析房间号 -> 取弹幕 token -> 试建弹幕连接。

    只读探测：不改 ``LIVE_STREAM``、不动 `enabled`、也不启动点歌线程，测试失败不影响
    正在运行的连接。任何一步失败都把原因放在 ``message`` 里返回，不抛异常给 UI。
    调用方负责把它丢到 IO 线程，本函数是阻塞的。
    """
    room_id = normalize_room_id(raw_room_id)
    if not room_id:
        return LiveProbeResult(
            ok=False, message="请先填写直播间 ID（房间号或 live.bilibili.com 地址）"
        )
    try:
        room_id, live_status = _fetch_room_state(room_id)
    except Exception as exc:
        return LiveProbeResult(ok=False, room_id=room_id, message=f"直播间解析失败：{exc}")
    # 未开播/轮播时弹幕服务器仍会正常发 token 并接受认证，但房间里不会有弹幕推送。
    # 探测的用途是「这个房间能不能用」，所以这时必须报失败，否则用户看到 ✓ 之后
    # 一直等不到弹幕，只会以为程序坏了。
    offline_hint = _live_status_hint(live_status)
    if offline_hint:
        return LiveProbeResult(
            ok=False,
            room_id=room_id,
            message=f"{offline_hint}，暂时收不到弹幕",
        )
    try:
        token, hosts = _fetch_danmaku_endpoint(room_id)
    except Exception as exc:
        return LiveProbeResult(ok=False, room_id=room_id, message=f"弹幕服务不可用：{exc}")
    try:
        import websocket  # 延迟导入：缺依赖时给出可读原因而不是 ImportError 栈
    except Exception:
        return LiveProbeResult(
            ok=False,
            room_id=room_id,
            message="弹幕服务正常，但本机缺少 websocket 组件，无法建立弹幕连接",
        )
    socket = None
    try:
        socket = websocket.create_connection(_danmaku_url(hosts), timeout=_REQUEST_TIMEOUT)
        socket.send_binary(build_auth_packet(room_id, token=token))
        socket.settimeout(1.0)
        try:
            _require_auth_ok(socket)
        except Exception as exc:
            # 认证回复可能晚到；只有网络类错误才算失败，读超时视作已连上。
            if not LiveSongRequestService._is_timeout(exc):
                raise
    except Exception as exc:
        return LiveProbeResult(ok=False, room_id=room_id, message=f"弹幕连接失败：{exc}")
    finally:
        if socket is not None:
            try:
                socket.close()
            except Exception:
                pass
    return LiveProbeResult(ok=True, room_id=room_id, message=f"已连接直播间 {room_id}（直播中）")


class LiveSongRequestService:
    """Live danmaku -> search -> queue facade. One instance per process."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # 构造时按当前配置落一次点歌开关，避免「配置里开着、服务里看着是关的」。
        self._status = LiveStatus(song_enabled=bool(LIVE_STREAM.get("enabled", False)))
        self._worker_stop: threading.Event | None = None
        self._configured_room_id = ""
        self._thread: threading.Thread | None = None
        # 可重入：完成回调可能从提交线程同步进来，普通 Lock 会自死锁。
        self._search_lock = threading.RLock()
        self._pending: set[Future] = set()
        self._active_socket = None
        self._recent: dict[str, float] = {}
        self._user_last: dict[str, float] = {}
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.CONFIG_UPDATED, self._on_config_updated)

    # ------------------------------------------------------------------
    # Public state
    # ------------------------------------------------------------------

    @property
    def is_enabled(self) -> bool:
        with self._lock:
            return bool(self._status.enabled)

    def status(self) -> LiveStatus:
        with self._lock:
            return LiveStatus(
                enabled=self._status.enabled,
                connected=self._status.connected,
                room_id=self._status.room_id,
                last_error=self._status.last_error,
                queued_count=self._status.queued_count,
                skipped_count=self._status.skipped_count,
                song_enabled=self._status.song_enabled,
                live_status=self._status.live_status,
            )

    def set_enabled(self, enabled: bool, *, source: str = "manual") -> bool:
        """打开/关闭直播模式（弹幕连接总开关）；返回状态是否真的发生变化。

        总开关只管「要不要连弹幕服务器」：礼物提醒与弹幕点歌共用这条连接，
        各自还有自己的开关（`LIVE_STREAM.enabled` 管点歌）。托盘菜单用的是这个方法。
        """
        target = bool(enabled)
        with self._lock:
            if target == self._status.enabled:
                return False
            if target and not normalize_room_id(LIVE_STREAM.get("room_id")):
                self._status.last_error = "请先在控制面板填写直播间 ID"
                self._publish_status(source=source)
                return False
            self._status.enabled = target
            self._status.last_error = ""
            if not target:
                self._status.connected = False
        if target:
            self._start_worker()
        else:
            self._stop_worker()
        self._publish_status(source=source)
        logger.info("[LiveSong] enabled=%s source=%s", target, source)
        return True

    def set_song_enabled(self, enabled: bool, *, source: str = "manual") -> bool:
        """打开/关闭弹幕点歌；返回状态是否真的发生变化。

        它只影响「收到弹幕要不要解析点歌」，不断开连接——连接由 `set_enabled` 负责。
        点歌开关打开但总开关关着时，`_handle_payload` 不解析点歌，状态里也能看出来。
        """
        target = bool(enabled)
        with self._lock:
            if target == self._status.song_enabled:
                return False
            self._status.song_enabled = target
        self._publish_status(source=source)
        logger.info("[LiveSong] song_enabled=%s source=%s", target, source)
        return True

    def reload_from_config(self) -> None:
        """配置保存后按新开关/房间号热重载。"""
        with self._lock:
            live_enabled = bool(LIVE_STREAM.get("live_mode_enabled", False))
            self._status.song_enabled = bool(LIVE_STREAM.get("enabled", False))
            room_id = normalize_room_id(LIVE_STREAM.get("room_id"))
            changed_room = room_id != self._configured_room_id
            self._configured_room_id = room_id
            if changed_room:
                self._status.room_id = room_id
        if not live_enabled:
            # 总开关关掉时也要把点歌开关的状态同步给 UI，但先停掉连接。
            changed = self.set_enabled(False, source="config")
            if not changed:
                self._publish_status(source="config")
            return
        if self._thread is None or changed_room:
            self.set_enabled(False, source="config")
            self.set_enabled(True, source="config")
        else:
            self.set_enabled(True, source="config")
            self._publish_status(source="config")

    def _on_config_updated(self, event: Event) -> None:
        """只响应包含 ``LIVE_STREAM`` 的配置保存，避免无关保存触发重连。"""
        data = event.data if isinstance(event.data, dict) else {}
        values = data.get("values")
        if not isinstance(values, dict) or "LIVE_STREAM" not in values:
            return
        try:
            self.reload_from_config()
        except Exception as exc:
            logger.warning("[LiveSong] 应用直播配置失败: %s", exc)

    def cleanup(self) -> None:
        """幂等停止：先置停止事件，再关闭 socket 并等待线程收尾。"""
        self._stop_worker()
        self.set_enabled(False, source="cleanup")
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        self._thread = None
        self._worker_stop = None
        with self._search_lock:
            self._pending.clear()
        self._event_center.unsubscribe(EventType.CONFIG_UPDATED, self._on_config_updated)
        with self._lock:
            self._status.enabled = False
            self._status.song_enabled = False
            self._status.connected = False

    # ------------------------------------------------------------------
    # Worker lifecycle
    # ------------------------------------------------------------------

    def _start_worker(self) -> None:
        stop_event = threading.Event()
        with self._lock:
            self._worker_stop = stop_event
        thread = threading.Thread(
            target=self._run,
            args=(stop_event,),
            name="live-song-requests",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def _stop_worker(self) -> None:
        with self._lock:
            stop_event = self._worker_stop
        if stop_event is not None:
            stop_event.set()
        self._close_active_socket()

    def _close_active_socket(self) -> None:
        with self._lock:
            ws = self._active_socket
            self._active_socket = None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def _run(self, stop_event: threading.Event) -> None:
        delay_index = 0
        while not stop_event.is_set():
            try:
                self._connect_once(stop_event)
                delay_index = 0
            except Exception as exc:
                if stop_event.is_set():
                    break
                logger.warning("[LiveSong] 连接失败: %s", exc)
                with self._lock:
                    if self._worker_stop is stop_event:
                        self._status.last_error = str(exc)[:200]
                        self._status.connected = False
                self._publish_status(source="worker")
            delay = _RECONNECT_DELAYS[min(delay_index, len(_RECONNECT_DELAYS) - 1)]
            delay_index += 1
            if stop_event.wait(delay):
                break

    def _connect_once(self, stop_event: threading.Event) -> None:
        room_id = self._resolved_room_id(stop_event)
        logger.info("[LiveSong] 正在连接直播间 %s", room_id)
        token, hosts = self._danmu_info(room_id)
        url = _danmaku_url(hosts)
        import websocket  # 延迟导入：扩展依赖缺失时不影响主程序启动

        if stop_event.is_set():
            return
        ws = websocket.create_connection(url, timeout=_REQUEST_TIMEOUT)
        with self._lock:
            if self._worker_stop is not stop_event:
                superseded = True
            else:
                superseded = False
                self._active_socket = ws
        if superseded:
            try:
                ws.close()
            except Exception:
                pass
            return
        try:
            ws.send_binary(build_auth_packet(room_id, token=token))
            ws.settimeout(1.0)
            _require_auth_ok(ws)
            with self._lock:
                if self._worker_stop is stop_event:
                    self._status.connected = True
                    self._status.last_error = ""
            # 连接成功必须留痕：此前成功路径完全不写日志，用户看到「没有任何输出」
            # 就以为弹幕没在读，实际上只是直播间暂时没人发言。
            logger.info("[LiveSong] 弹幕连接已建立 room=%s host=%s", room_id, url)
            self._publish_status(source="worker")
            last_heartbeat = time.monotonic()
            while not stop_event.is_set():
                if time.monotonic() - last_heartbeat >= _HEARTBEAT_SECONDS:
                    ws.send_binary(build_heartbeat_packet())
                    last_heartbeat = time.monotonic()
                    logger.debug("[LiveSong] 已发送心跳")
                try:
                    raw = ws.recv()
                except Exception as exc:
                    if self._is_timeout(exc):
                        continue
                    raise
                if isinstance(raw, str):
                    raw = raw.encode("utf-8", "ignore")
                for operation, payload in iter_messages(raw):
                    if operation == 5:
                        self._handle_payload(payload, stop_event)
        finally:
            with self._lock:
                if self._active_socket is ws:
                    self._active_socket = None
                if self._worker_stop is stop_event:
                    self._status.connected = False
            try:
                ws.close()
            except Exception:
                pass
            with self._lock:
                current = self._worker_stop is stop_event
            if current:
                self._publish_status(source="worker")

    def _resolved_room_id(self, stop_event: threading.Event) -> str:
        configured = normalize_room_id(LIVE_STREAM.get("room_id"))
        if not configured:
            raise RuntimeError("直播间 ID 未配置")
        real_id, live_status = _fetch_room_state(configured)
        with self._lock:
            if self._worker_stop is stop_event:
                self._status.room_id = real_id
                self._status.live_status = live_status
        # 未开播/轮播不重连失败，但要连上并留着等开播（房间号不会变）。只在日志里留痕，
        # 避免界面在“已连接”和“未开播”之间反复跳；`last_error` 留给真正的连接错误。
        hint = _live_status_hint(live_status)
        if hint:
            logger.info("[LiveSong] %s（room=%s），仍会保持连接等待弹幕", hint, real_id)
        return real_id

    def _danmu_info(self, room_id: str) -> tuple[str, list[dict]]:
        return _fetch_danmaku_endpoint(room_id)

    # ------------------------------------------------------------------
    # Danmaku handling
    # ------------------------------------------------------------------

    def _handle_payload(self, payload: bytes, stop_event: threading.Event | None = None) -> None:
        if stop_event is not None and stop_event.is_set():
            return
        # 礼物提醒与点歌共用这一条连接：先交给礼物模块，它自己判定开关与阈值。
        # 一条负载只可能是其中一种（`SEND_GIFT` 或 `DANMU_MSG`），两边互不干扰。
        try:
            if get_gift_alert_service().handle_payload(payload):
                return
        except Exception as exc:
            logger.warning("[LiveSong] 处理礼物提醒失败: %s", exc)
        message = parse_danmaku(payload)
        if message is None:
            return
        # 每收到一条弹幕都留一行：这是判断「链路到底通不通」最直接的证据。
        # 直播间没人发言时一条都不会有，那和「读不到弹幕」在日志上一眼可分。
        logger.info("[LiveSong] 收到弹幕 %s: %s", message.username or "?", message.text)
        # 礼物提醒已经在上方处理过；点歌有自己的开关，关掉时只记日志不解析命令。
        with self._lock:
            song_enabled = bool(self._status.song_enabled)
        if not song_enabled:
            return
        request = parse_song_request(
            message.text,
            prefix=str(LIVE_STREAM.get("command_prefix", "点歌") or "点歌"),
            title_max=int(LIVE_STREAM.get("song_title_max_length", 50) or 50),
            artist_max=int(LIVE_STREAM.get("artist_max_length", 30) or 30),
        )
        if request is None:
            return
        request = SongRequest(
            title=request.title,
            artist=request.artist,
            user_id=message.user_id,
            username=message.username,
        )
        now = time.monotonic()
        user_key = message.user_id or message.username or message.text
        if not self._allow_request(request, user_key, now):
            return
        with self._search_lock:
            pending_limit = max(1, int(LIVE_STREAM.get("pending_limit", 10) or 10))
            if len(self._pending) >= pending_limit:
                self._status.skipped_count += 1
                return
            try:
                future = get_compute_hub().submit_io(self._search_and_enqueue, request)
            except Exception as exc:
                logger.warning("[LiveSong] 提交点歌任务失败: %s", exc)
                return
            self._pending.add(future)
            # 注册必须在**释放 `_search_lock` 之后**：`Future.add_done_callback` 对已完成
            # 的任务会当场同步执行回调，而 `_discard_pending` 要拿同一把锁（它是普通
            # `Lock`，不可重入）。搜索很快（或线程池空闲）时就会在持锁状态下自死锁，
            # 而这里跑在弹幕接收线程上——卡住后不再 recv、也不再发心跳，表现是
            # 「连接看着正常，却一条弹幕都没有」。见 `tests/test_live_song_requests.py`。
        future.add_done_callback(self._discard_pending)

    def _discard_pending(self, future: Future) -> None:
        with self._search_lock:
            self._pending.discard(future)

    def _allow_request(self, request: SongRequest, user_key: str, now: float) -> bool:
        key = request.keyword.casefold()
        cooldown = max(0.0, float(LIVE_STREAM.get("request_cooldown_seconds", 10) or 0))
        duplicate_window = max(0.0, float(LIVE_STREAM.get("duplicate_window_seconds", 120) or 0))
        with self._lock:
            last_user = self._user_last.get(user_key)
            if cooldown and last_user is not None and now - last_user < cooldown:
                self._status.skipped_count += 1
                return False
            last_song = self._recent.get(key)
            if duplicate_window and last_song is not None and now - last_song < duplicate_window:
                self._status.skipped_count += 1
                return False
            self._user_last[user_key] = now
            self._recent[key] = now
            if len(self._recent) > 500:
                cutoff = now - max(duplicate_window, 60.0)
                self._recent = {k: v for k, v in self._recent.items() if v >= cutoff}
            return True

    def _search_and_enqueue(self, request: SongRequest) -> None:
        try:
            service = get_music_service()
            max_queue = max(1, int(LIVE_STREAM.get("max_queue_size", 200) or 200))
            if len(service.queue_snapshot()) >= max_queue:
                self._record_skip("队列已满")
                return
            track = service.search_first(request.keyword, mode="song", limit=10)
            if track is None:
                self._record_skip(f"未找到《{request.title}》")
                return
            if not service.enqueue_track(track, silent=True, autoplay=False):
                self._record_skip("入队失败")
                return
            with self._lock:
                self._status.queued_count += 1
            self._publish_info(f"直播点歌：{request.username or '观众'} 点了《{track.title}》")
        except Exception as exc:
            logger.warning("[LiveSong] 点歌失败: %s", exc)
            self._record_skip(str(exc)[:80])
        finally:
            self._publish_status(source="queue")

    def _record_skip(self, reason: str) -> None:
        with self._lock:
            self._status.skipped_count += 1
        logger.info("[LiveSong] 跳过点歌: %s", reason)

    def _publish_status(self, *, source: str) -> None:
        snapshot = self.status()
        self._event_center.publish(Event(EventType.LIVE_SONG_STATUS_CHANGE, {
            "enabled": snapshot.enabled,
            "connected": snapshot.connected,
            "room_id": snapshot.room_id,
            "last_error": snapshot.last_error,
            "queued_count": snapshot.queued_count,
            "skipped_count": snapshot.skipped_count,
            "song_enabled": snapshot.song_enabled,
            "live_status": snapshot.live_status,
            "source": source,
        }))

    def _publish_info(self, text: str) -> None:
        if not bool(LIVE_STREAM.get("notify_on_enqueue", True)):
            return
        self._event_center.publish(Event(EventType.INFORMATION, {
            "text": str(text),
            "min": 0,
            "max": 120,
        }))

    def enqueue_song_request(self, text: str, *, username: str = "", user_id: str = "") -> bool:
        """供测试与本地命令复用的入口：解析并异步提交一条点歌请求。"""
        request = parse_song_request(
            text,
            prefix=str(LIVE_STREAM.get("command_prefix", "点歌") or "点歌"),
            title_max=int(LIVE_STREAM.get("song_title_max_length", 50) or 50),
            artist_max=int(LIVE_STREAM.get("artist_max_length", 30) or 30),
        )
        if request is None:
            return False
        request = SongRequest(
            title=request.title,
            artist=request.artist,
            user_id=str(user_id or ""),
            username=str(username or ""),
        )
        user_key = request.user_id or request.username or text
        if not self._allow_request(request, user_key, time.monotonic()):
            return False
        get_compute_hub().submit_io(self._search_and_enqueue, request)
        return True

    @staticmethod
    def _is_timeout(exc: Exception) -> bool:
        text = str(exc).lower()
        return "timed out" in text or "timeout" in text


_instance: LiveSongRequestService | None = None


def get_live_song_request_service() -> LiveSongRequestService:
    global _instance
    if _instance is None:
        _instance = LiveSongRequestService()
    return _instance


def cleanup_live_song_request_service() -> None:
    global _instance
    if _instance is not None:
        _instance.cleanup()
        _instance = None


__all__ = [
    "LiveProbeResult",
    "LiveSongRequestService",
    "LiveStatus",
    "SongRequest",
    "cleanup_live_song_request_service",
    "get_live_song_request_service",
    "parse_song_request",
    "probe_live_room",
]
