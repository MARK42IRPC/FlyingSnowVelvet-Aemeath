"""Bilibili live danmaku protocol helpers.

只做协议编解码，不含网络、线程或 UI：房间号规范化、认证包、心跳包、
zlib 解包与 ``DANMU_MSG`` 提取都在这里，便于离线单测。
"""

from __future__ import annotations

import json
import re
import struct
import zlib
from dataclasses import dataclass
from urllib.parse import urlparse

_HEADER = struct.Struct(">IHHII")
_OP_HEARTBEAT = 2
_OP_HEARTBEAT_REPLY = 3
_OP_MESSAGE = 5
_OP_AUTH = 7
_OP_AUTH_REPLY = 8
_MAX_MESSAGE_BYTES = 8 * 1024 * 1024

def _room_id_from_bilibili_url(text: str) -> str:
    candidate = text if "://" in text else f"https://{text}"
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ""
    host = str(parsed.hostname or "").lower()
    if host not in {"live.bilibili.com", "www.live.bilibili.com"}:
        return ""
    for segment in reversed([part for part in parsed.path.split("/") if part]):
        if segment.isdigit():
            value = int(segment)
            return str(value) if value > 0 else ""
    return ""


def normalize_room_id(raw) -> str:
    """把直播间号、URL 或 ``live.bilibili.com/123`` 统一成纯数字串。

    只接受数字；短号与真实房间号都由 B 站 ``room_init`` 再解析一次。
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.isdigit():
        value = int(text)
        return str(value) if value > 0 else ""
    if "bilibili.com" in text.lower():
        return _room_id_from_bilibili_url(text)
    return ""


def build_packet(operation: int, payload: bytes = b"") -> bytes:
    """组一个 B 站直播协议包（头部 16 字节，大端）。"""
    body = payload or b""
    header = _HEADER.pack(16 + len(body), 16, 1, int(operation), 1)
    return header + body


def build_auth_packet(room_id: str, uid: int = 0, token: str = "", buvid: str = "") -> bytes:
    payload = json.dumps({
        "uid": int(uid),
        "roomid": int(room_id),
        "protover": 2,
        "platform": "web",
        "type": 2,
        "key": str(token or ""),
        "buvid": str(buvid or ""),
    }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return build_packet(_OP_AUTH, payload)


def parse_auth_reply(payload: bytes) -> int | None:
    """解析认证回复 ``{"code": 0}``；解析不出来时返回 ``None``。

    认证失败时 B 站仍会先回一包 ``op=8``，只是 ``code`` 非 0（如 ``-101`` 未登录）。
    调用方拿到非 0 必须当作失败，否则会一直停在「已连接」却收不到任何弹幕。
    """
    try:
        document = json.loads(payload.decode("utf-8", "replace"))
    except (TypeError, ValueError):
        return None
    if not isinstance(document, dict) or "code" not in document:
        return None
    try:
        return int(document.get("code"))
    except (TypeError, ValueError):
        return None


def build_heartbeat_packet() -> bytes:
    return build_packet(_OP_HEARTBEAT, b"[object Object]")


@dataclass(frozen=True)
class DanmakuMessage:
    """一条已解析的弹幕：文本、发送者标识与显示名。"""

    text: str
    user_id: str = ""
    username: str = ""


@dataclass(frozen=True)
class GiftMessage:
    """一条已解析的礼物：礼物名、数量、单价与送礼人。

    ``price`` 是 B 站 ``SEND_GIFT.data.price`` 的原始单位（金瓜子），只用来和用户设置的
    阈值比较，不做货币换算——换算规则属于上游，跟着变会让阈值失去意义。
    """

    gift_name: str
    number: int = 1
    price: int = 0
    user_id: str = ""
    username: str = ""

    @property
    def total_price(self) -> int:
        """这一批礼物的总价（单价 × 数量），阈值按它判定。"""
        return max(0, int(self.price)) * max(1, int(self.number))


def iter_messages(raw: bytes):
    """逐条产出 ``(operation, payload_bytes)``。

    ``protover=2`` 时服务端用 zlib 压缩一整批消息；解包后仍按协议头切分。
    遇到截断/超长/非法长度直接停止，不让脏数据把接收线程拖住。
    """
    data = raw or b""
    if len(data) < 16:
        return
    offset = 0
    total = len(data)
    while offset + 16 <= total:
        packet_len, header_len, _version, operation, _sequence = _HEADER.unpack_from(data, offset)
        if packet_len < 16 or header_len < 16 or packet_len > _MAX_MESSAGE_BYTES:
            return
        end = offset + packet_len
        if end > total:
            return
        payload = data[offset + header_len:end]
        offset = end
        if operation == _OP_MESSAGE and payload[:1] == b"\x78":
            try:
                inner = zlib.decompress(payload)
            except zlib.error:
                continue
            yield from iter_messages(inner)
            continue
        yield int(operation), payload


def _load_document(payload: bytes) -> dict | None:
    try:
        document = json.loads(payload.decode("utf-8", "replace"))
    except (TypeError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def _command_of(document: dict) -> str:
    """取命令名；B 站偶尔带 ``:`` 后缀（如 ``DANMU_MSG:4:0:2:2:2:0``）。"""
    return str(document.get("cmd") or "").split(":", 1)[0]


def parse_gift(payload: bytes) -> GiftMessage | None:
    """从一条 ``SEND_GIFT`` 负载里取礼物信息。

    免费/盲盒类礼物也会走 ``SEND_GIFT``，它们的 ``price`` 可能为 0；数量缺省按 1 处理。
    解析不出礼物名时返回 ``None``——没有名字的提醒对用户没有意义。
    """
    document = _load_document(payload)
    if document is None or _command_of(document) != "SEND_GIFT":
        return None
    data = document.get("data")
    if not isinstance(data, dict):
        return None
    gift_name = str(data.get("giftName") or "").strip()
    if not gift_name:
        return None
    try:
        number = int(data.get("num") or 1)
    except (TypeError, ValueError):
        number = 1
    try:
        price = int(data.get("price") or 0)
    except (TypeError, ValueError):
        price = 0
    return GiftMessage(
        gift_name=gift_name,
        number=max(1, number),
        price=max(0, price),
        user_id=str(data.get("uid") or ""),
        username=str(data.get("uname") or "").strip(),
    )


def parse_danmaku(payload: bytes) -> DanmakuMessage | None:
    """从一条 ``DANMU_MSG`` 负载里取文本与用户标识。"""
    try:
        document = json.loads(payload.decode("utf-8", "replace"))
    except (TypeError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    if str(document.get("cmd") or "").split(":", 1)[0] != "DANMU_MSG":
        return None
    info = document.get("info")
    if not isinstance(info, list) or len(info) < 3:
        return None
    text = str(info[1] or "").strip()
    if not text:
        return None
    user_id = ""
    username = ""
    user = info[2]
    if isinstance(user, list) and len(user) >= 2:
        user_id = str(user[0] or "")
        username = str(user[1] or "")
    return DanmakuMessage(text=text, user_id=user_id, username=username)


__all__ = [
    "DanmakuMessage",
    "GiftMessage",
    "build_auth_packet",
    "build_heartbeat_packet",
    "build_packet",
    "iter_messages",
    "normalize_room_id",
    "parse_auth_reply",
    "parse_danmaku",
    "parse_gift",
]
