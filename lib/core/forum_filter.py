"""留言墙的本地内容过滤：违规词、链接与长数字串。

服务端只有一条 200 字的长度约束，没有任何内容检查，所以「会不会被刷屏」这件事只能
在客户端先拦一道。这里定的是**保守**规则：宁可少发几条，也不让留言墙变成广告板。

判定顺序固定，命中即返回，调用方拿到的永远是第一条命中的原因：

  1. **链接**：``http(s)://``、``www.``、``.com``/``.cn`` 等常见顶级域，以及 ``[url]``
     之类的标签写法。附带网址总是违规——留言墙没有做外链安全的余量。
  2. **超长数字串**：六位及以上连续阿拉伯数字（允许中间夹 ``,``/``-``/``.``/空格）。
     手机号、QQ 号、群号、价格表都属于这一类。
  3. **违规词**：``FORUM_BANNED_WORDS`` 里的词，忽略大小写、全角半角与常见分隔符
     （``*``/``.``/`` ``/``-``/``_``），所以 ``加*微``、``加 微`` 都拦得住。

判定前会先洗掉正文里的颜色令牌（``lib/core/forum_colors.py``）与段落排版令牌
（``lib/core/forum_layout.py``）：前者的六位十六进制数、后者的字号数字都会被长数字规则
误伤，而它们本来就不算留言内容。违规词表按用途分成四组，``category`` 跟着命中词所属的
组走——窗口层按这个类别挑提示语音，别让「广告」配上「骂人」的话术。

模块不导入任何 GUI 库，返回的是结构化的 ``ForumViolation``，由调用方决定怎么提示。
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from lib.core.forum_colors import strip_color_tokens
from lib.core.forum_layout import strip_layout_tokens

#: 违规词的分类：只表示「这条留言是哪一类问题」，UI 提示与提示语音都按它选话术。
FORUM_CATEGORY_LINK = "link"
FORUM_CATEGORY_NUMBER = "number"
FORUM_CATEGORY_TRAFFIC = "traffic"
FORUM_CATEGORY_PROMOTION = "promotion"
FORUM_CATEGORY_ABUSE = "abuse"
FORUM_CATEGORY_ILLEGAL = "illegal"
FORUM_CATEGORY_GENERIC = "generic"

#: 违规词表：命中任一即拒绝。全部按「已经去掉分隔符的小写形式」比较。
#:
#: 维护约定：只放**明确**的违规词，不要放正常留言可能出现的词。词表故意保持短小，
#: 宁可漏拦也不要误伤；需要收紧时在这里加词，不需要改判定逻辑。
#:
#: 分组同时承担两件事：词本身，以及命中后播哪一类提示语音（`FORUM_CATEGORY_*`）。
#: 换分组等于换话术，所以「骂人」和「广告」不能合成一组。
FORUM_BANNED_WORD_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # 引流 / 联系方式
    (
        FORUM_CATEGORY_TRAFFIC,
        (
            "加微信", "加qq", "加群", "进群", "拉群", "私聊", "私我",
            "微信号", "二维码", "扫码", "公众号", "关注我",
        ),
    ),
    # 广告 / 交易
    (
        FORUM_CATEGORY_PROMOTION,
        (
            "代练", "代充", "出售", "低价出", "免费领", "点击领取", "刷单", "返利",
            "优惠券", "折扣码", "拼单", "带货",
        ),
    ),
    # 辱骂与攻击
    (
        FORUM_CATEGORY_ABUSE,
        ("傻逼", "煞笔", "沙比", "智障", "脑残", "废物", "滚出去", "去死"),
    ),
    # 违法违规
    (
        FORUM_CATEGORY_ILLEGAL,
        ("外挂", "破解版", "盗版", "赌博", "博彩", "色情", "代开", "发票"),
    ),
)

#: 所有违规词，按分组顺序铺平；只关心「哪些词」的调用方用它。
FORUM_BANNED_WORDS: tuple[str, ...] = tuple(
    word for _category, words in FORUM_BANNED_WORD_GROUPS for word in words
)

#: 判定原因码；UI 与语音提示按它选择文案。
FORUM_REASON_LINK = "link"
FORUM_REASON_LONG_NUMBER = "long_number"
FORUM_REASON_BANNED_WORD = "banned_word"

#: 长数字串的最小位数：六位及以上视为违规（手机号 / QQ 号 / 群号）。
FORUM_LONG_NUMBER_MIN_DIGITS = 6

#: 链接判定：协议、``www.``、常见顶级域、以及 ``[url]``/``<a>`` 这类标签写法。
_LINK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"https?\s*:\s*//", re.IGNORECASE),
    re.compile(r"\bwww\s*\.", re.IGNORECASE),
    re.compile(r"\b[\w-]+\s*\.\s*(?:com|cn|net|org|io|cc|tv|xyz|top|vip|me|app|shop|site)\b", re.IGNORECASE),
    re.compile(r"\[\s*url\s*\]", re.IGNORECASE),
    re.compile(r"<\s*a\b", re.IGNORECASE),
)

#: 连续数字：允许中间夹一个常见分隔符，所以 ``123 456 789`` 与 ``123-456-789`` 都算一条。
_LONG_NUMBER_RE = re.compile(
    r"\d(?:[\s\-_.·]*\d){" + str(FORUM_LONG_NUMBER_MIN_DIGITS - 1) + r",}"
)

#: 违规词匹配时忽略的分隔符（含常见规避写法与全角形式）。
_WORD_SEPARATORS = "*.*-_ \u3000·|/\\、,，。.。"


@dataclass(frozen=True, slots=True)
class ForumViolation:
    """一条违规判定：``reason`` 是稳定的原因码，``detail`` 是命中的原文片段。

    ``category`` 是更细的一层（``FORUM_CATEGORY_*``），语音提示按它挑音频：原因码只有
    链接 / 长数字 / 违规词三种，违规词里「骂人」和「广告」却得用两套话术。
    """

    reason: str
    detail: str
    category: str = ""


def normalize_for_matching(text) -> str:
    """归一化用于违规词比较的文本：NFKC 折叠全角、去分隔符、转小写。

    ``加*微*信``、``加 微 信``、``ＡＤＤ微信`` 折叠后都是 ``加微信``。
    """
    folded = unicodedata.normalize("NFKC", str(text or "")).lower()
    for separator in _WORD_SEPARATORS:
        folded = folded.replace(separator, "")
    return folded


def find_link(text) -> str | None:
    """返回第一处命中的链接片段；没有则返回 None。"""
    raw = unicodedata.normalize("NFKC", str(text or ""))
    for pattern in _LINK_PATTERNS:
        match = pattern.search(raw)
        if match is not None:
            return match.group(0).strip()
    return None


def find_long_number(text) -> str | None:
    """返回第一处六位及以上的数字串；没有则返回 None。"""
    raw = unicodedata.normalize("NFKC", str(text or ""))
    match = _LONG_NUMBER_RE.search(raw)
    return match.group(0).strip() if match is not None else None


def find_banned_word(text) -> str | None:
    """返回第一处命中的违规词（返回词表里的原词，不是用户写的变体）。"""
    folded = normalize_for_matching(text)
    best_index: int | None = None
    best_word = ""
    for word in FORUM_BANNED_WORDS:
        index = folded.find(word)
        if index < 0:
            continue
        if best_index is None or index < best_index or (
            index == best_index and len(word) > len(best_word)
        ):
            best_index = index
            best_word = word
    return best_word or None


def category_for_word(word: str) -> str:
    """某个违规词属于哪一组；词表里没有的词给通用类。"""
    target = str(word or "")
    for category, words in FORUM_BANNED_WORD_GROUPS:
        if target in words:
            return category
    return FORUM_CATEGORY_GENERIC


def check_content(text) -> ForumViolation | None:
    """按固定顺序检查一段留言；返回第一条违规，没有则返回 None。

    判定前先洗掉颜色令牌与排版令牌：``[color=#000000]`` 里的六位数字、``[size=999]``
    里的字号都会被长数字规则误伤，而令牌是渲染指令、不是留言内容。
    """
    raw = strip_layout_tokens(strip_color_tokens(text))
    link = find_link(raw)
    if link:
        return ForumViolation(FORUM_REASON_LINK, link, FORUM_CATEGORY_LINK)
    number = find_long_number(raw)
    if number:
        return ForumViolation(FORUM_REASON_LONG_NUMBER, number, FORUM_CATEGORY_NUMBER)
    word = find_banned_word(raw)
    if word:
        return ForumViolation(FORUM_REASON_BANNED_WORD, word, category_for_word(word))
    return None


def is_allowed(text) -> bool:
    """内容是否可以通过本地过滤。"""
    return check_content(text) is None


__all__ = [
    "FORUM_BANNED_WORD_GROUPS",
    "FORUM_BANNED_WORDS",
    "FORUM_CATEGORY_ABUSE",
    "FORUM_CATEGORY_GENERIC",
    "FORUM_CATEGORY_ILLEGAL",
    "FORUM_CATEGORY_LINK",
    "FORUM_CATEGORY_NUMBER",
    "FORUM_CATEGORY_PROMOTION",
    "FORUM_CATEGORY_TRAFFIC",
    "FORUM_LONG_NUMBER_MIN_DIGITS",
    "FORUM_REASON_BANNED_WORD",
    "FORUM_REASON_LINK",
    "FORUM_REASON_LONG_NUMBER",
    "ForumViolation",
    "category_for_word",
    "check_content",
    "find_banned_word",
    "find_link",
    "find_long_number",
    "is_allowed",
    "normalize_for_matching",
]
