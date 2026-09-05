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
