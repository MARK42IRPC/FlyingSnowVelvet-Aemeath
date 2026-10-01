"""Windows taskbar AppUserModelID so python.exe-hosted windows show the project icon."""

from __future__ import annotations

import os

_APP_USER_MODEL_ID = "FlyingSnowDeskPet.UnifiedWorkbench.1"


def set_windows_app_user_model_id() -> None:
    """Associate the process with a stable app id on Windows taskbar."""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            _APP_USER_MODEL_ID
        )
    except Exception:
        pass


__all__ = ["set_windows_app_user_model_id"]
