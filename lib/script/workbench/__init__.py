"""Unified workbench infrastructure."""

from .page_registry import WorkbenchPageRegistry, WorkbenchPageSpec, default_page_spec

__all__ = [
    "WorkbenchPageRegistry",
    "WorkbenchPageSpec",
    "default_page_spec",
]
