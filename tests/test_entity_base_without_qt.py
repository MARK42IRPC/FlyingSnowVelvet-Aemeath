import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


class EntityBaseWithoutQtTests(unittest.TestCase):
    def test_core_entity_contract_imports_and_runs_without_pyqt(self):
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

            from lib.core.entity.base import BaseEntity
            from lib.core.graphics.types import Point, Rect

            class Entity(BaseEntity):
                def change_state(self, state): pass
                def get_current_state(self): return "idle"
                def start_move(self, target): pass
                def stop_move(self): pass
                def get_position(self): return Point(3, 4)
                def play_animation(self, state, duration=0): pass
                def spawn_particles(self, *args, **kwargs): pass
                def toggle_command_dialog(self): pass
                def schedule_task(self, callback, delay_ms, repeat=False): return "task"
                def cancel_task(self, task_id): pass
                def get_geometry(self): return Rect(3, 4, 100, 80)
                def is_moving(self): return False
                def set_direction(self, flipped): pass
                def get_direction(self): return False

            entity = Entity()
            assert entity.get_core_position() == Point(3, 4)
            assert entity.get_core_geometry() == Rect(3, 4, 100, 80)
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


if __name__ == "__main__":
    unittest.main()
