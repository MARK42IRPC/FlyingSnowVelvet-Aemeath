import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = PROJECT_ROOT / "services" / "yuanbao-free-api"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from src.utils.chat import process_response_stream


class _FakeResponse:
    def __init__(self, lines):
        self._lines = list(lines)

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class YuanbaoFreeApiStreamTests(unittest.IsolatedAsyncioTestCase):
    async def _collect(self, lines):
        items = []
        async for item in process_response_stream(_FakeResponse(lines), "deepseek-v3"):
            items.append(item)
        return items

    async def test_process_response_stream_accepts_raw_json_lines(self):
        items = await self._collect(
            [
                '{"type":"text","msg":"你"}',
                '{"type":"text","msg":"你好"}',
                '{"stopReason":"stop"}',
            ]
        )

        self.assertEqual(json.loads(items[0])["choices"][0]["delta"]["content"], "你")
        self.assertEqual(json.loads(items[1])["choices"][0]["delta"]["content"], "好")
        self.assertEqual(json.loads(items[2])["choices"][0]["finish_reason"], "stop")
        self.assertEqual(items[3], "[DONE]")

    async def test_process_response_stream_accepts_sse_and_multiline_payloads(self):
        items = await self._collect(
            [
                "event: message",
                'data: {"type":"text",',
                'data: "msg":"晚安"}',
                "",
                "data: [DONE]",
            ]
        )

        self.assertEqual(json.loads(items[0])["choices"][0]["delta"]["content"], "晚安")
        self.assertEqual(json.loads(items[1])["choices"][0]["finish_reason"], "stop")
        self.assertEqual(items[2], "[DONE]")

    async def test_process_response_stream_ignores_speech_type_events(self):
        items = await self._collect(
            [
                'data: {"type":"text"}',
                "",
                "event: speech_type",
                "data: status",
                "",
                "event: speech_type",
                "data: text",
                "",
                'data: {"type":"text","msg":"收到"}',
                "",
                'data: {"type":"tips","status":0}',
                "",
                "data: [plugin: ]",
                "",
                "data: [MSGINDEX:2]",
                "",
                'data: {"type":"meta","stopReason":"stop"}',
                "",
                "data: [TRACEID:94e8eb1cf9ac8f2e65f12560abe6042d]",
                "",
                "data: [DONE]",
                "",
            ]
        )

        self.assertEqual(len(items), 3)
        self.assertEqual(json.loads(items[0])["choices"][0]["delta"]["content"], "收到")
        self.assertEqual(json.loads(items[1])["choices"][0]["finish_reason"], "stop")
        self.assertEqual(items[2], "[DONE]")


if __name__ == "__main__":
    unittest.main()
