"""Behavioural tests for the uninstaller's pre-uninstall process sweep.

The visual harness proves the uninstaller window still paints correctly; this
harness compiles the same ``uninstaller.c`` and checks the rule that decides
which processes get closed before files are deleted: only images inside the
install root (or the shared contract directory) may be touched, so an
unrelated ``python.exe`` / ``node.exe`` keeps running.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scripts import build_offline_installer as installer


@unittest.skipUnless(os.name == "nt", "native uninstaller sweep checks are Windows-only")
class OfflineUninstallerShutdownTests(unittest.TestCase):
    _HOLD_SECONDS = 20

    @classmethod
    def setUpClass(cls) -> None:
        try:
            vsdevcmd = installer.find_vsdevcmd(None)
        except SystemExit as exc:
            raise unittest.SkipTest(str(exc)) from exc

        cls._temporary = tempfile.TemporaryDirectory(prefix="fsv-uninstaller-sweep-")
        root = Path(cls._temporary.name)
        source_root = installer.DEFAULT_INSTALLER_SOURCE / "src"
        native_test_root = Path(__file__).parent / "native"
        for source in (
            source_root / "uninstaller.c",
            source_root / "resource.h",
            native_test_root / "uninstaller_shutdown_harness.c",
        ):
            shutil.copy2(source, root / source.name)
        installer._write_installer_theme_header(root / "installer_theme.h")

        installer.run_vs_command(
            vsdevcmd,
            " ".join(
                (
                    "cl.exe",
                    "/nologo",
                    "/MT",
                    "/O2",
                    "/W4",
                    "/WX",
                    "/utf-8",
                    '/Fe:"uninstaller_shutdown_harness.exe"',
                    '"uninstaller_shutdown_harness.c"',
                    "/link",
                    "/SUBSYSTEM:CONSOLE",
                    "/MANIFEST:NO",
                )
            ),
            root,
        )
        cls._harness = root / "uninstaller_shutdown_harness.exe"
        if not cls._harness.is_file():
            raise unittest.SkipTest("Visual Studio harness compilation produced no executable")

        # The sweep matches by image path, so the stand-ins are copies of this
        # harness: one lives inside a fake install root, one lives outside it.
        cls._inside_root = root / "install-root"
        cls._shared_root = root / "shared-contract"
        cls._outside_root = root / "elsewhere"
        for directory in (cls._inside_root, cls._shared_root, cls._outside_root):
            directory.mkdir()
        cls._inside_image = cls._inside_root / "runner.exe"
        cls._shared_image = cls._shared_root / "runner.exe"
        cls._outside_image = cls._outside_root / "runner.exe"
        for image in (cls._inside_image, cls._shared_image, cls._outside_image):
            shutil.copy2(cls._harness, image)
        cls._spawned: list[int] = []

    @classmethod
    def tearDownClass(cls) -> None:
        for pid in getattr(cls, "_spawned", ()):
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                text=True,
                check=False,
            )
        temporary = getattr(cls, "_temporary", None)
        if temporary is not None:
            temporary.cleanup()

    @classmethod
    def _harness_command(cls, *arguments: object) -> str:
        # The spawned stand-in inherits the harness stdout handle and lives for
        # seconds afterwards, so the output goes to a file: a pipe would keep
        # ``subprocess`` reading until that stand-in exits.
        output_path = Path(cls._temporary.name) / "harness-output.txt"
        with open(output_path, "w", encoding="utf-8") as sink:
            result = subprocess.run(
                [str(cls._harness), *(str(item) for item in arguments)],
                stdout=sink,
                stderr=subprocess.STDOUT,
                timeout=90,
                check=False,
            )
        text = output_path.read_text(encoding="utf-8", errors="replace")
        if result.returncode != 0:
            raise AssertionError(
                f"uninstaller sweep harness failed for {arguments}: {text}"
            )
        return text.strip()

    @classmethod
    def _spawn_holder(cls, image: Path) -> int:
        output = cls._harness_command("spawn", image, cls._HOLD_SECONDS)
        pid = int(output.split("pid=", 1)[1].splitlines()[0])
        if pid == 0:
            raise AssertionError(f"could not start the stand-in process from {image}")
        cls._spawned.append(pid)
        return pid

    @classmethod
    def _is_alive(cls, pid: int) -> bool:
        return cls._harness_command("alive", pid).endswith("1")

    @classmethod
    def _sweep(cls, install_root: Path, shared_root: Path) -> int:
        output = cls._harness_command("sweep", install_root, shared_root)
        return int(output.split("terminated=", 1)[1].splitlines()[0])

    def test_sweep_closes_processes_started_from_the_install_root(self) -> None:
        pid = self._spawn_holder(self._inside_image)
        self.assertTrue(self._is_alive(pid), "the stand-in should be running before the sweep")

        self._sweep(self._inside_root, self._shared_root)

        self.assertFalse(self._is_alive(pid), "the sweep must close processes from the install root")

    def test_sweep_leaves_processes_outside_the_install_root_running(self) -> None:
        pid = self._spawn_holder(self._outside_image)

        self._sweep(self._inside_root, self._shared_root)

        self.assertTrue(
            self._is_alive(pid),
            "the sweep must never touch processes that do not run from our own directories",
        )

    def test_sweep_closes_processes_started_from_the_shared_contract_root(self) -> None:
        pid = self._spawn_holder(self._shared_image)

        self._sweep(self._inside_root, self._shared_root)

        self.assertFalse(
            self._is_alive(pid),
            "the voice runtime starts from the shared contract root and must be closed too",
        )

    def test_sweep_without_matching_processes_reports_nothing_terminated(self) -> None:
        self.assertEqual(self._sweep(self._inside_root, self._shared_root), 0)


if __name__ == "__main__":
    unittest.main()
