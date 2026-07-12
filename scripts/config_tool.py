"""Inspect, migrate, and compact sparse user settings."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.user_settings import get_section_overrides, load_section, save_section
from config.user_storage_paths import get_user_settings_path


def _known_defaults() -> dict[str, dict]:
    import config.ollama_config as oc
    from config.general_user_settings import get_general_setting_defaults
    from config.music.volume_config import get_default_volume

    return {
        "ai": oc.get_ai_setting_defaults(),
        "audio": {"volume": get_default_volume()},
        "general": get_general_setting_defaults(),
        "ui": {"scale": 1.0},
    }


def _effective_settings() -> dict[str, dict]:
    return {
        section: load_section(section, defaults)
        for section, defaults in _known_defaults().items()
    }


def command_check() -> int:
    path = get_user_settings_path()
    if not path.exists():
        print(f"OK: settings file does not exist yet: {path}")
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ERROR: invalid settings file: {exc}")
        return 1
    if not isinstance(payload, dict) or not isinstance(payload.get("overrides", {}), dict):
        print("ERROR: settings root or overrides is not an object")
        return 1
    for section, defaults in _known_defaults().items():
        effective = load_section(section, defaults)
        print(f"OK: {section}: {len(get_section_overrides(section))} sparse override(s), {len(effective)} effective key(s)")
    return 0


def command_compact() -> int:
    for section, defaults in _known_defaults().items():
        effective = load_section(section, defaults)
        sparse = save_section(section, effective, defaults)
        print(f"COMPACTED: {section}: {len(sparse)} override(s)")
    print(get_user_settings_path())
    return 0


def command_migrate() -> int:
    import config.ollama_config  # noqa: F401
    from config.music.volume_config import get_volume_config
    from config.user_scale_config import get_user_scale_config

    get_user_scale_config()
    get_volume_config()
    print(f"MIGRATED: {get_user_settings_path()}")
    return command_compact()


def command_effective() -> int:
    print(json.dumps(_effective_settings(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "compact", "migrate", "effective"))
    args = parser.parse_args()
    return {
        "check": command_check,
        "compact": command_compact,
        "migrate": command_migrate,
        "effective": command_effective,
    }[args.command]()


if __name__ == "__main__":
    raise SystemExit(main())
