import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


REMOVED_PACKAGE = "music" + "dl"


class RemovedMusicFallbackDependencyTests(unittest.TestCase):
    def test_removed_package_is_not_a_project_dependency(self):
        dependency_files = [
            PROJECT_ROOT / "requirements.txt",
            PROJECT_ROOT / "install_deps.py",
        ]

        for path in dependency_files:
            with self.subTest(path=path.name):
                self.assertNotIn(REMOVED_PACKAGE, path.read_text(encoding="utf-8").lower())

    def test_music_clients_do_not_import_removed_package(self):
        client_files = [
            PROJECT_ROOT / "lib" / "script" / "qqmusic" / "qqmisic.py",
            PROJECT_ROOT / "lib" / "script" / "kugou" / "kugou.py",
        ]

        for path in client_files:
            with self.subTest(path=path.name):
                self.assertNotIn(REMOVED_PACKAGE, path.read_text(encoding="utf-8").lower())


if __name__ == "__main__":
    unittest.main()
