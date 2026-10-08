"""AI 设置面板「贡献名单」的纯解析逻辑（无 Qt）。

从 `ai_settings_panel.py` 拆出：这一族原本和面板控件挤在同一个 5000+ 行文件里，但
它不碰 Qt、不碰 UI 装配，只做"读文件 -> 解析 -> 过滤/插入手工条目 -> 返回记录列表"。
拆出后主文件只保留渲染这些记录的控件代码。

事实源仍是 `doc/贡献名单和主播的狗盆/开发贡献.txt`：`贡献:` 开头是角色行，`===` 开头是
条目明细，明细里带 URL 的直接成条，不带 URL 的短文本作为下一条的兜底名字。手工条目
（`_MANUAL_CONTRIBUTION_RECORDS`）在解析后按 URL 去重并按 `insert_at` 位置插回。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

_logger = logging.getLogger(__name__)


def project_root() -> Path:
    """仓库根目录。本模块位于 `lib/script/ui/`，往上三级即根。"""
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


_CONTRIBUTION_IGNORED_TITLE_PARTS = {"保留所有权利"}
_CONTRIBUTION_HIDDEN_ROLES = {"安装教程指引"}
_MANUAL_CONTRIBUTION_RECORDS = [
    {
        "insert_at": 1,
        "name": "猫咪",
        "role": "配音（千咲，达妮娅，莫宁）",
        "url": "https://space.bilibili.com/1838261330",
    },
    {
        "insert_at": 2,
        "name": "TDSI服务器",
        "role": "服务器支持",
        "url": "https://systemtemp.pages.dev/",
    },
    {
        "insert_at": 999,
        "name": "鸣潮",
        "role": "素材/形象来源",
        "url": "https://mc.kurogames.com/",
    },
]


def extract_first_url(text: str) -> str:
    match = re.search(r"https?://\S+", str(text or ""))
    return match.group(0).strip() if match else ""


def normalize_contribution_name(text: str) -> str:
    value = str(text or "").strip()
    value = re.sub(r"\s+", " ", value)
    value = value.strip("-=:： \t")
    return value


def guess_contribution_fallback_name(text: str) -> str:
    candidate = normalize_contribution_name(text)
    if not candidate:
        return ""
    if len(candidate) > 20:
        return ""
    blocked_tokens = ("感谢", "谢谢", "喜欢", "更新", "测试版", "版权", "侵权", "删除")
    if any(token in candidate for token in blocked_tokens):
        return ""
    return candidate


def split_contribution_header(header: str) -> tuple[str, str]:
    text = normalize_contribution_name(header)
    parts = [
        normalize_contribution_name(part)
        for part in text.split("-")
        if normalize_contribution_name(part)
    ]
    if len(parts) < 2:
        return text, ""

    picked_index = -1
    for index in range(len(parts) - 1, -1, -1):
        part = parts[index]
        if part in _CONTRIBUTION_IGNORED_TITLE_PARTS:
            continue
        if index == 0:
            continue
        picked_index = index
        break

    if picked_index < 0:
        return text, ""

    name = parts[picked_index]
    role_parts = [
        part
        for index, part in enumerate(parts)
        if index != picked_index and part not in _CONTRIBUTION_IGNORED_TITLE_PARTS
    ]
    role = "-".join(role_parts).strip("- ") or text
    return role, name


def parse_contribution_records(text: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    current_role = ""
    current_default_name = ""
    current_fallback_name = ""
    current_has_record = False

    def flush_pending() -> None:
        nonlocal current_role, current_default_name, current_fallback_name, current_has_record
        if current_role and not current_has_record:
            name = current_fallback_name or current_default_name
            if name:
                records.append({
                    "name": name,
                    "role": current_role,
                    "url": "",
                })
        current_role = ""
        current_default_name = ""
        current_fallback_name = ""
        current_has_record = False

    for raw_line in str(text or "").splitlines():
        line = str(raw_line or "").strip()
        if not line:
            continue
        if line.startswith("贡献:"):
            flush_pending()
            current_role, current_default_name = split_contribution_header(line[3:].strip())
            current_fallback_name = current_default_name
            continue
        if not current_role or not line.startswith("==="):
            continue

        detail = normalize_contribution_name(line[3:].strip())
        if not detail:
            continue
        url = extract_first_url(detail)
        if url:
            prefix = normalize_contribution_name(detail.split(url, 1)[0])
            name = prefix or current_default_name or current_fallback_name or "未命名贡献者"
            records.append({
                "name": name,
                "role": current_role,
                "url": url,
            })
            current_has_record = True
            continue

        fallback_name = guess_contribution_fallback_name(detail)
        if fallback_name:
            current_fallback_name = fallback_name

    flush_pending()
    return records


def load_contribution_records(root: Path | None = None) -> list[dict[str, str]]:
    """读取并整理贡献名单：解析 -> 去掉隐藏角色 -> 插回手工条目（按 URL 去重）。"""
    path = contribution_list_path(root)
    if not path.exists():
        records = []
    else:
        try:
            records = parse_contribution_records(read_text_with_fallback(path))
        except Exception as exc:
            _logger.warning("读取贡献名单失败: %s", exc)
            records = []
    try:
        filtered_records: list[dict[str, str]] = []
        for record in records:
            role = str(record.get("role") or "").strip()
            if role in _CONTRIBUTION_HIDDEN_ROLES:
                continue
            filtered_records.append(record)

        for manual in _MANUAL_CONTRIBUTION_RECORDS:
            manual_url = str(manual.get("url") or "").strip()
            if not manual_url:
                continue
            filtered_records = [
                record for record in filtered_records
                if str(record.get("url") or "").strip() != manual_url
            ]
            insert_at = int(manual.get("insert_at", len(filtered_records)))
            insert_at = max(0, min(insert_at, len(filtered_records)))
            filtered_records.insert(insert_at, {
                "name": str(manual.get("name") or "未命名贡献者").strip(),
                "role": str(manual.get("role") or "贡献者").strip(),
                "url": manual_url,
            })

        return filtered_records
    except Exception as exc:
        _logger.warning("整理贡献名单失败: %s", exc)
        return []
