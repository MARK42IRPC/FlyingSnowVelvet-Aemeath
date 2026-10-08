"""OpenAI 兼容接口的地址补全与模型列表解析（后端中立）。

这些函数原先住在 `lib/script/ui/office_mode_settings.py`：它们只做地址补全、/models
端点换算、模型列表解析与一次 HTTP 请求，既不碰 Qt 也不碰控件，却因为和控件同一个
文件而一直待在 `frozen_ui_qt_importers` 的待迁清单里。现在按「纯能力先抽离」的次序
（`doc/render层边界契约.md` 第 34 节批次 1）搬到 `lib/core/services/`，
`ai_settings_panel.py` 与 `office_mode_settings.py` 都从这里取用，两边不会再各写
一份实现、慢慢漂移。

`MANUAL_API_PROVIDER_PRESETS` 是同一份产品事实（常用提供商预设表），一并放在这里。

本机 DeepSeek Harness 的探测留在了 `lib/script/ui/office_mode_settings.py`：它必须
import 产品包（`lib.script.office`），而以 `lib/core` 为根的方向是单向的，产品包只能
住在 `lib/script` 一侧。

本模块不 import `PyQt5`、不 import `lib.script`。
"""

from __future__ import annotations

import re

import requests

from lib.core.services.network_policy import API_TIMEOUT_SECS

MANUAL_API_PROVIDER_PRESETS = (
    ("自定义地址", ""),
    ("OpenAI", "https://api.openai.com/v1"),
    ("DeepSeek", "https://api.deepseek.com/v1"),
    ("Kimi", "https://api.moonshot.cn/v1"),
    ("智谱 AI", "https://open.bigmodel.cn/api/paas/v4"),
    ("阿里云百炼", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    ("硅基流动", "https://api.siliconflow.cn/v1"),
    ("OpenRouter", "https://openrouter.ai/api/v1"),
)


def normalize_api_base_url(raw_url: object) -> str:
    """补全 OpenAI 兼容地址的协议，保留用户填写的路径。"""
    text = str(raw_url or "").strip()
    if not text:
        return ""
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
        return text.rstrip("/")
    if text.startswith("//"):
        return f"https:{text}".rstrip("/")

    host = text.split("/", 1)[0].lower()
    is_local = (
        host == "localhost"
        or host.startswith("localhost:")
        or host.startswith("127.")
        or host.startswith("0.0.0.0")
        or host.startswith("[::1]")
        or host == "::1"
    )
    scheme = "http" if is_local else "https"
    return f"{scheme}://{text}".rstrip("/")


def manual_api_models_url(base_url: object) -> str:
    """把用户填的基地址换算成 /models 端点；填了完整端点也能还原。"""
    root = normalize_api_base_url(base_url).rstrip("/")
    suffix = "/chat/completions"
    if root.lower().endswith(suffix):
        root = root[: -len(suffix)].rstrip("/")
    return f"{root}/models" if root else ""


def parse_api_models(payload: object) -> list[str]:
    """解析 OpenAI 兼容的 /models 响应，缺失或形状不对就报错。"""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ValueError("接口没有返回兼容的模型列表")
    models = {
        str(item.get("id", "")).strip()
        for item in data
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    }
    return sorted(models, key=str.casefold)


def fetch_api_models(base_url: object, api_key: object) -> list[str]:
    """同步探测模型列表；调用方负责放到 IO 线程里执行。"""
    models_url = manual_api_models_url(base_url)
    if not models_url:
        raise ValueError("请先填写接口地址")
    key = str(api_key or "").strip()
    if not key:
        raise ValueError("请先填写接口密钥")
    response = requests.get(
        models_url,
        headers={"Authorization": f"Bearer {key}"},
        timeout=API_TIMEOUT_SECS,
    )
    response.raise_for_status()
    models = parse_api_models(response.json())
    if not models:
        raise ValueError("接口未返回可用模型")
    return models


__all__ = [
    "MANUAL_API_PROVIDER_PRESETS",
    "fetch_api_models",
    "manual_api_models_url",
    "normalize_api_base_url",
    "parse_api_models",
]
