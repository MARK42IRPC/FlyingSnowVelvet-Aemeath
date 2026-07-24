"""Shared settings-page schema and layout primitives."""

from .page_layout import SettingsFormLayout, SettingsPageScaffold, create_settings_form
from .schema import GENERAL_CONFIG_CATEGORIES

__all__ = [
    "GENERAL_CONFIG_CATEGORIES",
    "SettingsFormLayout",
    "SettingsPageScaffold",
    "create_settings_form",
]
