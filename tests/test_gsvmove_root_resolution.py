import tempfile
import unittest
from pathlib import Path

from lib.script.gsvmove.service import GsvmoveService, _read_text_best_effort


def _create_fake_gsvmove_root(path: Path) -> Path:
    (path / "configs").mkdir(parents=True, exist_ok=True)
    (path / ".venv" / "Scripts").mkdir(parents=True, exist_ok=True)
    (path / "start.bat").write_text("@echo off\n", encoding="utf-8")
    (path / "api.py").write_text("print('ok')\n", encoding="utf-8")
    (path / "configs" / "tts_infer.yaml").write_text("custom:\n  device: cpu\n", encoding="utf-8")
    (path / ".venv" / "Scripts" / "python.exe").write_text("", encoding="utf-8")
    return path


class GsvmoveRootResolutionTests(unittest.TestCase):
    def _new_service(self, project_root: Path, launcher_path: Path) -> GsvmoveService:
        service = GsvmoveService.__new__(GsvmoveService)
        service._project_root = project_root
        service._launcher_path = launcher_path
        return service

    def test_resolve_prefers_configured_root_over_local_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project_root = tmp_path / "repo" / "飞行雪绒LTS1.0.5pre2"
            launcher_root = tmp_path / "shared"
            launcher_root.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)

            local_root = _create_fake_gsvmove_root(tmp_path / "repo" / "buildingspace" / "tts" / "GSVmove")
            external_root = _create_fake_gsvmove_root(tmp_path / "external" / "aiyuyin")
            root_file = launcher_root / "config" / "gsvmove_root.txt"
            root_file.parent.mkdir(parents=True, exist_ok=True)
            root_file.write_text(str(external_root), encoding="utf-8")

            launcher_path = launcher_root / "start_gsvmove.bat"
            launcher_path.write_text(
                f'@echo off\nset "ROOT_FILE={root_file}"\n',
                encoding="utf-8",
            )

            service = self._new_service(project_root, launcher_path)
            resolved_root, resolved_root_file = service._resolve_gsvmove_root()

            self.assertTrue(local_root.exists())
            self.assertEqual(resolved_root, external_root)
            self.assertEqual(resolved_root_file, root_file)
            self.assertEqual(_read_text_best_effort(root_file), str(external_root))

    def test_resolve_uses_configured_root_when_no_local_root_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project_root = tmp_path / "repo" / "飞行雪绒LTS1.0.5pre2"
            launcher_root = tmp_path / "shared"
            launcher_root.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)

            external_root = _create_fake_gsvmove_root(tmp_path / "external" / "aiyuyin")
            root_file = launcher_root / "config" / "gsvmove_root.txt"
            root_file.parent.mkdir(parents=True, exist_ok=True)
            root_file.write_text(str(external_root), encoding="utf-8")

            launcher_path = launcher_root / "start_gsvmove.bat"
            launcher_path.write_text(
                f'@echo off\nset "ROOT_FILE={root_file}"\n',
                encoding="utf-8",
            )

            service = self._new_service(project_root, launcher_path)
            resolved_root, resolved_root_file = service._resolve_gsvmove_root()

            self.assertEqual(resolved_root, external_root)
            self.assertEqual(resolved_root_file, root_file)

    def test_resolve_uses_launcher_find_root_when_configured_root_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project_root = tmp_path / "repo" / "飞行雪绒LTS1.0.5pre2"
            launcher_root = tmp_path / "shared"
            launcher_root.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)

            search_base = tmp_path / "scan_here"
            discovered_root = _create_fake_gsvmove_root(search_base / "nested" / "GSVmove")
            root_file = launcher_root / "config" / "gsvmove_root.txt"
            root_file.parent.mkdir(parents=True, exist_ok=True)
            root_file.write_text(str(tmp_path / "missing" / "gsvmove"), encoding="utf-8")

            launcher_path = launcher_root / "start_gsvmove.bat"
            launcher_path.write_text(
                "@echo off\n"
                f'set "ROOT_FILE={root_file}"\n'
                f'call :find_root "{search_base}"\n',
                encoding="utf-8",
            )

            service = self._new_service(project_root, launcher_path)
            resolved_root, resolved_root_file = service._resolve_gsvmove_root()

            self.assertEqual(resolved_root, discovered_root)
            self.assertEqual(resolved_root_file, root_file)
            self.assertEqual(_read_text_best_effort(root_file), str(discovered_root))


if __name__ == "__main__":
    unittest.main()
