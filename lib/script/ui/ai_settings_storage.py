"""AI settings persistence and runtime application."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from config.user_settings import save_section
from config.user_storage_paths import get_user_secrets_dir
from lib.core.logger import get_logger

_logger = get_logger(__name__)

_SECRET_KEYS = (
    "api_key",
    "yuanbao_hy_user",
    "yuanbao_x_uskey",
    "office_api_key",
)


def load_ai_values(default_values: dict) -> dict:
    import config.ollama_config as oc

    disk_secrets = _read_local_ai_secrets()
    values = dict(default_values)
    values.update({
        "api_key": str(disk_secrets.get("api_key", oc.API_KEY) or ""),
        "force_reply_mode": str(oc.FORCE_REPLY_MODE or ""),
        "welfare_intelligence_boost": bool(oc.WELFARE_INTELLIGENCE_BOOST),
        "api_base_url": str(oc.API_BASE_URL or ""),
        "api_model": str(oc.API_MODEL or ""),
        "yuanbao_login_url": str(oc.YUANBAO_FREE_API.get("login_url", "") or ""),
        "yuanbao_hy_source": str(oc.YUANBAO_FREE_API.get("hy_source", "web") or ""),
        "yuanbao_hy_user": str(disk_secrets.get("yuanbao_hy_user", oc.YUANBAO_FREE_API.get("hy_user", "")) or ""),
        "yuanbao_x_uskey": str(disk_secrets.get("yuanbao_x_uskey", oc.YUANBAO_FREE_API.get("x_uskey", "")) or ""),
        "yuanbao_agent_id": str(oc.YUANBAO_FREE_API.get("agent_id", "") or ""),
        "ollama_base_url": str(oc.OLLAMA.get("base_url", "")),
        "ollama_model": str(oc.OLLAMA_MODEL or ""),
        "num_gpu": oc.OLLAMA_OPTIONS.get("num_gpu", -1),
        "num_thread": oc.OLLAMA_OPTIONS.get("num_thread", 0),
        "api_temperature": oc.OLLAMA.get("api_temperature", 1.35),
        "model_vision": oc.OLLAMA.get("model_vision", 0),
        "gsv_auto_start": bool(oc.OLLAMA.get("gsv_auto_start", False)),
        "gsv_gpu_hybrid": bool(oc.OLLAMA.get("gsv_gpu_hybrid", False)),
        "gsv_nvidia_cuda_acceleration": bool(
            oc.OLLAMA.get("gsv_nvidia_cuda_acceleration", False)
        ),
        "gsv_temperature": oc.OLLAMA.get("gsv_temperature", 1.0),
        "gsv_top_k": oc.OLLAMA.get("gsv_top_k", 15),
        "gsv_top_p": oc.OLLAMA.get("gsv_top_p", 1.0),
        "gsv_repetition_penalty": oc.OLLAMA.get("gsv_repetition_penalty", 1.35),
        "gsv_speed_factor": oc.OLLAMA.get("gsv_speed_factor", 1.0),
        "gsv_text_split_method": oc.OLLAMA.get("gsv_text_split_method", "cut5"),
        "gsv_fragment_interval": oc.OLLAMA.get("gsv_fragment_interval", 0.3),
        "gsv_seed": oc.OLLAMA.get("gsv_seed", -1),
        "gsv_max_steps": oc.OLLAMA.get("gsv_max_steps", 500),
        "ai_voice_max_chars": oc.OLLAMA.get(
            "ai_voice_max_chars",
            oc.AI_VOICE_MAX_CHARS_DEFAULT,
        ),
        "gsv_cache_max_files": oc.OLLAMA.get("gsv_cache_max_files", 20),
        "memory_context_limit": oc.OLLAMA.get("memory_context_limit", 12),
        "memory_recall_count": oc.OLLAMA.get("memory_recall_count", 30),
        "api_enable_thinking": bool(oc.OLLAMA.get("api_enable_thinking", False)),
        "auto_companion_enabled": bool(oc.AUTO_COMPANION.get("enabled", True)),
        "auto_companion_interval_minutes": int(oc.AUTO_COMPANION.get("interval_minutes", 2)),
        "office_use_independent_api": bool(oc.OFFICE_MODE.get("use_independent_api", False)),
        "office_api_key": str(disk_secrets.get("office_api_key", oc.OFFICE_MODE.get("api_key", "")) or ""),
        "office_api_base_url": str(oc.OFFICE_MODE.get("api_base_url", "") or ""),
        "office_api_model": str(oc.OFFICE_MODE.get("api_model", "gpt-5.4") or ""),
        "office_warmup_on_startup": bool(oc.OFFICE_MODE.get("warmup_on_startup", True)),
    })
    return values


def save_ai_values(values: dict, default_values: dict) -> None:
    import config.ollama_config as oc

    setting_defaults = oc.get_ai_setting_defaults()
    ordinary_values = {
        key: values.get(key, default)
        for key, default in setting_defaults.items()
    }
    _write_local_ai_secrets(values)
    save_section("ai", ordinary_values, setting_defaults)


def apply_ai_runtime(values: dict, default_values: dict) -> None:
    import config.ollama_config as oc

    memory_context_limit = values.get("memory_context_limit", default_values["memory_context_limit"])
    oc.API_KEY = values["api_key"]
    oc.FORCE_REPLY_MODE = values["force_reply_mode"]
    oc.WELFARE_INTELLIGENCE_BOOST = values["welfare_intelligence_boost"]
    oc.API_BASE_URL = values["api_base_url"]
    oc.API_MODEL = values["api_model"]
    oc.YUANBAO_FREE_API["login_url"] = values["yuanbao_login_url"]
    oc.YUANBAO_FREE_API["hy_source"] = values["yuanbao_hy_source"]
    oc.YUANBAO_FREE_API["hy_user"] = values["yuanbao_hy_user"]
    oc.YUANBAO_FREE_API["x_uskey"] = values["yuanbao_x_uskey"]
    oc.YUANBAO_FREE_API["agent_id"] = values["yuanbao_agent_id"]
    oc.OLLAMA_MODEL = values["ollama_model"]
    oc.OLLAMA["base_url"] = values["ollama_base_url"]
    oc.OLLAMA["api_temperature"] = values["api_temperature"]
    oc.OLLAMA["model_vision"] = values["model_vision"]
    oc.OLLAMA["gsv_auto_start"] = values["gsv_auto_start"]
    oc.OLLAMA["gsv_gpu_hybrid"] = values["gsv_gpu_hybrid"]
    oc.OLLAMA["gsv_nvidia_cuda_acceleration"] = values.get(
        "gsv_nvidia_cuda_acceleration", False
    )
    oc.OLLAMA["gsv_temperature"] = values["gsv_temperature"]
    oc.OLLAMA["gsv_top_k"] = values["gsv_top_k"]
    oc.OLLAMA["gsv_top_p"] = values["gsv_top_p"]
    oc.OLLAMA["gsv_repetition_penalty"] = values["gsv_repetition_penalty"]
    oc.OLLAMA["gsv_speed_factor"] = values["gsv_speed_factor"]
    oc.OLLAMA["gsv_text_split_method"] = values["gsv_text_split_method"]
    oc.OLLAMA["gsv_fragment_interval"] = values["gsv_fragment_interval"]
    oc.OLLAMA["gsv_seed"] = values["gsv_seed"]
    oc.OLLAMA["gsv_max_steps"] = values["gsv_max_steps"]
    oc.OLLAMA["ai_voice_max_chars"] = values["ai_voice_max_chars"]
    oc.OLLAMA["gsv_cache_max_files"] = values["gsv_cache_max_files"]
    oc.OLLAMA["memory_context_limit"] = memory_context_limit
    oc.OLLAMA["memory_recall_count"] = values["memory_recall_count"]
    oc.OLLAMA["api_enable_thinking"] = values["api_enable_thinking"]
    oc.AUTO_COMPANION["enabled"] = values["auto_companion_enabled"]
    interval_minutes = int(values["auto_companion_interval_minutes"])
    oc.AUTO_COMPANION["interval_minutes"] = interval_minutes
    oc.AUTO_COMPANION["interval_ms"] = (interval_minutes * 60000, interval_minutes * 60000)
    oc.OLLAMA_OPTIONS["num_gpu"] = values["num_gpu"]
    oc.OLLAMA_OPTIONS["num_thread"] = values["num_thread"]
    oc.OFFICE_MODE["use_independent_api"] = values["office_use_independent_api"]
    oc.OFFICE_MODE["api_key"] = values["office_api_key"]
    oc.OFFICE_MODE["api_base_url"] = values["office_api_base_url"]
    oc.OFFICE_MODE["api_model"] = values["office_api_model"]
    oc.OFFICE_MODE["warmup_on_startup"] = values["office_warmup_on_startup"]

    try:
        from lib.script.chat.ollama import get_ollama_manager

        get_ollama_manager().reload_config()
    except Exception as exc:
        _logger.warning("热重载 AI 配置失败: %s", exc)

    try:
        from lib.core.event.center import Event, EventType, get_event_center

        get_event_center().publish(Event(EventType.CONFIG_UPDATED, {
            "source": "ai",
            "values": dict(values),
        }))
    except Exception as exc:
        _logger.debug("发布 AI 配置热重载事件失败: %s", exc)


def _local_ai_secret_path() -> Path:
    return get_user_secrets_dir("ai.json")


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temp_path.write_text(text, encoding="utf-8")
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass


def _write_local_ai_secrets(values: dict) -> None:
    payload = {
        key: str(values.get(key, "") or "").strip()
        for key in _SECRET_KEYS
    }
    _write_text_atomic(
        _local_ai_secret_path(),
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def _read_local_ai_secrets() -> dict[str, str]:
    try:
        payload = json.loads(_local_ai_secret_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        key: str(payload.get(key, "") or "").strip()
        for key in _SECRET_KEYS
        if key in payload
    }
