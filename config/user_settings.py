"""Versioned sparse user-setting overrides.

Program defaults remain in ``config`` modules. This store only persists values
that differ from those defaults.
"""

from __future__ import annotations

import json
import math
import os
import threading
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from config.user_storage_paths import get_user_settings_path
from lib.core.logger import get_logger

_logger = get_logger(__name__)

SCHEMA_VERSION = 1
_LOCK = threading.RLock()
_MISSING = object()


def _empty_document() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "overrides": {},
        "migrations": {},
    }


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def values_equal(left: object, right: object) -> bool:
    if _is_number(left) and _is_number(right):
        return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-9)
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(values_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(values_equal(a, b) for a, b in zip(left, right))
    return left == right


def compact_overrides(values: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    for key, value in values.items():
        default = defaults.get(key, _MISSING)
        if default is _MISSING:
            compacted[key] = deepcopy(value)
            continue
        if isinstance(value, dict) and isinstance(default, dict):
            nested = compact_overrides(value, default)
            if nested:
                compacted[key] = nested
            continue
        if not values_equal(value, default):
            compacted[key] = deepcopy(value)
    return compacted


def _is_compatible(value: object, default: object) -> bool:
    if default is None:
        return True
    if _is_number(default):
        return _is_number(value)
    if isinstance(default, tuple):
        return isinstance(value, (list, tuple))
    return isinstance(value, type(default))


def _coerce_value(value: object, default: object) -> object:
    if isinstance(default, tuple) and isinstance(value, (list, tuple)):
        return tuple(value)
    return deepcopy(value)


def _merge_validated(defaults: dict[str, Any], overrides: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    merged = deepcopy(defaults)
    for key, value in overrides.items():
        if key not in defaults:
            continue
        name = f"{prefix}.{key}" if prefix else key
        default = defaults[key]
        if isinstance(default, dict):
            if isinstance(value, dict):
                merged[key] = _merge_validated(default, value, name)
            else:
                _logger.warning("[UserSettings] 忽略类型错误的配置 %s", name)
            continue
        if not _is_compatible(value, default):
            _logger.warning("[UserSettings] 忽略类型错误的配置 %s", name)
            continue
        merged[key] = _coerce_value(value, default)
    return merged


def _backup_corrupt(path: Path) -> None:
    if not path.exists():
        return
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.stem}.corrupt.{timestamp}{path.suffix}")
    try:
        path.replace(backup)
        _logger.warning("[UserSettings] 损坏配置已备份到 %s", backup)
    except OSError as exc:
        _logger.warning("[UserSettings] 无法备份损坏配置 %s: %s", path, exc)


def _read_document(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_document()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        _logger.warning("[UserSettings] 读取配置失败，使用空配置: %s", exc)
        _backup_corrupt(path)
        return _empty_document()
    if not isinstance(payload, dict):
        _backup_corrupt(path)
        return _empty_document()
    overrides = payload.get("overrides")
    migrations = payload.get("migrations")
    if not isinstance(overrides, dict):
        overrides = {}
    if not isinstance(migrations, dict):
        migrations = {}
    return {
        "schema_version": SCHEMA_VERSION,
        "overrides": overrides,
        "migrations": migrations,
    }


def _write_document(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        temp_path.write_text(text, encoding="utf-8")
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass


@contextmanager
def _file_lock(path: Path):
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load_section(section: str, defaults: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    settings_path = path or get_user_settings_path()
    with _LOCK:
        document = _read_document(settings_path)
        overrides = document["overrides"].get(section, {})
        if not isinstance(overrides, dict):
            overrides = {}
        return _merge_validated(defaults, overrides, section)


def get_section_overrides(section: str, *, path: Path | None = None) -> dict[str, Any]:
    settings_path = path or get_user_settings_path()
    with _LOCK:
        document = _read_document(settings_path)
        value = document["overrides"].get(section, {})
        return deepcopy(value) if isinstance(value, dict) else {}


def save_section(
    section: str,
    values: dict[str, Any],
    defaults: dict[str, Any],
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    settings_path = path or get_user_settings_path()
    with _LOCK:
        with _file_lock(settings_path):
            document = _read_document(settings_path)
            existing = document["overrides"].get(section, {})
            existing_dict = existing if isinstance(existing, dict) else {}
            preserved_unknown = {
                key: deepcopy(value)
                for key, value in existing_dict.items()
                if key not in defaults
            }
            sparse = compact_overrides(values, defaults)
            sparse.update(preserved_unknown)
            if sparse:
                document["overrides"][section] = sparse
            else:
                document["overrides"].pop(section, None)
            _write_document(settings_path, document)
            return deepcopy(sparse)


def migrate_section_once(
    migration_id: str,
    section: str,
    legacy_values: dict[str, Any],
    defaults: dict[str, Any],
    *,
    path: Path | None = None,
) -> bool:
    settings_path = path or get_user_settings_path()
    with _LOCK:
        with _file_lock(settings_path):
            document = _read_document(settings_path)
            if document["migrations"].get(migration_id):
                return False
            existing = document["overrides"].get(section)
            if not isinstance(existing, dict):
                sparse = compact_overrides(legacy_values, defaults)
                if sparse:
                    document["overrides"][section] = sparse
            document["migrations"][migration_id] = True
            _write_document(settings_path, document)
            return True
