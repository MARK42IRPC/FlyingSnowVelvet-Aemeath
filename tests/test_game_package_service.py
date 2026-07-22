from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from lib.script.gemes.MAIN.game_packages import GamePackageService, qualify_game_extension_id
from lib.script.practical.manager import cleanup_particle_script_manager, get_particle_script_manager


class GamePackageServiceTests(unittest.TestCase):
    def _cleanup_service(self, service: GamePackageService) -> None:
        for game_id in list(service._registered_extensions.keys()):  # type: ignore[attr-defined]
            service._unregister_game_extensions(game_id)  # type: ignore[attr-defined]
        cleanup_particle_script_manager()

    def _write_package(
        self,
        package_root: Path,
        *,
        game_id: str,
        package_name: str,
        entry_module: str,
        entry_class: str,
        version: str = "1.0.0",
        particle_extensions: list[dict[str, str]] | None = None,
        entry_body: str | None = None,
    ) -> None:
        package_root.mkdir(parents=True, exist_ok=True)
        manifest = {
            "package_type": "game",
            "package_format_version": 1,
            "runtime_api_version": 1,
            "game_id": game_id,
            "name": package_name,
            "version": version,
            "summary": f"{package_name} summary",
            "entry_module": entry_module,
            "entry_class": entry_class,
            "official": True,
            "extensions": {
                "particles": list(particle_extensions or []),
                "effects": [],
            },
        }
        (package_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        entry_rel = Path(*entry_module.split("."))
        entry_path = package_root / "code" / f"{entry_rel}.py"
        entry_path.parent.mkdir(parents=True, exist_ok=True)
        entry_path.write_text(
            entry_body
            or (
                f"class {entry_class}:\n"
                "    def __init__(self, context):\n"
                "        self.context = context\n"
            ),
            encoding="utf-8",
        )

    def _write_package_zip(self, package_root: Path, zip_path: Path) -> None:
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(package_root.rglob("*"), key=lambda item: item.relative_to(package_root).as_posix()):
                if not path.is_file():
                    continue
                rel = path.relative_to(package_root)
                if "__pycache__" in rel.parts or path.suffix.lower() in {".pyc", ".pyo"}:
                    continue
                archive.write(path, rel.as_posix())

    def test_qualify_game_extension_id_uses_game_prefix(self) -> None:
        self.assertEqual(qualify_game_extension_id("lahai_tetris", "glow_burst"), "lahai_tetris.glow_burst")

    def test_official_lahai_package_bootstraps_and_exports(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"AEMEATH_DESK_PET_HOME": tmpdir}, clear=False):
            service = GamePackageService()
            self.addCleanup(self._cleanup_service, service)

            record = service.get_installed_game("lahai_tetris")
            self.assertIsNotNone(record)
            assert record is not None
            self.assertTrue(record.install_dir.exists())
            self.assertTrue((record.install_dir / "manifest.json").exists())
            self.assertTrue((record.install_dir / "extensions" / "particles" / "lahai_glow_burst_particle.py").exists())
            self.assertEqual(
                [spec.local_id for spec in record.manifest.particle_extensions],
                ["glow_burst", "preview_rise", "line_flash"],
            )

            export_path = Path(tmpdir) / "lahai_tetris-export.zip"
            service.export_game_zip("lahai_tetris", export_path)
            self.assertTrue(export_path.exists())

            with zipfile.ZipFile(export_path, "r") as archive:
                names = set(archive.namelist())
            self.assertIn("manifest.json", names)
            self.assertIn("code/lahai_tetris_pkg/entry.py", names)
            self.assertIn("extensions/particles/lahai_glow_burst_particle.py", names)

    def test_package_particle_extensions_register_into_live_manager_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"AEMEATH_DESK_PET_HOME": tmpdir}, clear=False):
            source_root = Path(tmpdir) / "official_source"
            package_root = source_root / "demo_particle_game"
            particle_module = package_root / "extensions" / "particles" / "demo_particle.py"
            particle_module.parent.mkdir(parents=True, exist_ok=True)
            particle_module.write_text(
                "from lib.script.practical.base_particle import BaseParticleScript\n"
                "\n"
                "class DemoParticleScript(BaseParticleScript):\n"
                "    PARTICLE_ID = 'demo_particle'\n"
                "\n"
                "    def create_particles(self, area_type, area_data):\n"
                "        return []\n",
                encoding="utf-8",
            )
            self._write_package(
                package_root,
                game_id="demo_particle_game",
                package_name="Demo Particle Game",
                entry_module="demo_pkg.entry",
                entry_class="DemoGame",
                particle_extensions=[
                    {
                        "module": "extensions/particles/demo_particle.py",
                        "class_name": "DemoParticleScript",
                        "local_id": "spark",
                    }
                ],
            )

            with patch("lib.script.gemes.MAIN.game_packages._official_source_root", return_value=source_root):
                service = GamePackageService()
                self.addCleanup(self._cleanup_service, service)
                particle_script = get_particle_script_manager().get_script("demo_particle_game.spark")

            self.assertIsNotNone(particle_script)
            self.assertEqual(particle_script.get_particle_id(), "demo_particle_game.spark")

    def test_bootstrap_official_package_reinstalls_when_source_changes_without_version_bump(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"AEMEATH_DESK_PET_HOME": tmpdir}, clear=False):
            source_root = Path(tmpdir) / "official_source"
            package_root = source_root / "demo_refresh_game"
            self._write_package(
                package_root,
                game_id="demo_refresh_game",
                package_name="Demo Refresh Game",
                entry_module="demo_refresh.entry",
                entry_class="DemoRefreshGame",
                entry_body="PACKAGE_MARK = 'v1'\n",
            )

            with patch("lib.script.gemes.MAIN.game_packages._official_source_root", return_value=source_root):
                service = GamePackageService()
                self.addCleanup(self._cleanup_service, service)

                installed = service.get_installed_game("demo_refresh_game")
                self.assertIsNotNone(installed)
                assert installed is not None
                entry_path = installed.install_dir / "code" / "demo_refresh" / "entry.py"
                self.assertEqual(entry_path.read_text(encoding="utf-8"), "PACKAGE_MARK = 'v1'\n")

                source_entry = package_root / "code" / "demo_refresh" / "entry.py"
                source_entry.write_text("PACKAGE_MARK = 'v2'\n", encoding="utf-8")
                service.bootstrap_official_packages()

                refreshed = service.get_installed_game("demo_refresh_game")
                self.assertIsNotNone(refreshed)
                assert refreshed is not None
                refreshed_entry = refreshed.install_dir / "code" / "demo_refresh" / "entry.py"
                self.assertEqual(refreshed_entry.read_text(encoding="utf-8"), "PACKAGE_MARK = 'v2'\n")

    def test_bootstrap_prefers_gamepack_zip_over_source_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"AEMEATH_DESK_PET_HOME": tmpdir}, clear=False):
            source_root = Path(tmpdir) / "official_source"
            package_root = source_root / "demo_zip_game"
            self._write_package(
                package_root,
                game_id="demo_zip_game",
                package_name="Demo Zip Game",
                entry_module="demo_zip.entry",
                entry_class="DemoZipGame",
                entry_body="PACKAGE_MARK = 'source'\n",
            )

            gamepack_root = Path(tmpdir) / "gamepack" / "official"
            zip_source_root = Path(tmpdir) / "zip_source" / "demo_zip_game"
            self._write_package(
                zip_source_root,
                game_id="demo_zip_game",
                package_name="Demo Zip Game",
                entry_module="demo_zip.entry",
                entry_class="DemoZipGame",
                entry_body="PACKAGE_MARK = 'zip'\n",
            )
            zip_path = gamepack_root / "demo_zip_game.zip"
            self._write_package_zip(zip_source_root, zip_path)

            with (
                patch("lib.script.gemes.MAIN.game_packages._official_gamepack_root", return_value=gamepack_root),
                patch("lib.script.gemes.MAIN.game_packages._official_source_root", return_value=source_root),
            ):
                service = GamePackageService()
                self.addCleanup(self._cleanup_service, service)

                installed = service.get_installed_game("demo_zip_game")
                self.assertIsNotNone(installed)
                assert installed is not None
                entry_path = installed.install_dir / "code" / "demo_zip" / "entry.py"
                self.assertEqual(entry_path.read_text(encoding="utf-8"), "PACKAGE_MARK = 'zip'\n")


if __name__ == "__main__":
    unittest.main()
