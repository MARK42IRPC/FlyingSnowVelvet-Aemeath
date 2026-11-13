from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from config.user_settings import (
    compact_overrides,
    load_section,
    migrate_section_once,
    save_section,
)


class UserSettingsSparseTests(unittest.TestCase):
    def test_compact_overrides_recurses_and_tolerates_float_noise(self):
        defaults = {
            "volume": 0.14,
            "nested": {"enabled": True, "scale": 1.0},
            "items": [1, 2],
        }
        values = {
            "volume": 0.14000000001,
            "nested": {"enabled": False, "scale": 1.0},
            "items": [1, 2],
        }
        self.assertEqual(compact_overrides(values, defaults), {
            "nested": {"enabled": False},
        })

    def test_save_section_keeps_only_non_default_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "settings.json"
            defaults = {"volume": 0.14, "muted": False}
            sparse = save_section(
                "audio",
                {"volume": 0.6, "muted": False},
                defaults,
                path=path,
            )
            self.assertEqual(sparse, {"volume": 0.6})
            self.assertEqual(load_section("audio", defaults, path=path), {
                "volume": 0.6,
                "muted": False,
            })

            save_section("audio", defaults, defaults, path=path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("audio", payload["overrides"])

    def test_migration_runs_once_even_when_legacy_value_is_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "settings.json"
            defaults = {"scale": 1.0}
            self.assertTrue(migrate_section_once(
                "legacy_scale",
                "ui",
                {"scale": 1.0},
                defaults,
                path=path,
            ))
            self.assertFalse(migrate_section_once(
                "legacy_scale",
                "ui",
                {"scale": 1.5},
                defaults,
                path=path,
            ))
            self.assertEqual(load_section("ui", defaults, path=path), defaults)

    def test_invalid_override_type_falls_back_per_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "settings.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "overrides": {"audio": {"volume": "loud", "muted": True}},
                "migrations": {},
            }), encoding="utf-8")
            self.assertEqual(
                load_section("audio", {"volume": 0.14, "muted": False}, path=path),
                {"volume": 0.14, "muted": True},
            )


if __name__ == "__main__":
    unittest.main()
