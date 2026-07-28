"""AI 设置面板校验辅助。"""

import math
from urllib.parse import urlparse


def is_valid_http_url(text: str) -> bool:
    try:
        parsed = urlparse(text)
    except Exception:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _hostname(text: str) -> str:
    try:
        return (urlparse(text).hostname or "").strip().lower()
    except Exception:
        return ""


def validate_ai_values(values: dict) -> None:
    force_mode = str(values.get("force_reply_mode", "")).strip()
    welfare_intelligence_boost = values.get("welfare_intelligence_boost")
    api_base_url = str(values.get("api_base_url", "") or "").strip()
    yuanbao_login_url = str(values.get("yuanbao_login_url", "") or "").strip()
    yuanbao_free_api_enabled = bool(values.get("yuanbao_free_api_enabled", False))
    yuanbao_hy_source = str(values.get("yuanbao_hy_source", "") or "").strip()
    yuanbao_hy_user = str(values.get("yuanbao_hy_user", "") or "").strip()
    yuanbao_x_uskey = str(values.get("yuanbao_x_uskey", "") or "").strip()
    yuanbao_agent_id = str(values.get("yuanbao_agent_id", "") or "").strip()
    ollama_base_url = str(values.get("ollama_base_url", "") or "").strip()
    ollama_model = str(values.get("ollama_model", "") or "").strip()
    num_gpu = values.get("num_gpu")
    num_thread = values.get("num_thread")
    api_temperature = values.get("api_temperature")
    model_vision = values.get("model_vision")
    gsv_temperature = values.get("gsv_temperature")
    gsv_speed_factor = values.get("gsv_speed_factor")
    gsv_auto_start = values.get("gsv_auto_start")
    api_enable_thinking = values.get("api_enable_thinking")
    auto_companion_enabled = values.get("auto_companion_enabled")
    ai_voice_max_chars = values.get("ai_voice_max_chars")
    gsv_cache_max_files = values.get("gsv_cache_max_files")
    memory_context_limit = values.get("memory_context_limit")
    memory_recall_count = values.get("memory_recall_count")

    if force_mode not in ("0", "1", "2", "3", "4"):
        raise ValueError("回复模式值无效")
    if not isinstance(welfare_intelligence_boost, bool):
        raise ValueError("福利 API 智力提升开关无效")

    if force_mode == "0":
        if not str(values.get("api_key", "") or "").strip():
            raise ValueError("手动 API 模式下接口密钥不能为空")
        if not api_base_url:
            raise ValueError("手动 API 模式下接口地址不能为空")
        if not str(values.get("api_model", "") or "").strip():
            raise ValueError("手动 API 模式下接口模型不能为空")

    if api_base_url and not is_valid_http_url(api_base_url):
        raise ValueError("接口地址必须是有效的 http/https 地址")

    if yuanbao_login_url and not is_valid_http_url(yuanbao_login_url):
        raise ValueError("元宝登录页地址必须是有效的 http/https 地址")

    if yuanbao_free_api_enabled:
        if not yuanbao_agent_id:
            raise ValueError("启用 YuanBao-Free-API 时，agent_id 不能为空")
        if yuanbao_x_uskey and any(ch.isspace() for ch in yuanbao_x_uskey):
            raise ValueError("x_uskey 不能包含空白字符")

    if force_mode == "2":
        if not ollama_base_url:
            raise ValueError("Ollama地址不能为空")
        if not is_valid_http_url(ollama_base_url):
            raise ValueError("Ollama地址必须是有效的 http/https 地址")
        if not ollama_model:
            raise ValueError("Ollama模型不能为空")

    if isinstance(num_gpu, bool) or not isinstance(num_gpu, int):
        raise ValueError("推理模式值无效")
    if num_gpu not in (-1, 0) and num_gpu < 1:
        raise ValueError("推理模式值无效")

    if isinstance(num_thread, bool) or not isinstance(num_thread, int):
        raise ValueError("CPU线程数必须是整数")
    if num_thread < 0 or num_thread > 1024:
        raise ValueError("CPU线程数范围应为 0~1024")

    if isinstance(api_temperature, bool) or not isinstance(api_temperature, (int, float)):
        raise ValueError("采样温度必须是数字")
    try:
        temp = float(api_temperature)
    except Exception as e:
        raise ValueError("采样温度必须是数字") from e
    if not math.isfinite(temp):
        raise ValueError("采样温度必须是有限数字")
    if not (0.0 <= temp <= 2.0):
        raise ValueError("采样温度范围应为 0~2")

    if isinstance(model_vision, bool) or not isinstance(model_vision, int):
        raise ValueError("模型视力必须是整数")
    if not (0 <= model_vision <= 100):
        raise ValueError("模型视力范围应为 0~100")

    if isinstance(gsv_temperature, bool) or not isinstance(gsv_temperature, (int, float)):
        raise ValueError("GSV服务温度必须是数字")
    try:
        gsv_temp = float(gsv_temperature)
    except Exception as e:
        raise ValueError("GSV服务温度必须是数字") from e
    if not math.isfinite(gsv_temp):
        raise ValueError("GSV服务温度必须是有限数字")
    if not (0.0 <= gsv_temp <= 2.0):
        raise ValueError("GSV服务温度范围应为 0~2")

    if isinstance(gsv_speed_factor, bool) or not isinstance(gsv_speed_factor, (int, float)):
        raise ValueError("GSV语速必须是数字")
    try:
        speed = float(gsv_speed_factor)
    except Exception as e:
        raise ValueError("GSV语速必须是数字") from e
    if not math.isfinite(speed):
        raise ValueError("GSV语速必须是有限数字")
    if not (0.5 <= speed <= 2.0):
        raise ValueError("GSV语速范围应为 0.5~2.0")

    if not isinstance(gsv_auto_start, bool):
        raise ValueError("GSV自动启用开关无效")

    if isinstance(ai_voice_max_chars, bool) or not isinstance(ai_voice_max_chars, int):
        raise ValueError("GSV语音字数限制必须是整数")
    if not (20 <= ai_voice_max_chars <= 80):
        raise ValueError("GSV语音字数限制范围应为 20~80")

    if isinstance(gsv_cache_max_files, bool) or not isinstance(gsv_cache_max_files, int):
        raise ValueError("GSV缓存上限必须是整数")
    if not (1 <= gsv_cache_max_files <= 128):
        raise ValueError("GSV缓存上限范围应为 1~128")

    if isinstance(memory_context_limit, bool) or not isinstance(memory_context_limit, int):
        raise ValueError("记忆上下文条数必须是整数")
    if not (0 <= memory_context_limit <= 48):
        raise ValueError("记忆上下文条数范围应为 0~48")

    if isinstance(memory_recall_count, bool) or not isinstance(memory_recall_count, int):
        raise ValueError("回忆提取条数必须是整数")
    if not (5 <= memory_recall_count <= 50):
        raise ValueError("回忆提取条数范围应为 5~50")

    if not isinstance(api_enable_thinking, bool):
        raise ValueError("思考模式配置无效")
    if not isinstance(auto_companion_enabled, bool):
        raise ValueError("自动陪伴配置无效")
