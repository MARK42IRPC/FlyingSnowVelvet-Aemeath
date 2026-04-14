"""AI 设置面板存储辅助。"""

from __future__ import annotations

import re
from pathlib import Path

from config.shared_storage import ensure_shared_config_ready, get_shared_config_path
from lib.core.logger import get_logger

_logger = get_logger(__name__)


def load_ai_values(default_values: dict) -> dict:
    import config.ollama_config as oc

    values = dict(default_values)
    values.update({
        "api_key": str(oc.API_KEY or ""),
        "force_reply_mode": str(oc.FORCE_REPLY_MODE or ""),
        "api_base_url": str(oc.API_BASE_URL or ""),
        "api_model": str(oc.API_MODEL or ""),
        "yuanbao_login_url": str(getattr(oc, "YUANBAO_FREE_API", {}).get("login_url", values.get("yuanbao_login_url", "")) or ""),
        "yuanbao_free_api_enabled": bool(getattr(oc, "YUANBAO_FREE_API", {}).get("enabled", values.get("yuanbao_free_api_enabled", False))),
        "yuanbao_hy_source": str(getattr(oc, "YUANBAO_FREE_API", {}).get("hy_source", values.get("yuanbao_hy_source", "web")) or ""),
        "yuanbao_hy_user": str(getattr(oc, "YUANBAO_FREE_API", {}).get("hy_user", values.get("yuanbao_hy_user", "")) or ""),
        "yuanbao_x_uskey": str(getattr(oc, "YUANBAO_FREE_API", {}).get("x_uskey", values.get("yuanbao_x_uskey", "")) or ""),
        "yuanbao_agent_id": str(getattr(oc, "YUANBAO_FREE_API", {}).get("agent_id", values.get("yuanbao_agent_id", "")) or ""),
        "yuanbao_chat_id": str(getattr(oc, "YUANBAO_FREE_API", {}).get("chat_id", values.get("yuanbao_chat_id", "")) or ""),
        "yuanbao_remove_conversation": bool(getattr(oc, "YUANBAO_FREE_API", {}).get("should_remove_conversation", values.get("yuanbao_remove_conversation", False))),
        "yuanbao_upload_images": bool(getattr(oc, "YUANBAO_FREE_API", {}).get("upload_images", values.get("yuanbao_upload_images", True))),
        "ollama_base_url": str(oc.OLLAMA.get("base_url", values.get("ollama_base_url", ""))),
        "ollama_model": str(oc.OLLAMA_MODEL or ""),
        "num_gpu": oc.OLLAMA_OPTIONS.get("num_gpu", values.get("num_gpu", -1)),
        "num_thread": oc.OLLAMA_OPTIONS.get("num_thread", values.get("num_thread", 0)),
        "api_temperature": oc.OLLAMA.get("api_temperature", values.get("api_temperature", 0.8)),
        "gsv_auto_start": bool(oc.OLLAMA.get("gsv_auto_start", values.get("gsv_auto_start", True))),
        "gsv_temperature": oc.OLLAMA.get("gsv_temperature", values.get("gsv_temperature", 1.35)),
        "gsv_speed_factor": oc.OLLAMA.get("gsv_speed_factor", values.get("gsv_speed_factor", 1.0)),
        "ai_voice_max_chars": oc.OLLAMA.get("ai_voice_max_chars", values.get("ai_voice_max_chars", 40)),
        "gsv_cache_max_files": oc.OLLAMA.get("gsv_cache_max_files", values.get("gsv_cache_max_files", 20)),
        "memory_context_limit": oc.OLLAMA.get("memory_context_limit", values.get("memory_context_limit", default_values["memory_context_limit"])),
        "api_enable_thinking": bool(oc.OLLAMA.get("api_enable_thinking", values.get("api_enable_thinking", False))),
        "auto_companion_enabled": bool(oc.AUTO_COMPANION.get("enabled", values.get("auto_companion_enabled", True))),
    })
    return values


def save_ai_values(values: dict, default_values: dict) -> None:
    cfg_path = _ollama_config_path()
    text = cfg_path.read_text(encoding="utf-8")
    memory_context_limit_value = values.get("memory_context_limit", default_values["memory_context_limit"])

    text = _replace_assignment(text, "API_KEY", _py_literal(values["api_key"]))
    text = _replace_assignment(text, "FORCE_REPLY_MODE", _py_literal(values["force_reply_mode"]))
    text = _replace_assignment(text, "API_BASE_URL", _py_literal(values["api_base_url"]))
    text = _replace_assignment(text, "API_MODEL", _py_literal(values["api_model"]))
    text = _replace_assignment(text, "OLLAMA_MODEL", _py_literal(values["ollama_model"]))
    text = _replace_named_dict_item(text, "YUANBAO_FREE_API", "enabled", _py_literal(values["yuanbao_free_api_enabled"]))
    text = _replace_named_dict_item(text, "YUANBAO_FREE_API", "login_url", _py_literal(values["yuanbao_login_url"]))
    text = _replace_named_dict_item(text, "YUANBAO_FREE_API", "hy_source", _py_literal(values["yuanbao_hy_source"]))
    text = _replace_named_dict_item(text, "YUANBAO_FREE_API", "hy_user", _py_literal(values["yuanbao_hy_user"]))
    text = _replace_named_dict_item(text, "YUANBAO_FREE_API", "x_uskey", _py_literal(values["yuanbao_x_uskey"]))
    text = _replace_named_dict_item(text, "YUANBAO_FREE_API", "agent_id", _py_literal(values["yuanbao_agent_id"]))
    text = _replace_named_dict_item(text, "YUANBAO_FREE_API", "chat_id", _py_literal(values["yuanbao_chat_id"]))
    text = _replace_named_dict_item(text, "YUANBAO_FREE_API", "should_remove_conversation", _py_literal(values["yuanbao_remove_conversation"]))
    text = _replace_named_dict_item(text, "YUANBAO_FREE_API", "upload_images", _py_literal(values["yuanbao_upload_images"]))

    text = _replace_dict_item(text, "base_url", _py_literal(values["ollama_base_url"]))
    text = _replace_dict_item(text, "api_temperature", _py_literal(values["api_temperature"]))
    text = _replace_or_insert_dict_item_after(text, "gsv_auto_start", _py_literal(values["gsv_auto_start"]), "api_temperature")
    text = _replace_or_insert_dict_item_after(text, "gsv_temperature", _py_literal(values["gsv_temperature"]), "gsv_auto_start")
    text = _replace_or_insert_dict_item_after(text, "gsv_speed_factor", _py_literal(values["gsv_speed_factor"]), "gsv_temperature")
    text = _replace_or_insert_dict_item_after(text, "ai_voice_max_chars", _py_literal(values["ai_voice_max_chars"]), "gsv_speed_factor")
    text = _replace_or_insert_dict_item_after(text, "gsv_cache_max_files", _py_literal(values["gsv_cache_max_files"]), "ai_voice_max_chars")
    text = _replace_or_insert_dict_item_after(text, "memory_context_limit", _py_literal(memory_context_limit_value), "gsv_cache_max_files")
    text = _replace_dict_item(text, "api_enable_thinking", _py_literal(values["api_enable_thinking"]))
    text = _replace_named_dict_item(text, "AUTO_COMPANION", "enabled", _py_literal(values["auto_companion_enabled"]))

    text = _replace_dict_item(text, "num_gpu", _py_literal(values["num_gpu"]))
    text = _replace_dict_item(text, "num_thread", _py_literal(values["num_thread"]))

    _write_text_atomic(cfg_path, text)
    _mirror_config_text_to_shared("ollama_config.py", text)


def apply_ai_runtime(values: dict, default_values: dict) -> None:
    import config.ollama_config as oc

    memory_context_limit_value = values.get("memory_context_limit", default_values["memory_context_limit"])

    oc.API_KEY = values["api_key"]
    oc.FORCE_REPLY_MODE = values["force_reply_mode"]
    oc.API_BASE_URL = values["api_base_url"]
    oc.API_MODEL = values["api_model"]
    oc.YUANBAO_FREE_API["enabled"] = values["yuanbao_free_api_enabled"]
    oc.YUANBAO_FREE_API["login_url"] = values["yuanbao_login_url"]
    oc.YUANBAO_FREE_API["hy_source"] = values["yuanbao_hy_source"]
    oc.YUANBAO_FREE_API["hy_user"] = values["yuanbao_hy_user"]
    oc.YUANBAO_FREE_API["x_uskey"] = values["yuanbao_x_uskey"]
    oc.YUANBAO_FREE_API["agent_id"] = values["yuanbao_agent_id"]
    oc.YUANBAO_FREE_API["chat_id"] = values["yuanbao_chat_id"]
    oc.YUANBAO_FREE_API["should_remove_conversation"] = values["yuanbao_remove_conversation"]
    oc.YUANBAO_FREE_API["upload_images"] = values["yuanbao_upload_images"]
    oc.OLLAMA_MODEL = values["ollama_model"]
    oc.OLLAMA["base_url"] = values["ollama_base_url"]
    oc.OLLAMA["api_temperature"] = values["api_temperature"]
    oc.OLLAMA["gsv_auto_start"] = values["gsv_auto_start"]
    oc.OLLAMA["gsv_temperature"] = values["gsv_temperature"]
    oc.OLLAMA["gsv_speed_factor"] = values["gsv_speed_factor"]
    oc.OLLAMA["ai_voice_max_chars"] = values["ai_voice_max_chars"]
    oc.OLLAMA["gsv_cache_max_files"] = values["gsv_cache_max_files"]
    oc.OLLAMA["memory_context_limit"] = memory_context_limit_value
    oc.OLLAMA["api_enable_thinking"] = values["api_enable_thinking"]
    oc.AUTO_COMPANION["enabled"] = values["auto_companion_enabled"]
    oc.OLLAMA_OPTIONS["num_gpu"] = values["num_gpu"]
    oc.OLLAMA_OPTIONS["num_thread"] = values["num_thread"]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _ollama_config_path() -> Path:
    return _project_root() / "config" / "ollama_config.py"


def _write_text_atomic(path: Path, text: str) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


def _mirror_config_text_to_shared(rel_name: str, text: str) -> None:
    try:
        ensure_shared_config_ready()
        shared_path = get_shared_config_path(rel_name)
        shared_path.parent.mkdir(parents=True, exist_ok=True)
        _write_text_atomic(shared_path, text)
    except Exception as exc:
        _logger.warning("镜像写入外部配置失败(%s): %s", rel_name, exc)


def _py_literal(value) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return str(value)
    return repr(str(value))


def _replace_assignment(text: str, key: str, py_literal: str) -> str:
    pattern = re.compile(rf"(?m)^(\s*{re.escape(key)}\s*=\s*).*(\s*(?:#.*)?)$")
    if not pattern.search(text):
        raise ValueError(f"未找到配置项: {key}")
    return pattern.sub(lambda match: f"{match.group(1)}{py_literal}{match.group(2)}", text, count=1)


def _replace_dict_item(text: str, key: str, py_literal: str) -> str:
    pattern = re.compile(rf"(?m)^(\s*'{re.escape(key)}'\s*:\s*).*(,\s*(?:#.*)?)$")
    if not pattern.search(text):
        raise ValueError(f"未找到字典项: '{key}'")
    return pattern.sub(lambda match: f"{match.group(1)}{py_literal}{match.group(2)}", text, count=1)


def _replace_or_insert_dict_item_after(text: str, key: str, py_literal: str, after_key: str) -> str:
    try:
        return _replace_dict_item(text, key, py_literal)
    except ValueError:
        pass

    newline = "\r\n" if "\r\n" in text else "\n"
    anchor_pattern = re.compile(rf"(?m)^(?P<indent>\s*)'{re.escape(after_key)}'\s*:\s*.*(?:\r?\n|$)")
    match = anchor_pattern.search(text)
    if not match:
        raise ValueError(f"未找到插入锚点字典项: '{after_key}'")

    indent = match.group("indent")
    inserted = f"{indent}'{key}': {py_literal},{newline}"
    return text[: match.end()] + inserted + text[match.end():]


def _replace_named_dict_item(text: str, dict_name: str, key: str, py_literal: str) -> str:
    dict_start = re.search(rf"(?m)^{re.escape(dict_name)}\s*=\s*\{{\s*$", text)
    if not dict_start:
        raise ValueError(f"未找到字典定义: {dict_name}")

    dict_end = re.search(r"(?m)^}\s*$", text[dict_start.end():])
    if not dict_end:
        raise ValueError(f"未找到字典结束: {dict_name}")

    body_start = dict_start.end()
    body_end = body_start + dict_end.start()
    body = text[body_start:body_end]
    body_updated = _replace_dict_item(body, key, py_literal)
    return text[:body_start] + body_updated + text[body_end:]
