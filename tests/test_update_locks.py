"""进入更新流程前解除安装目录占用的契约。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from lib.script.app import update_locks


def _needed_interpreter_files(prefix: Path) -> list[str]:
    names = ["python.exe", "python311.dll", "python3.dll"]
    names.extend(
        name
        for name in ("vcruntime140.dll", "vcruntime140_1.dll")
        if (prefix / name).is_file()
    )
    return [name for name in names if (prefix / name).is_file()]


class UpdateLockTargetTests(unittest.TestCase):
    """目标筛选：只动镜像在安装目录里的进程，当前进程与祖先一律跳过。"""

    def test_targets_are_limited_to_the_install_directory(self):
        root = Path(tempfile.gettempdir()) / "fsv-root"
        processes = [(10, 1, "node.exe"), (11, 1, "pythonw.exe"), (12, 1, "other.exe")]
        images = {
            10: str(root / "app" / "resc" / "node.exe"),
            11: str(Path(tempfile.gettempdir()) / "elsewhere" / "pythonw.exe"),
            12: str(root.parent / "fsv-root-sibling" / "other.exe"),
        }
        with mock.patch.object(
            update_locks, "_process_image_path", side_effect=images.get
        ):
            targets = update_locks._target_processes(processes, root, {os.getpid()})

        self.assertEqual(targets, [(10, "node.exe")])

    def test_current_process_and_its_ancestors_are_never_targets(self):
        """安装壳 -> 桌宠进程 -> 侧车 的链条里只有侧车可以被结束。"""
        root = Path(tempfile.gettempdir()) / "fsv-root"
        # 10 是安装壳（祖先），11 是当前进程（桌宠），12 是 sidecar。
        processes = [(10, 0, "启动飞行雪绒.exe"), (11, 10, "pythonw.exe"), (12, 11, "node.exe")]
        images = {
            10: str(root / "启动飞行雪绒.exe"),
            11: str(root / "runtime" / "python311" / "pythonw.exe"),
            12: str(root / "app" / "resc" / "node.exe"),
        }
        with (
            mock.patch.object(update_locks.os, "getpid", return_value=11),
            mock.patch.object(
                update_locks, "_process_image_path", side_effect=images.get
            ),
        ):
            protected = update_locks._ancestor_pids(processes) | {11}
            self.assertEqual(protected, {10, 11})
            targets = update_locks._target_processes(processes, root, protected)
        self.assertEqual(targets, [(12, "node.exe")])

    def test_ancestor_chain_stops_on_a_cycle(self):
        processes = [(1, 2, "a.exe"), (2, 1, "b.exe")]
        with mock.patch.object(update_locks.os, "getpid", return_value=1):
            self.assertEqual(update_locks._ancestor_pids(processes), {2})


class WorkbenchHelperRestoreTests(unittest.TestCase):
    """控制面板窗口被释放流程关掉时要按原页面重新打开。"""

    def run_release(self, *, helper_pid, root):
        processes = [(10, 0, "node.exe"), (helper_pid or 99, 0, "pythonw.exe")]
        images = {
            10: str(root / "app" / "resc" / "node.exe"),
            (helper_pid or 99): str(root / "runtime" / "python311" / "pythonw.exe"),
        }
        with (
            mock.patch.object(update_locks, "_snapshot_processes", return_value=processes),
            mock.patch.object(
                update_locks, "_process_image_path", side_effect=images.get
            ),
            mock.patch.object(update_locks, "_terminate_process") as terminate,
            mock.patch.object(update_locks, "_workbench_helper_process_id", return_value=helper_pid),
            mock.patch.object(update_locks, "_restore_workbench_helper") as restore,
            mock.patch.object(update_locks.time, "sleep"),
        ):
            released = update_locks.release_install_directory_locks(root, info=lambda _m: None)
        return released, terminate, restore

    def test_helper_is_relaunched_after_being_killed(self):
        root = Path(tempfile.gettempdir()) / "fsv-root"
        released, terminate, restore = self.run_release(helper_pid=99, root=root)

        self.assertEqual(len(released), 2)
        self.assertEqual([call.args[0] for call in terminate.call_args_list], [10, 99])
        restore.assert_called_once()

    def test_other_processes_do_not_trigger_a_relaunch(self):
        root = Path(tempfile.gettempdir()) / "fsv-root"
        released, terminate, restore = self.run_release(helper_pid=None, root=root)

        self.assertEqual(len(released), 2)
        self.assertEqual([call.args[0] for call in terminate.call_args_list], [10, 99])
        restore.assert_not_called()


class UpdateLockKillTests(unittest.TestCase):
    """真的结束一个从安装目录启动的进程，并释放它占用的文件。"""

    @unittest.skipUnless(os.name == "nt", "只有 Windows 有进程镜像占用")
    def test_release_kills_a_process_that_runs_from_the_install_directory(self):
        base_prefix = Path(getattr(sys, "base_prefix", sys.prefix))
        names = _needed_interpreter_files(base_prefix)
        if "python.exe" not in names or "python311.dll" not in names:
            self.skipTest("解释器目录布局不完整，无法复制出一个可运行的安装目录")

        with tempfile.TemporaryDirectory(prefix="fsv-locks-") as temporary:
            install_root = Path(temporary) / "install"
            install_root.mkdir()
            for name in names:
                shutil.copy2(base_prefix / name, install_root / name)
            executable = install_root / "python.exe"
            if not executable.is_file():
                self.skipTest("无法复制解释器可执行文件")

            child = subprocess.Popen(
                [str(executable), "-c", "import time; time.sleep(60)"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 10
                while child.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.2)
                    if update_locks._process_image_path(child.pid):
                        break
                if child.poll() is not None:
                    self.skipTest("复制出的解释器没能常驻，跳过这次释放验证")

                messages: list[str] = []
                released = update_locks.release_install_directory_locks(
                    install_root, info=messages.append
                )

                self.assertTrue(any(str(child.pid) in item for item in released), released)
                self.assertTrue(messages and "后台进程" in messages[0], messages)
                time.sleep(1.0)
                self.assertIsNotNone(child.poll())
            finally:
                if child.poll() is None:
                    child.kill()


if __name__ == "__main__":
    unittest.main()
