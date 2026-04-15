"""版本信息与 Github 仓库声明。"""

from __future__ import annotations

# 当前应用版本（与 README / 发布标签保持一致）
APP_VERSION = "LTS1.0.5pre3"
# 版本发布日期（ISO 日期字符串，便于比较）
APP_RELEASE_DATE = "2026-04-15"

# 资源包版本：初始与 APP 保持一致，后续若有独立资源包可单独更新
RESOURCE_VERSION = APP_VERSION
RESOURCE_RELEASE_DATE = APP_RELEASE_DATE

# Github 仓库（owner/repo），供自动更新与链接展示复用
GITHUB_REPO = "MARK42IRPC/FlyingSnowVelvet-Aemeath"


def as_dict() -> dict[str, str]:
    """返回便于序列化的版本信息字典。"""
    return {
        "app_version": APP_VERSION,
        "app_release_date": APP_RELEASE_DATE,
        "resource_version": RESOURCE_VERSION,
        "resource_release_date": RESOURCE_RELEASE_DATE,
        "repo": GITHUB_REPO,
    }
