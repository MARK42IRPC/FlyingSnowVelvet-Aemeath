import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


class ApplicationRuntimeContractTests(unittest.TestCase):
    def test_protocol_imports_and_runs_without_pyqt(self):
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

            from lib.core.application_runtime import ApplicationRuntime

            class Runtime:
                def create_application(self, logger, argv=None): return object()
                def connect_exit_acknowledged(self, application, callback): pass
                def schedule_once(self, delay_ms, callback): callback()
                def run_event_loop(self, application): return 0
                def process_events(self, application): pass
                def request_exit(self, application, exit_code): pass
                def close_all_windows(self, application): pass

            runtime: ApplicationRuntime = Runtime()
            called = []
            runtime.schedule_once(0, lambda: called.append(True))
            assert called == [True]
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

    def test_application_coordinator_has_no_direct_pyqt_calls(self):
        repo_root = Path(__file__).resolve().parents[1]
        source = (repo_root / "lib" / "script" / "main.py").read_text(encoding="utf-8")

        self.assertNotIn("from PyQt5", source)
        self.assertNotIn("import PyQt5", source)
        self.assertNotIn("QTimer", source)
        self.assertNotIn("QEvent", source)


if __name__ == "__main__":
    unittest.main()
