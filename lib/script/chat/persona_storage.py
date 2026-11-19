"""User-owned persona file resolution and first-edit initialization."""

from __future__ import annotations

import os
from pathlib import Path

from config.user_storage_paths import get_user_persona_path


_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _resolve_persona_path(raw_path: str) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(str(raw_path or "").strip()))
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = _PROJECT_ROOT / candidate
    return candidate


def _configured_persona_paths() -> tuple[Path, ...]:
    import config.ollama_config as oc
    from config.config import BUBBLE_CONFIG, CHAT

    raw_paths = (
        str(getattr(oc, "PERSONA_FILE", "") or "").strip(),
        str(CHAT.get("persona_file", "") or "").strip(),
        str(BUBBLE_CONFIG.get("default_persona_file", "resc/persona.txt") or "").strip()
        or "resc/persona.txt",
    )
    paths: list[Path] = []
    for raw_path in raw_paths:
        if not raw_path:
            continue
        candidate = _resolve_persona_path(raw_path)
        if candidate not in paths:
            paths.append(candidate)
    return tuple(paths)


def persona_file_candidates() -> tuple[Path, ...]:
    """Return persona paths in runtime precedence order."""
    user_path = get_user_persona_path()
    return (user_path, *(
        path for path in _configured_persona_paths() if path != user_path
    ))


def resolve_persona_file_path() -> Path:
    """Resolve the first existing persona, preferring the user-owned file."""
    candidates = persona_file_candidates()
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[-1]


def ensure_user_persona_file() -> Path:
    """Create the editable user persona once, seeded from the current source."""
    user_path = get_user_persona_path()
    if user_path.exists():
        if user_path.is_dir():
            raise IsADirectoryError(f"人格路径是文件夹，不是 txt 文件：{user_path}")
        return user_path

    content = ""
    for candidate in persona_file_candidates()[1:]:
        if not candidate.is_file():
            continue
        content = candidate.read_text(encoding="utf-8-sig")
        break

    user_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with user_path.open("x", encoding="utf-8", newline="") as handle:
            handle.write(content)
    except FileExistsError:
        pass
    return user_path


PERSONA_CONTRACT_RELATIVE_PATH = "resc/persona_contract.txt"

#: 人格词里由项目维护、设置页不展示的段落标题。用户人格文件若还留着旧版本里的这些段落，
#: 运行时先切掉，再由 `load_persona_contract()` 统一追加，避免同一段出现两次。
MANAGED_PERSONA_SECTION_HEADERS = (
    "[工具调用]",
    "[工具使用规则]",
    "[使用musiclist工具",
    "[执行前自检]",
)


def resolve_persona_contract_path() -> Path:
    return _PROJECT_ROOT / PERSONA_CONTRACT_RELATIVE_PATH


def load_persona_contract() -> str:
    """读取项目维护的人格词协议段；文件缺失时返回空串，不阻塞对话。"""
    try:
        return resolve_persona_contract_path().read_text(encoding="utf-8-sig").strip()
    except OSError:
        return ""


def strip_managed_persona_sections(text: str) -> str:
    """切掉人格词里由项目维护的尾部段落，只留用户可编辑的人设部分。"""
    kept: list[str] = []
    for line in str(text or "").splitlines():
        if line.strip().startswith(MANAGED_PERSONA_SECTION_HEADERS):
            break
        kept.append(line)
    return "\n".join(kept).rstrip()


__all__ = [
    "MANAGED_PERSONA_SECTION_HEADERS",
    "PERSONA_CONTRACT_RELATIVE_PATH",
    "ensure_user_persona_file",
    "load_persona_contract",
    "persona_file_candidates",
    "resolve_persona_contract_path",
    "resolve_persona_file_path",
    "strip_managed_persona_sections",
]
