"""Global clickthrough state shared by runtime windows."""

_clickthrough_enabled: bool | None = None


def set_clickthrough_enabled(enabled: bool) -> None:
    """Persist clickthrough state for windows created later in this process."""
    global _clickthrough_enabled
    _clickthrough_enabled = bool(enabled)


def is_clickthrough_enabled(default: bool = False) -> bool:
    """Read current state, using ``default`` before the first explicit update."""
    if _clickthrough_enabled is None:
        return bool(default)
    return _clickthrough_enabled
