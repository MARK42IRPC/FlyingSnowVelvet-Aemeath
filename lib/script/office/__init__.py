"""Office-mode coding agent services."""

from .contracts import InteractionMode
from .mode import (
    cleanup_interaction_mode_service,
    get_interaction_mode_service,
)
from .service import cleanup_office_service, get_office_service

__all__ = (
    "InteractionMode",
    "get_interaction_mode_service",
    "cleanup_interaction_mode_service",
    "get_office_service",
    "cleanup_office_service",
)
