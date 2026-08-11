import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


class LifecycleContractTests(unittest.TestCase):
    def test_lifecycle_protocols_import_without_pyqt(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = textwrap.dedent(
            """
            import builtins

            original_import = builtins.__import__

            def blocked_import(name, *args, **kwargs):
                if name == "PyQt5" or name.startswith("PyQt5."):
                    raise ModuleNotFoundError("PyQt5 blocked by test")
                return original_import(name, *args, **kwargs)

            builtins.__import__ = blocked_import

            from lib.core.application_ui import ApplicationUiHost
            from lib.core.overlay_host import OverlayHost
            from lib.core.pet_host import PetWindowHost
            from lib.core.tray_host import TrayHost

            class Overlay:
                def flush_immediately(self): pass
                def cleanup(self): pass

            class ApplicationUi:
                def configure_services(self, yuanbao_service): pass
                def prepare_application(self, application): pass
                def prepare_runtime(self): pass
                def start_runtime(self, application): pass
                def open_announcement(self): pass
                def begin_shutdown(self): pass
                def stop_runtime(self): pass
                def cleanup(self): pass
                def has_exit_animation(self): return False
                def finalize(self): pass

            class Pet:
                def shutdown_host(self): pass

            class Tray:
                def connect_quit_requested(self, callback): pass
                def disconnect_quit_requested(self, callback): pass
                def connect_announcement_requested(self, callback): pass
                def disconnect_announcement_requested(self, callback): pass
                def connect_command_requested(self, callback): pass
                def disconnect_command_requested(self, callback): pass
                def set_menu_state(self, state): pass
                def initialize(self): return True
                def begin_shutdown(self): pass
                def cleanup(self): pass

            application_ui: ApplicationUiHost = ApplicationUi()
            overlay: OverlayHost = Overlay()
            pet: PetWindowHost = Pet()
            tray: TrayHost = Tray()
            application_ui.prepare_runtime()
            overlay.flush_immediately()
            pet.shutdown_host()
            assert tray.initialize()
            tray.cleanup()
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_application_coordinator_uses_lifecycle_surfaces(self):
        repo_root = Path(__file__).resolve().parents[1]
        source = (repo_root / "lib" / "script" / "main.py").read_text(encoding="utf-8")

        for token in (
            "lib.core.qt_bridge",
            "lib.script.ui",
            "lib.script.gemes",
            "configure_selected_backend",
            "register_backend",
            "quit_requested.",
            "announcement_requested.",
            "deleteLater()",
            "_timing_manager",
            "self._pet.close()",
            "self._particles.close()",
            "self._effects.close()",
            "_tray_icon_cleanup",
        ):
            self.assertNotIn(token, source, token)

        self.assertIn("shutdown_host()", source)
        self.assertIn("disconnect_quit_requested", source)
        self.assertIn("disconnect_announcement_requested", source)
        self.assertIn("disconnect_command_requested", source)
        self.assertIn("set_menu_state", source)
        self.assertIn("self._application_ui", source)


if __name__ == "__main__":
    unittest.main()
