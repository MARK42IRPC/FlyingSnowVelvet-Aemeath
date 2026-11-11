"""AI 设置面板「贡献名单」的纯解析逻辑（无 Qt）。

事实源是 `doc/贡献名单和主播的狗盆/开发贡献.txt`。文件按「段落」组织，
段头是一行 `标题:`（半角或全角冒号均可），段内每行 `===` 开头是条目明细：

    贡献:配音-猫咪
    ===千咲，达妮娅，莫宁
    ===https://space.bilibili.com/1838261330

    ai平台技术支持:
    ===claude-CODE        (核心架构)
    ===deepseek深度求索    (日常帮助)

解析规则：
- `贡献:` 段产出人物条目（角色 + 名字 + URL），进入「贡献名单」。
- `ai平台技术支持:` 段产出平台条目（名字 + 括号内的用途），进入「AI 平台」。
- 其他段落（`音乐模块支持:`、`【声明】` 等）不进入任何名单。
- 表格形式的多行条目（一行多个 `名字<TAB>URL`）逐行拆分。

所有条目都来自文件本身，本模块不再内置任何硬编码名单；要增减条目请改 txt。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

_logger = logging.getLogger(__name__)

CONTRIBUTION_SECTION = "贡献"
AI_PLATFORM_SECTION = "ai平台"

# 段头里出现这些关键字说明该段不属于名单（声明、版权说明等）
_IGNORED_SECTION_KEYWORDS = ("声明", "版权", "音乐模块")

# 角色行里只作为声明、不参与角色/名字拆分的片段
_CONTRIBUTION_IGNORED_TITLE_PARTS = frozenset({"保留所有权利"})


def project_root() -> Path:
    """仓库根目录。本模块位于 `lib/core/ai_settings/`，往上三级即根。"""
    return Path(__file__).resolve().parents[3]


def contribution_list_path(root: Path | None = None) -> Path:
    """贡献名单事实源路径。``root`` 供调用方覆盖（发行包自检会把文档树暂存到别处）。"""
    base = Path(root) if root is not None else project_root()
    return base / "doc" / "贡献名单和主播的狗盆" / "开发贡献.txt"


def sponsor_author_image_path(root: Path | None = None) -> Path:
    """赞助/支持入口的图片路径。"""
    base = Path(root) if root is not None else project_root()
    return (
        base
        / "doc"
        / "贡献名单和主播的狗盆"
        / "如果想给作者买鸡腿饭的话"
        / "喵-感谢支持喵-欢迎工单喵.jpg"
    )


def read_text_with_fallback(path: Path) -> str:
    """按常见中文编码逐个尝试读取，最后退回 utf-8 + ignore。"""
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "cp936"):
        try:
            return path.read_text(encoding=encoding)
        except Exception:
            pass
    return path.read_text(encoding="utf-8", errors="ignore")


def extract_first_url(text: str) -> str:
    match = re.search(r"https?://\S+", str(text or ""))
    return match.group(0).strip() if match else ""


def normalize_text(text: str) -> str:
    """压缩空白并去掉首尾与正文中的 `-` `=` `:` 分隔标记。"""
    value = str(text or "").replace("　", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = value.strip()
    value = re.sub(r"^[\s\-=:：]+|[\s\-=:：]+$", "", value)
    return value


def _strip_ignored_parts(parts: list[str]) -> list[str]:
    return [part for part in parts if part and part not in _CONTRIBUTION_IGNORED_TITLE_PARTS]


def split_section_header(line: str) -> tuple[str, str] | None:
    """把 `标题:内容` 拆成 (标题, 内容)；不是段头时返回 None。

    同时接受半角 `:` 与全角 `：`——源文件两种都用了。
    `===` 开头是条目明细而不是段头，必须在任何规整之前排除，否则
    `===https://...` 会被 `https` 当成标题吃掉。
    """
    if str(line or "").lstrip().startswith("==="):
        return None
    stripped = normalize_text(line)
    match = re.match(r"^(?P<title>[^\s:：]{1,40})\s*[:：]\s*(?P<body>.*)$", stripped)
    if match is None:
        return None
    return match.group("title"), match.group("body")


def _section_kind(title: str) -> str | None:
    """把段头标题归类：贡献 / ai平台 / 其它（返回 None 表示不进名单）。"""
    text = title.strip().lower()
    if any(keyword in text for keyword in _IGNORED_SECTION_KEYWORDS):
        return None
    if text.startswith(AI_PLATFORM_SECTION):
        return AI_PLATFORM_SECTION
    if text.startswith(CONTRIBUTION_SECTION):
        return CONTRIBUTION_SECTION
    return None


def _parse_contribution_header(body: str) -> tuple[str, str]:
    """`配音-猫咪` -> ('配音', '猫咪')。

    没有 `-` 分隔时返回 `("", 整段)`——角色留空，名字取整段。
    「保留所有权利」这类提示语不参与拆分。
    """
    parts = _strip_ignored_parts([normalize_text(part) for part in normalize_text(body).split("-")])
    if not parts:
        return "", ""
    if len(parts) < 2:
        # 没有分隔符时无法区分角色与名字：整段当角色，名字交给段内 `===` 行
        return parts[0], ""
    role = "-".join(parts[:-1])
    name = parts[-1]
    return role, name


def _parse_platform_detail(detail: str) -> tuple[str, str]:
    """`claude-CODE        (核心架构)` -> ('claude-CODE', '核心架构')。"""
    text = normalize_text(detail)
    match = re.match(r"^(?P<name>.*?)\s*[（(]\s*(?P<usage>[^)）]*)\s*[)）]\s*$", text)
    if match:
        return normalize_text(match.group("name")), normalize_text(match.group("usage"))
    return text, ""


def _split_table_row(detail: str) -> list[tuple[str, str]]:
    """把 `名字<TAB>URL` 这类一行多条的表格式条目拆开。"""
    text = str(detail or "")
    if "\t" not in text:
        return []
    cells = [normalize_text(cell) for cell in text.split("\t")]
    if len(cells) < 2:
        return []
    url = extract_first_url(text)
    if not url:
        return []
    name = normalize_text(text.split(url, 1)[0])
    return [(name, url)] if name else []


def parse_contribution_sections(text: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """解析整份文件，返回 (贡献名单, AI 平台名单)。"""
    contributors: list[dict[str, str]] = []
    platforms: list[dict[str, str]] = []

    kind: str | None = None
    role = ""
    default_name = ""
    url = ""
    emitted = False
    pending_vendor = ""

    def flush() -> None:
        """段结束时：只有没被任何 `===` 条目消费过的角色才落成一条。

        段头 `贡献:配音-猫咪` 只提供「角色 + 默认名字」；真正的条目由带 URL 的
        `===` 行产出。若整段没有任何 `===` 条目（或条目都没 URL），
        才用段头名字补一条，避免同一个贡献者出现两遍。
        """
        nonlocal role, default_name, url, emitted
        if kind == CONTRIBUTION_SECTION and not emitted and (default_name or url):
            contributors.append({
                "name": default_name or role,
                "role": role or "贡献者",
                "url": url,
            })
        role = ""
        default_name = ""
        url = ""
        emitted = False

    for raw_line in str(text or "").splitlines():
        # 表格条目（`名字<TAB>URL`）依赖制表符，必须在 normalize 之前处理
        if raw_line.lstrip().startswith("===") and "\t" in raw_line:
            raw_detail = raw_line.lstrip()[3:]
            if kind == CONTRIBUTION_SECTION:
                for name, row_url in _split_table_row(raw_detail):
                    contributors.append({"name": name, "role": role or "", "url": row_url})
                continue

        line = normalize_text(raw_line)
        if not line:
            continue

        header = split_section_header(raw_line)
        if header is not None:
            flush()
            title, body = header
            pending_vendor = ""
            kind = _section_kind(title)
            if kind == CONTRIBUTION_SECTION:
                # 段头正文（`贡献:配音-猫咪`）本身就是条目；空正文时等下方 === 行
                if body:
                    role, default_name = _parse_contribution_header(body)
            elif kind == AI_PLATFORM_SECTION:
                # 段头正文是平台/公司名（`AI平台技术支持:Anthropic`），
                # 段内 `===` 行是该平台下的产品线；正文为空时用段内行自身名字。
                pending_vendor = normalize_text(body)
            continue

        if not raw_line.lstrip().startswith("==="):
            continue
        detail = normalize_text(raw_line.lstrip()[3:])
        if not detail:
            continue

        if kind == AI_PLATFORM_SECTION:
            name, usage = _parse_platform_detail(detail)
            if name:
                platforms.append({
                    "name": name,
                    "usage": usage,
                    "vendor": pending_vendor,
                })
            continue

        if kind != CONTRIBUTION_SECTION:
            continue

        detail_url = extract_first_url(detail)
        if detail_url:
            prefix = normalize_text(detail.split(detail_url, 1)[0])
            name = prefix or default_name or role
            contributors.append({"name": name, "role": role, "url": detail_url})
            url = url or detail_url
            emitted = True
            continue
        # 不带 URL 的短文本作为名字（如 `粉丝群的大家`）：
        # 只在还没有任何名字时采用，避免覆盖角色行给出的默认名。
        if not default_name and len(detail) <= 20:
            default_name = detail

    flush()
    return contributors, platforms


def parse_contribution_records(text: str) -> list[dict[str, str]]:
    """只返回贡献名单部分（保持旧接口）。"""
    return parse_contribution_sections(text)[0]


def load_contribution_records(root: Path | None = None) -> list[dict[str, str]]:
    """读取贡献名单。条目完全来自 txt，不再做任何硬编码覆盖。"""
    path = contribution_list_path(root)
    if not path.exists():
        _logger.warning("贡献名单文件不存在: %s", path)
        return []
    try:
        return parse_contribution_records(read_text_with_fallback(path))
    except Exception as exc:
        _logger.warning("读取贡献名单失败: %s", exc)
        return []


def load_ai_platform_records(root: Path | None = None) -> list[dict[str, str]]:
    """读取「ai平台技术支持」段。"""
    path = contribution_list_path(root)
    if not path.exists():
        _logger.warning("贡献名单文件不存在: %s", path)
        return []
    try:
        return parse_contribution_sections(read_text_with_fallback(path))[1]
    except Exception as exc:
        _logger.warning("读取 AI 平台名单失败: %s", exc)
        return []
