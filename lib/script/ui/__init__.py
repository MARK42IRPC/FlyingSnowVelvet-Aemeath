"""Qt UI package with lazy compatibility exports.

Importing a specific submodule must not eagerly load every QWidget implementation.
"""
from __future__ import annotations

from importlib import import_module


_EXPORT_MODULES = {
    'CloseButton': 'lib.script.ui.close_button',
    'ClickThroughButton': 'lib.script.ui.clickthrough_button',
    'RestoreButton': 'lib.script.ui.restore_button',
    'CommandDialog': 'lib.script.ui.command_dialog',
    'Bubble': 'lib.script.ui.bubble',
    'LaunchWutheringWavesButton': 'lib.script.ui.launch_wuwa_button',
    'ChatModeButton': 'lib.script.ui.chat_mode_button',
    'TrayContextMenu': 'lib.script.ui.tray_menu',
    'SpeakerControlButton': 'lib.script.ui.speaker_control_buttons',
    'PlayPauseButton': 'lib.script.ui.speaker_control_buttons',
    'NextTrackButton': 'lib.script.ui.speaker_control_buttons',
    'MusicLoginButton': 'lib.script.ui.speaker_control_buttons',
    'LikedQueueButton': 'lib.script.ui.speaker_control_buttons',
    'SpeakerControlButtons': 'lib.script.ui.speaker_control_buttons',
}

__all__ = [
    'CloseButton',
    'ClickThroughButton',
    'RestoreButton',
    'CommandDialog',
    'Bubble',
    'LaunchWutheringWavesButton',
    'ChatModeButton',
    'TrayContextMenu',
    'SpeakerControlButton',
    'PlayPauseButton',
    'NextTrackButton',
    'MusicLoginButton',
    'LikedQueueButton',
    'SpeakerControlButtons',
]


def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
