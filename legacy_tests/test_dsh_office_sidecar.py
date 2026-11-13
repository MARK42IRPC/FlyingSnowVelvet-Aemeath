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
from lib.script.office.pet_tools import EXCLUDED_PET_TOOL_NAMES, PET_TOOL_NAMES


def _completion_chunks(model: str, delta: dict, finish_reason: str) -> bytes:
    chunks = (
        {
            "id": "chatcmpl-fsv",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        },
        {
            "id": "chatcmpl-fsv",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
        },
    )
    body = "".join(
        f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks
    ) + "data: [DONE]\n\n"
    return body.encode("utf-8")


class _OpenAiHandler(BaseHTTPRequestHandler):
    requests: queue.Queue = queue.Queue()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        self.requests.put((self.path, payload))
        encoded = _completion_chunks(
            payload.get("model", "test-model"),
            {"role": "assistant", "content": "完成"},
            "stop",
        )
        self._write_stream(encoded)

    def _write_stream(self, encoded: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format, *_args):
        return


class _PetToolHandler(_OpenAiHandler):
    """第一轮要求生成雪豹，拿到工具结果后第二轮收尾。"""

    requests: queue.Queue = queue.Queue()
    _counter = 0
    _counter_lock = threading.Lock()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        self.requests.put((self.path, payload))
        with self._counter_lock:
            type(self)._counter += 1
            turn = type(self)._counter
        if turn == 1:
            encoded = _completion_chunks(
                payload.get("model", "test-model"),
                {
                    "role": "assistant",
                    "tool_calls": [{
                        "index": 0,
                        "id": "call-pet-1",
                        "type": "function",
                        "function": {
                            "name": "spawn_snow_leopard",
                            "arguments": json.dumps({"count": 2}, ensure_ascii=False),
                        },
                    }],
                },
                "tool_calls",
            )
        else:
            encoded = _completion_chunks(
                payload.get("model", "test-model"),
                {"role": "assistant", "content": "雪豹已经到齐。"},
                "stop",
            )
        self._write_stream(encoded)


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


class _SidecarProcess:
    """真实 DSH 办公侧车：写 JSONL 命令，读 JSONL 事件。"""

    def __init__(self, root: Path, base_url: str) -> None:
        self.root = root
        self.workspace = root / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        sessions = root / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.update({
            "DSH_HOME": str(_copy_profile(root)),
            "DSH_BUNDLED_SKILL_DIR": str(office_runtime.office_skill_root()),
            "DSH_TELEMETRY_DISABLED": "1",
            "FSV_OFFICE_BASE_URL": base_url,
            "FSV_OFFICE_MODEL": "test-model",
            "FSV_OFFICE_SESSION_ROOT": str(sessions),
            "FSV_OFFICE_SYSTEM_PROMPT": office_runtime.load_office_system_prompt(),
        })
        self.events: queue.Queue = queue.Queue()
        self.process = subprocess.Popen(
            [
                str(office_runtime.resolve_node_executable()),
                str(office_runtime.dsh_entry_path()),
                "--profile",
                "fsv-office",
            ],
            cwd=self.workspace,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self.reader = threading.Thread(target=self._read_events, daemon=True)
        self.reader.start()

    def _read_events(self) -> None:
        stream = self.process.stdout
        if stream is None:
            return
        for line in stream:
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if payload.get("protocol") == office_runtime.PROTOCOL:
                self.events.put(payload)

    def send(self, command: dict) -> None:
        stream = self.process.stdin
        assert stream is not None
        stream.write(json.dumps(command, ensure_ascii=False) + "\n")
        stream.flush()

    def collect(self, *, on_event=None, deadline_seconds: float = 30) -> list[dict]:
        """收集事件直到任务空闲；致命事件直接抛出，超时给出尾部事件。"""
        observed: list[dict] = []
        deadline = time.monotonic() + deadline_seconds
        while time.monotonic() < deadline:
            try:
                event = self.events.get(timeout=0.5)
            except queue.Empty:
                if self.process.poll() is not None:
                    break
                continue
            observed.append(event)
            if on_event is not None:
                on_event(event)
            if event.get("type") == "task_idle":
                return observed
            if event.get("type") in {"fatal", "task_error", "command_error"}:
                raise AssertionError(str(event))
        raise AssertionError(f"DSH task timed out: {observed[-5:]}")

    def shutdown(self) -> None:
        if self.process.poll() is None:
            self.send({"type": "shutdown"})
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def close(self) -> None:
        try:
            self.shutdown()
        finally:
            if self.process.stdin is not None and not self.process.stdin.closed:
                self.process.stdin.close()
            if self.process.stdout is not None and not self.process.stdout.closed:
                self.process.stdout.close()
            self.reader.join(timeout=5)

    def __enter__(self) -> "_SidecarProcess":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def _drain(handler: type[BaseHTTPRequestHandler]) -> None:
    while not handler.requests.empty():
        handler.requests.get_nowait()


class DshOfficeSidecarTests(unittest.TestCase):
    def test_generic_route_omits_vendor_reasoning_fields(self):
        readiness = office_runtime.runtime_readiness_error()
        if readiness:
            self.skipTest(readiness)

        _drain(_OpenAiHandler)
        server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAiHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                with _SidecarProcess(
                    Path(tmpdir),
                    f"http://127.0.0.1:{server.server_port}/v1",
                ) as sidecar:
                    sidecar.send({"type": "configure", "apiKey": "test-key"})
                    sidecar.send({
                        "type": "create",
                        "taskId": "task-1",
                        "workspace": str(sidecar.workspace),
                        "prompt": "创建一个文本文件",
                        "model": "test-model",
                        "reasoningEffort": "max",
                    })
                    observed = sidecar.collect()

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
                    self.assertTrue(
                        any(event.get("type") == "task_idle" for event in observed)
                    )
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)

    def test_pet_tool_call_round_trips_through_the_desktop_process(self):
        readiness = office_runtime.runtime_readiness_error()
        if readiness:
            self.skipTest(readiness)

        _drain(_PetToolHandler)
        _PetToolHandler._counter = 0
        server = ThreadingHTTPServer(("127.0.0.1", 0), _PetToolHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                with _SidecarProcess(
                    Path(tmpdir),
                    f"http://127.0.0.1:{server.server_port}/v1",
                ) as sidecar:
                    calls: list[dict] = []

                    def answer_pet_tool(event: dict) -> None:
                        if event.get("type") != "pet_tool_call":
                            return
                        calls.append(event)
                        # 主进程的 OfficeService 在这里执行桌宠指令并回传结果。
                        sidecar.send({
                            "type": "pet_tool_result",
                            "taskId": event.get("taskId"),
                            "callId": event.get("callId"),
                            "ok": True,
                            "message": "已在桌面生成雪豹 2 只。",
                        })

                    sidecar.send({"type": "configure", "apiKey": "test-key"})
                    sidecar.send({
                        "type": "create",
                        "taskId": "task-pet",
                        "workspace": str(sidecar.workspace),
                        "prompt": "给我来两只雪豹",
                        "model": "test-model",
                        "reasoningEffort": "high",
                    })
                    observed = sidecar.collect(on_event=answer_pet_tool)

                    self.assertEqual(len(calls), 1)
                    call = calls[0]
                    self.assertEqual(call["taskId"], "task-pet")
                    self.assertEqual(call["name"], "spawn_snow_leopard")
                    self.assertEqual(call["arguments"], {"count": 2})
                    self.assertTrue(call["callId"])

                    tool_events = [
                        event["event"] for event in observed
                        if event.get("type") == "session_event"
                        and isinstance(event.get("event"), dict)
                    ]
                    self.assertIn(
                        "spawn_snow_leopard",
                        [
                            (item.get("data") or {}).get("name")
                            for item in tool_events
                            if item.get("type") == "tool/call"
                        ],
                    )

                    first_path, first_request = _PetToolHandler.requests.get(timeout=5)
                    _, second_request = _PetToolHandler.requests.get(timeout=5)
                    self.assertEqual(first_path, "/v1/chat/completions")
                    tool_names = [
                        item.get("function", {}).get("name")
                        for item in first_request.get("tools", [])
                        if isinstance(item, dict)
                    ]
                    for name in PET_TOOL_NAMES:
                        self.assertIn(name, tool_names)
                    for name in EXCLUDED_PET_TOOL_NAMES:
                        self.assertNotIn(name, tool_names)
                    snow_leopard = next(
                        item for item in first_request["tools"]
                        if item["function"]["name"] == "spawn_snow_leopard"
                    )
                    self.assertIn(
                        "count", snow_leopard["function"]["parameters"]["properties"]
                    )

                    result_text = json.dumps(second_request.get("messages", []), ensure_ascii=False)
                    self.assertIn("已在桌面生成雪豹 2 只。", result_text)
                    self.assertTrue(
                        any(event.get("type") == "task_idle" for event in observed)
                    )
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
