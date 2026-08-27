from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from lib.script.office import runtime as office_runtime


class _OpenAiHandler(BaseHTTPRequestHandler):
    requests: queue.Queue = queue.Queue()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        self.requests.put((self.path, payload))
        chunks = (
            {
                "id": "chatcmpl-fsv",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": payload.get("model", "test-model"),
                "choices": [{
                    "index": 0,
                    "delta": {"role": "assistant", "content": "完成"},
                    "finish_reason": None,
                }],
            },
            {
                "id": "chatcmpl-fsv",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": payload.get("model", "test-model"),
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
            },
        )
        body = "".join(
            f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            for chunk in chunks
        ) + "data: [DONE]\n\n"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format, *_args):
        return


def _copy_profile(root: Path) -> Path:
    runtime_root = office_runtime.runtime_root()
    dsh_home = root / "dsh-home"
    profile = dsh_home / "profiles" / "fsv-office"
    bridge = profile / "node_modules" / "@fsv" / "dsh-office-bridge"
    bridge.mkdir(parents=True)
    for name in ("package.json", "cordis.patch.yml"):
        shutil.copy2(runtime_root / "profile" / name, profile / name)
    for name in ("package.json", "index.mjs", "credentials.mjs"):
        shutil.copy2(runtime_root / "bridge" / name, bridge / name)
    return dsh_home


class DshOfficeSidecarTests(unittest.TestCase):
    def test_generic_route_omits_vendor_reasoning_fields(self):
        readiness = office_runtime.runtime_readiness_error()
        if readiness:
            self.skipTest(readiness)

        while not _OpenAiHandler.requests.empty():
            _OpenAiHandler.requests.get_nowait()
        server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAiHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        process = None
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                workspace = root / "workspace"
                sessions = root / "sessions"
                workspace.mkdir()
                sessions.mkdir()
                env = os.environ.copy()
                env.update({
                    "DSH_HOME": str(_copy_profile(root)),
                    "DSH_BUNDLED_SKILL_DIR": str(office_runtime.office_skill_root()),
                    "DSH_TELEMETRY_DISABLED": "1",
                    "FSV_OFFICE_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                    "FSV_OFFICE_MODEL": "test-model",
                    "FSV_OFFICE_SESSION_ROOT": str(sessions),
                    "FSV_OFFICE_SYSTEM_PROMPT": office_runtime.load_office_system_prompt(),
                })
                process = subprocess.Popen(
                    [
                        str(office_runtime.resolve_node_executable()),
                        str(office_runtime.dsh_entry_path()),
                        "--profile",
                        "fsv-office",
                    ],
                    cwd=workspace,
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
                self.assertIsNotNone(process.stdin)
                self.assertIsNotNone(process.stdout)
                events: queue.Queue = queue.Queue()

                def read_events():
                    for line in process.stdout:
                        try:
                            payload = json.loads(line)
                        except ValueError:
                            continue
                        if payload.get("protocol") == office_runtime.PROTOCOL:
                            events.put(payload)

                reader = threading.Thread(target=read_events, daemon=True)
                reader.start()
                commands = (
                    {"type": "configure", "apiKey": "test-key"},
                    {
                        "type": "create",
                        "taskId": "task-1",
                        "workspace": str(workspace),
                        "prompt": "创建一个文本文件",
                        "model": "test-model",
                        "reasoningEffort": "max",
                    },
                )
                for command in commands:
                    process.stdin.write(json.dumps(command, ensure_ascii=False) + "\n")
                process.stdin.flush()

                deadline = time.monotonic() + 30
                observed = []
                while time.monotonic() < deadline:
                    try:
                        event = events.get(timeout=0.5)
                    except queue.Empty:
                        if process.poll() is not None:
                            break
                        continue
                    observed.append(event)
                    if event.get("type") == "task_idle":
                        break
                    if event.get("type") in {"fatal", "task_error", "command_error"}:
                        self.fail(str(event))
                else:
                    self.fail(f"DSH task timed out: {observed[-5:]}")

                path, request = _OpenAiHandler.requests.get(timeout=5)
                self.assertEqual(path, "/v1/chat/completions")
                self.assertNotIn("thinking", request)
                self.assertNotIn("reasoning_effort", request)
                system_text = "\n".join(
                    str(message.get("content", ""))
                    for message in request.get("messages", [])
                    if message.get("role") in {"system", "developer"}
                )
                transcript_text = "\n".join(
                    str(message.get("content", ""))
                    for message in request.get("messages", [])
                )
                self.assertIn("thorough execution strategy", system_text)
                self.assertIn("office coding agent inside Flying Snow Velvet", system_text)
                self.assertIn("fsv-office-workflow", transcript_text)
                self.assertIn("fsv-browser-ui-check", transcript_text)
                self.assertNotIn("fsv-browser-research", transcript_text)
                self.assertNotIn("fsv-dependency-maintenance", transcript_text)
                self.assertNotIn("fsv-release-validation", transcript_text)
                self.assertTrue(any(event.get("type") == "task_idle" for event in observed))

                process.stdin.write('{"type":"shutdown"}\n')
                process.stdin.flush()
                process.wait(timeout=10)
                self.assertEqual(process.returncode, 0)
                process.stdin.close()
                reader.join(timeout=5)
                process.stdout.close()
        finally:
            if process is not None and process.poll() is None:
                try:
                    if process.stdin is not None and not process.stdin.closed:
                        process.stdin.write('{"type":"shutdown"}\n')
                        process.stdin.flush()
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
            if process is not None:
                if process.stdin is not None and not process.stdin.closed:
                    process.stdin.close()
                if process.stdout is not None and not process.stdout.closed:
                    process.stdout.close()
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
