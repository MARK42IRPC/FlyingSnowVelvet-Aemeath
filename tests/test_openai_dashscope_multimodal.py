import unittest

import requests

from lib.script.chat.api_client_openai import _ApiClientOpenAIMixin


class _FakeStreamResponse:
    ok = True
    content = b""

    def __init__(self, lines=None, *, status_code=200, error_text=""):
        self.status_code = status_code
        self._lines = lines or [b'data: {"choices":[{"delta":{"content":"ok"}}]}', b'data: [DONE]']
        self._error_text = error_text
        self.ok = status_code < 400

    def iter_lines(self, *args, **kwargs):
        yield from self._lines

    def raise_for_status(self):
        if self.ok:
            return
        error = requests.HTTPError(f"{self.status_code} Error")
        error.response = self
        raise error

    def json(self):
        return {"error": self._error_text}

    @property
    def text(self):
        return self._error_text

    def close(self):
        pass


class _Client(_ApiClientOpenAIMixin):
    def __init__(self, *, response):
        self._active_config = {
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "test-key",
            "model": "qwen-plus",
        }
        self.response = response
        self.requests = []

    def _request_with_proxy_fallback(self, method, url, *, disable_env_proxy, **kwargs):
        self.requests.append({"method": method, "url": url, **kwargs})
        return self.response


class DashScopeMultimodalTests(unittest.TestCase):
    def test_qwen_without_vl_marker_still_sends_image_payload(self):
        client = _Client(response=_FakeStreamResponse())

        result = client._openai_chat_api(
            "看图说话",
            "",
            images=["data:image/png;base64,AA=="],
        )

        self.assertEqual(result, "ok")
        self.assertTrue(client.requests)
        messages = client.requests[0]["json"]["messages"]
        user_content = messages[-1]["content"]
        self.assertTrue(any(block.get("type") == "image_url" for block in user_content))

    def test_dashscope_image_rejection_reports_after_attempt(self):
        client = _Client(response=_FakeStreamResponse(
            status_code=400,
            error_text="model does not support image input",
        ))

        with self.assertRaisesRegex(RuntimeError, "已尝试携带图片请求"):
            client._openai_chat_api(
                "看图说话",
                "",
                images=["data:image/png;base64,AA=="],
            )

        self.assertTrue(client.requests)


if __name__ == "__main__":
    unittest.main()
