"""礼物提醒：直播弹幕里出现礼物时，按模板生成一句话并送进语音与消息队列。

与直播点歌平行的功能，共用同一条弹幕连接（`live_song_requests` 的守护线程）：
弹幕线程把 ``SEND_GIFT`` 负载交过来，这里只做「要不要响应 / 文案怎么拼 / 送进哪两个队列」，
网络与连接的重连、心跳全部仍由点歌线程负责。

设计边界：
- 不 import PyQt；消息走 ``INFORMATION`` 事件、语音走 ``AI_VOICE_REQUEST``，都是既有事件。
- 不直接调用 TTS 或气泡：那两个消费者各自有开关与队列，本模块只负责「申请」。
- 文案占位符是 ``<gift>`` / ``<number>`` / ``<name>``，替换后多余的空白会被收拢，
  免得模板写错时气泡里出现一串空格。
"""

from __future__ import annotations

import re

from lib.core.event.center import Event, EventType, get_event_center
from lib.core.logger import get_logger

from .bilibili_live import GiftMessage, parse_gift

logger = get_logger(__name__)

#: 默认文案：用户没填或填成空白时用它，避免气泡里只剩一句「感谢」。
DEFAULT_GIFT_TEMPLATE = "感谢<name>送出的<number>个<gift>"

_PLACEHOLDER_RE = re.compile(r"<\s*(gift|number|name)\s*>", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"[ \t　]{2,}")


def render_gift_text(gift: GiftMessage, template: str = "") -> str:
    """把模板里的占位符替换成这条礼物的实际值。

    未知占位符原样保留（用户可能想写 ``<3`` 之类的符号，静默吞掉更难排查）；
    送礼人名字缺失时用「有人」兜底，免得出现「感谢送出的 3 个小心心」这种残句。
    """
    source = str(template or "").strip() or DEFAULT_GIFT_TEMPLATE
    values = {
        "gift": gift.gift_name,
        "number": str(max(1, int(gift.number))),
        "name": gift.username or "有人",
    }

    def _replace(match: re.Match) -> str:
        return values[match.group(1).lower()]

    text = _PLACEHOLDER_RE.sub(_replace, source)
    # 模板里占位符两侧的空白在替换后可能连成一片，收拢成单个空格。
    return _WHITESPACE_RE.sub(" ", text).strip()


def should_alert(gift: GiftMessage, *, enabled: bool, min_price: int) -> bool:
    """这条礼物要不要响应。

    ``min_price`` 为 0 表示「所有礼物都响应」；阈值按这一批礼物的**总价**
    （单价 × 数量）判定，所以送 10 个 1 元的礼物与 1 个 10 元的礼物等价。
    """
    if not enabled:
        return False
    try:
        threshold = max(0, int(min_price))
    except (TypeError, ValueError):
        threshold = 0
    if threshold <= 0:
        return True
    return gift.total_price >= threshold


class GiftAlertService:
    """礼物提醒的判定与投递；单例由 `get_gift_alert_service()` 持有。"""

    def __init__(self) -> None:
        self._event_center = get_event_center()

    # ── 对外入口 ─────────────────────────────────────────────────────

    def handle_payload(self, payload: bytes) -> str:
        """处理一条弹幕负载；真的响应了返回渲染后的文案，否则返回空串。

        返回值给点歌线程做日志与测试断言用，本身不影响投递。
        """
        gift = parse_gift(payload)
        if gift is None:
            return ""
        settings = _settings()
        if not should_alert(
            gift,
            enabled=bool(settings.get("enabled", False)),
            min_price=settings.get("min_price", 0),
        ):
            return ""
        text = render_gift_text(gift, str(settings.get("template", "") or ""))
        if not text:
            return ""
        self.deliver(text, gift=gift, settings=settings)
        logger.info(
            "[GiftAlert] %s 送出 %s x%d（总价 %d）→ %s",
            gift.username or gift.user_id or "匿名",
            gift.gift_name,
            gift.number,
            gift.total_price,
            text,
        )
        return text

    def deliver(self, text: str, *, gift: GiftMessage | None = None, settings: dict | None = None) -> None:
        """把一句话同时送进消息队列与语音队列（两者各有自己的开关）。"""
        settings = settings if settings is not None else _settings()
        if bool(settings.get("notify_in_chat", True)):
            self._publish_message(text)
        if bool(settings.get("speak", True)):
            self._publish_voice(text)

    # ── 投递 ─────────────────────────────────────────────────────────

    def _publish_message(self, text: str) -> None:
        """气泡消息：与聊天/办公同一条 ``INFORMATION`` 通道，带自己的来源便于撤销。"""
        try:
            self._event_center.publish(Event(EventType.INFORMATION, {
                "text": text,
                "min": 0,
                "max": 100,
                "align": "left",
                "particle": False,
                "source": "gift_alert",
            }))
        except Exception as exc:
            logger.warning("[GiftAlert] 发送礼物消息失败: %s", exc)

    def _publish_voice(self, text: str) -> None:
        """语音申请：与聊天回复走同一条 ``AI_VOICE_REQUEST``，由语音模块自己决定要不要念。"""
        try:
            from lib.script.chat.handler_stream_presenter import _detect_ai_voice_language

            lang = _detect_ai_voice_language(text)
        except Exception:
            lang = "auto"
        try:
            self._event_center.publish(Event(EventType.AI_VOICE_REQUEST, {
                "text": text,
                "text_lang": lang,
                "interruptible": True,
            }))
        except Exception as exc:
            logger.warning("[GiftAlert] 发送礼物语音失败: %s", exc)


def _settings() -> dict:
    """读当前礼物提醒配置。

    每次现取 `config.config.GIFT_ALERT` 而不是导入期捕获：设置页保存走的是
    `apply_general_values()` 的就地 `dict.update()`，但那之后也可能整体替换字典
    （测试与热重载都会），模块级快照会让新值读不到。
    """
    try:
        import config.config as cc

        values = getattr(cc, "GIFT_ALERT", None)
        return values if isinstance(values, dict) else {}
    except Exception:
        return {}


_service: GiftAlertService | None = None


def get_gift_alert_service() -> GiftAlertService:
    global _service
    if _service is None:
        _service = GiftAlertService()
    return _service


def cleanup_gift_alert_service() -> None:
    global _service
    _service = None
