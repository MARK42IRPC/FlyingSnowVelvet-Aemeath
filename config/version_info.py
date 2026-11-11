"""版本信息与 Github 仓库声明。"""

from __future__ import annotations

import re

# 当前应用版本（与 README / 发布标签保持一致）
APP_VERSION = "LTS1.0.7pre7"
# 版本发布日期（ISO 日期字符串，便于比较）
APP_RELEASE_DATE = "2026-10-28"

# 资源包版本：初始与 APP 保持一致，后续若有独立资源包可单独更新
RESOURCE_VERSION = APP_VERSION
RESOURCE_RELEASE_DATE = APP_RELEASE_DATE

# Github 仓库（owner/repo），供自动更新与链接展示复用
GITHUB_REPO = "MARK42IRPC/FlyingSnowVelvet-Aemeath"

# 离线安装器与 ONNX 语音包共用发布仓库；更新器只从这两个仓库读取。
VOICE_PACKAGE_HUGGINGFACE_REPO = "Mark42IRP/Aemeath_onnx_GSV_model"
VOICE_PACKAGE_MODELSCOPE_REPO = "Mark42IRPC/GSV_onnx_Aemeath_Pack"
OFFLINE_UPDATE_FORMAT = "fsv-offline-installer-v1"
OFFLINE_UPDATE_METADATA_PATH = "updates/latest.json"


_VERSION_RE = re.compile(
    r"^(?:lts|v)?\s*(?P<release>\d+(?:\.\d+)*)"
    r"(?:[-\s]?(?P<pre>[A-Za-z][A-Za-z0-9.]*))?$",
    re.IGNORECASE,
)


def _prerelease_key(tag: str) -> tuple[tuple[int, int, str], ...]:
    """预发布标记的稳定排序键：数字段按数值、字母段按字典序。"""
    chunks = re.findall(r"\d+|[A-Za-z]+", tag or "")
    return tuple(
        (1, int(chunk), "") if chunk.isdigit() else (0, 0, chunk.lower())
        for chunk in chunks
    )


def version_key(value: str) -> tuple[tuple[int, ...], int, tuple[tuple[int, int, str], ...]] | None:
    """把 `LTS1.0.7pre7` / `v1.0.7` / `1.0.7-rc1` 解析成可比较的键；无法解析时返回 None。"""
    match = _VERSION_RE.match(str(value or "").strip())
    if match is None:
        return None
    release = tuple(int(part) for part in match.group("release").split("."))
    padded = tuple(release) + (0,) * (4 - len(release)) if len(release) < 4 else release
    prerelease = match.group("pre") or ""
    return padded, 0 if prerelease else 1, _prerelease_key(prerelease)


def is_newer_version(candidate: str, current: str = APP_VERSION) -> bool:
    """`candidate` 是否严格新于 `current`；任一侧无法解析时一律返回 False。"""
    candidate_key = version_key(candidate)
    current_key = version_key(current)
    if candidate_key is None or current_key is None:
        return False
    return candidate_key > current_key


def as_dict() -> dict[str, str]:
    """返回便于序列化的版本信息字典。"""
    return {
        "app_version": APP_VERSION,
        "app_release_date": APP_RELEASE_DATE,
        "resource_version": RESOURCE_VERSION,
        "resource_release_date": RESOURCE_RELEASE_DATE,
        "repo": GITHUB_REPO,
        "voice_package_huggingface_repo": VOICE_PACKAGE_HUGGINGFACE_REPO,
        "voice_package_modelscope_repo": VOICE_PACKAGE_MODELSCOPE_REPO,
    }

