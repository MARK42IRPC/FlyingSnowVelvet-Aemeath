"""gsvmove script 包公开接口。"""

from .service import (
    GsvmoveService,
    cleanup_gsvmove_service,
    get_gsvmove_service,
    is_gsvmove_launcher_available,
    is_voice_package_available,
)
from .package_manager import get_voice_package_status


def remove_voice_package(package_root):
    """Remove a managed package through the service's inference lock."""
    return get_gsvmove_service().remove_voice_package(package_root)

__all__ = [
    "GsvmoveService",
    "get_gsvmove_service",
    "cleanup_gsvmove_service",
    "is_gsvmove_launcher_available",
    "is_voice_package_available",
    "get_voice_package_status",
    "remove_voice_package",
]
