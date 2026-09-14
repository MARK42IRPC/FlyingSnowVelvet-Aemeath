"""AI 配置的默认值表。

这张表原先内联在 `ai_settings_panel` 里。办公模式独立成页面后，办公页面也要拿同一份
默认值去合并保存（`save_office_values` 需要它补齐没在办公页面上出现的字段），于是单独
放一个模块：设置面板与办公页面引用同一份，避免两边各写一张表慢慢漂移。
"""

from __future__ import annotations

from config.ollama_config import AI_VOICE_MAX_CHARS_DEFAULT

#: AI 设置面板与办公页面共用的默认值。
AI_DEFAULT_VALUES = {
    "api_key": "",
    "force_reply_mode": "1",
    "welfare_intelligence_boost": False,
    "api_base_url": "",
    "api_model": "gpt-5.4",
    "ollama_base_url": "http://localhost:11434",
    "ollama_model": "qwen2.5",
    "num_gpu": -1,
    "num_thread": 0,
    "api_temperature": 1.35,
    "model_vision": 0,
    "gsv_auto_start": False,
    "gsv_gpu_hybrid": False,
    "gsv_nvidia_cuda_acceleration": False,
    "gsv_temperature": 1.35,
    "gsv_top_k": 15,
    "gsv_top_p": 1.0,
    "gsv_repetition_penalty": 1.6,
    "gsv_speed_factor": 1.1,
    "gsv_text_split_method": "cut0",
    "gsv_fragment_interval": 0.3,
    "gsv_seed": -1,
    "ai_voice_max_chars": AI_VOICE_MAX_CHARS_DEFAULT,
    "gsv_cache_max_files": 20,
    "memory_context_limit": 12,
    "memory_recall_count": 30,
    "api_enable_thinking": False,
    "auto_companion_enabled": True,
    "auto_companion_interval_minutes": 2,
    "office_use_independent_api": False,
    "office_api_key": "",
    "office_api_base_url": "",
    "office_api_model": "gpt-5.4",
    "office_warmup_on_startup": True,
}


__all__ = ["AI_DEFAULT_VALUES"]
