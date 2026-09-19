"""AI 配置的默认值表。

这张表原先内联在 `ai_settings_panel` 里，办公模式独立成页面后又拆成单独模块：设置面板与
办公页面引用同一份，避免两边各写一张表慢慢漂移。

**默认值本身不在这里维护。** 唯一真源是 `config/ollama_config.py` 的
`_AI_SETTING_DEFAULTS`（由 `get_ai_setting_defaults()` 导出）。这里只做两处归位：

- 去掉 `office_backend`：办公后端由 `ai_settings_storage.OFFICE_VALUE_KEYS` 管理，
  不属于 AI 面板与办公页共用的这张表，与它原先的键集保持一致。
- 补上 `api_key`：密钥不入 config，出厂为空，真实值由 `user/secrets/ai.json` 提供。

改任一默认值只需改 `config/ollama_config.py` 一处，两张表不会再各走各的。
"""

from __future__ import annotations

from config.ollama_config import get_ai_setting_defaults

#: 从 config 归位得到的默认值键集之外的键（密钥不在 config 里）。
_LOCAL_ONLY_DEFAULTS = {
    "api_key": "",
}

#: config 里存在、但不属于本表的键：办公后端归办公存储层管理。
_EXCLUDED_KEYS = ("office_backend",)


def build_ai_default_values() -> dict:
    """按 config 的当前默认值构造 AI 设置面板与办公页共用的默认值表。"""
    values = get_ai_setting_defaults()
    for key in _EXCLUDED_KEYS:
        values.pop(key, None)
    values.update(_LOCAL_ONLY_DEFAULTS)
    return values


#: AI 设置面板与办公页面共用的默认值。
AI_DEFAULT_VALUES = build_ai_default_values()


__all__ = ["AI_DEFAULT_VALUES", "build_ai_default_values"]
