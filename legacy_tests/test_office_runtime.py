from __future__ import annotations

import io
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.script.office import runtime as office_runtime
from lib.script.office.runtime import DshOfficeRuntime


class _Process:
    def __init__(self, *, timeout: bool = False) -> None:
        self.stdin = io.StringIO()
        self.stdout = io.StringIO()
        self.pid = 1234
        self.return_code = None
        self.timeout = timeout
        self.wait_calls = []

    def poll(self):
        return self.return_code

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self.timeout:
            raise subprocess.TimeoutExpired(["node"], timeout)
        self.return_code = 0
        return 0


class MockInstall:
    """测试用的本机 DSH 探测结果。"""

    def __init__(self) -> None:
        self.entry = Path(r"C:\local\node_modules\@deepseek-ai\dsh\lib\bin.js")
        self.node_modules_root = Path(r"C:\local\node_modules")


class DshOfficeRuntimeLifecycleTests(unittest.TestCase):
    @staticmethod
    def _running_runtime(process: _Process) -> DshOfficeRuntime:
        runtime = DshOfficeRuntime(lambda payload: None)
        runtime._process = process
        runtime._fingerprint = ("workspace", "base", "model", "prompt")
        runtime._stderr_handle = io.StringIO()
        return runtime

    def test_cleanup_requests_shutdown_and_is_idempotent(self):
        process = _Process()
        runtime = self._running_runtime(process)

        runtime.cleanup()
        runtime.cleanup()

        self.assertEqual(process.wait_calls, [3])
        self.assertEqual(process.stdin.getvalue(), '{"type":"shutdown"}\n')
        self.assertFalse(runtime.running)

    def test_cleanup_terminates_runtime_that_ignores_shutdown(self):
        process = _Process(timeout=True)
        runtime = self._running_runtime(process)
        with patch.object(runtime, "_terminate_process_tree") as terminate:
            runtime.cleanup()

        terminate.assert_called_once_with(process)
        self.assertFalse(runtime.running)

    def test_start_after_cleanup_is_rejected_before_readiness_probe(self):
        runtime = DshOfficeRuntime(lambda payload: None)
        runtime.cleanup()
        with patch("lib.script.office.runtime.runtime_readiness_error") as readiness:
            with self.assertRaisesRegex(RuntimeError, "已清理"):
                runtime.start(
                    workspace=".",
                    base_url="https://example.invalid",
                    model="model",
                    api_key="secret",
                )
        readiness.assert_not_called()

    def test_office_system_prompt_is_loaded_from_managed_resource(self):
        prompt = office_runtime.load_office_system_prompt()

        self.assertIn("office coding agent inside Flying Snow Velvet", prompt)
        self.assertEqual(
            office_runtime.office_system_prompt_path(),
            office_runtime.project_root() / "resc" / "agent" / "office_system_prompt.txt",
        )

    def test_readiness_reports_missing_managed_prompt(self):
        with patch.dict("config.ollama_config.OFFICE_MODE", {"backend": "dsh"}, clear=False), patch.object(
            office_runtime,
            "office_system_prompt_path",
            return_value=office_runtime.project_root() / "resc" / "agent" / "missing.txt",
        ):
            error = office_runtime.runtime_readiness_error()

        self.assertIn("办公系统提示词资源无法读取", error)

    def test_readiness_rejects_non_dsh_backend(self):
        with patch.dict("config.ollama_config.OFFICE_MODE", {"backend": "unsupported"}, clear=False):
            error = office_runtime.runtime_readiness_error()

        self.assertIn("选择 DSH", error)

    def test_readiness_accepts_local_backend_with_supported_install(self):
        install = MockInstall()
        with patch.dict("config.ollama_config.OFFICE_MODE", {"backend": "local_dsh"}, clear=False), patch.object(
            office_runtime.local_dsh, "probe_local_dsh", return_value=install
        ), patch.object(
            office_runtime.local_dsh,
            "resolve_local_node_executable",
            return_value=r"C:\node\node.exe",
        ):
            error = office_runtime.runtime_readiness_error()

        self.assertEqual(error, "")

    def test_readiness_reports_local_backend_probe_failure(self):
        with patch.dict("config.ollama_config.OFFICE_MODE", {"backend": "local_dsh"}, clear=False), patch.object(
            office_runtime.local_dsh, "probe_local_dsh", return_value=None
        ), patch.object(
            office_runtime.local_dsh,
            "local_dsh_status",
            return_value={"available": False, "reason": "未探测到 本机 DeepSeek Harness"},
        ):
            error = office_runtime.runtime_readiness_error()

        self.assertIn("本机 DeepSeek Harness", error)

    def test_readiness_reports_local_backend_missing_node(self):
        with patch.dict("config.ollama_config.OFFICE_MODE", {"backend": "local_dsh"}, clear=False), patch.object(
            office_runtime.local_dsh, "probe_local_dsh", return_value=MockInstall()
        ), patch.object(
            office_runtime.local_dsh, "resolve_local_node_executable", return_value=None
        ):
            error = office_runtime.runtime_readiness_error()

        self.assertIn("需要 Node 运行时", error)

    def test_launch_command_uses_local_install_when_selected(self):
        install = MockInstall()
        with patch.dict("config.ollama_config.OFFICE_MODE", {"backend": "local_dsh"}, clear=False), patch.object(
            office_runtime.local_dsh, "probe_local_dsh", return_value=install
        ), patch.object(
            office_runtime.local_dsh,
            "resolve_local_node_executable",
            return_value=r"C:\node\node.exe",
        ):
            command, extra_env = office_runtime.resolve_launch_command()

        self.assertEqual(
            command,
            [r"C:\node\node.exe", str(install.entry), "--profile", "fsv-office"],
        )
        self.assertEqual(extra_env, {"NODE_PATH": str(install.node_modules_root)})

    def test_launch_command_reports_missing_local_install(self):
        with patch.dict("config.ollama_config.OFFICE_MODE", {"backend": "local_dsh"}, clear=False), patch.object(
            office_runtime.local_dsh, "probe_local_dsh", return_value=None
        ), patch.object(
            office_runtime.local_dsh,
            "local_dsh_status",
            return_value={"available": False, "reason": "未探测到 本机 DeepSeek Harness"},
        ):
            with self.assertRaisesRegex(RuntimeError, "未探测到 本机 DeepSeek Harness"):
                office_runtime.resolve_launch_command()

    def test_node_environment_rejects_external_runtime_hooks(self):
        injected = {
            "Path": r"C:\\user-tools",
            "NODE_OPTIONS": r"--import=C:\\outside\\hook.mjs",
            "node_path": r"C:\\outside\\modules",
            "NODE_TLS_REJECT_UNAUTHORIZED": "0",
            "NPM_CONFIG_PREFIX": r"C:\\outside\\npm",
            "DSH_HOME": r"C:\\outside\\dsh",
            "FSV_OFFICE_MODEL": "outside-model",
            "OPENSSL_CONF": r"C:\\outside\\openssl.cnf",
        }
        with patch.dict(os.environ, injected, clear=True), patch.object(
            office_runtime,
            "bundled_node_executable",
            return_value=Path(r"C:\\missing\\node.exe"),
        ):
            environment = office_runtime._isolated_node_environment()

        self.assertNotIn(r"C:\\user-tools", environment["PATH"])
        self.assertIn("System32", environment["PATH"])
        self.assertEqual(environment["NODE_ENV"], "production")
        for name in injected:
            if name != "Path":
                self.assertNotIn(name, environment)


if __name__ == "__main__":
    unittest.main()
