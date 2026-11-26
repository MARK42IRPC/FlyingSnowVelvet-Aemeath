"""User-owned persona file resolution and first-edit initialization."""

from __future__ import annotations

import os
from datetime import datetime
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
    "[输出格式]",
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


def default_persona_text() -> str:
    """内置默认人设段（不含项目维护的工具协议段）。"""
    for candidate in persona_file_candidates()[1:]:
        if not candidate.is_file():
            continue
        return candidate.read_text(encoding="utf-8-sig")
    return ""


def _next_backup_path(user_path: Path, suffix: str) -> Path:
    """同秒多次重置也不覆盖旧备份：`persona_backup_<秒>.txt` 占用时追加序号。"""
    backup_path = user_path.with_name(f"persona_backup_{suffix}.txt")
    index = 2
    while backup_path.exists():
        backup_path = user_path.with_name(f"persona_backup_{suffix}_{index}.txt")
        index += 1
    return backup_path


def reset_user_persona_file(*, stamp: str | None = None) -> tuple[Path, Path | None]:
    """把用户人格文件恢复成内置默认人设，返回 (用户文件, 备份文件)。

    先把默认人设写进同目录临时文件，再把旧文件整份备份为 `persona_backup_<时间戳>.txt`，
    最后原子替换成默认人设；格式与工具协议段由 `load_persona_contract()` 在发送时拼接，不写进用户文件。
    """
    user_path = get_user_persona_path()
    if user_path.is_dir():
        raise IsADirectoryError(f"人格路径是文件夹，不是 txt 文件：{user_path}")
    content = default_persona_text()
    if not content.strip():
        raise FileNotFoundError("没有可用的内置默认人格模板")

    user_path.parent.mkdir(parents=True, exist_ok=True)
    # 先把默认人设写进临时文件，再备份旧文件并原子替换：
    # 中途失败时旧人格仍在原位，不会出现「备份已挪走、新文件没写成」的空窗。
    temp_path = user_path.with_name(f".{user_path.name}.{os.getpid()}.tmp")
    backup_path: Path | None = None
    try:
        temp_path.write_text(content, encoding="utf-8", newline="")
        if user_path.is_file():
            suffix = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = _next_backup_path(user_path, suffix)
            user_path.replace(backup_path)
        temp_path.replace(user_path)
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass
    return user_path, backup_path


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
    "default_persona_text",
    "ensure_user_persona_file",
    "load_persona_contract",
    "persona_file_candidates",
    "reset_user_persona_file",
    "resolve_persona_contract_path",
    "resolve_persona_file_path",
    "strip_managed_persona_sections",
]
