from __future__ import annotations

import os

# Ollama / OpenAI 兼容 API 配置文件

# ============================================================
# API 配置-需要设置apikey,api服务器,api使用模型
# ============================================================


def _load_env_api_key() -> tuple[str, str]:
    """读取环境变量中的 API Key（如配置，优先启用）。"""
    env_candidates = (
        'FLYINGSNOWVELVET_API_KEY',
        'FLYINGSNOW_API_KEY',
        'OPENAI_API_KEY',
    )
    for name in env_candidates:
        value = (os.environ.get(name) or '').strip()
        if value:
            return value, name
    return '', ''


_ENV_API_KEY, _ENV_API_KEY_SOURCE = _load_env_api_key()

# API Key 配置（优先使用）
# 如果设置了有效的 API Key，将使用 OpenAI 兼容 API 而非本地 Ollama。
# 默认保持为空，请优先通过环境变量或 AI 设置面板注入，避免把密钥提交到仓库。
API_KEY = 'sss'

# 回复模式强制开关（留空=默认检索顺序）
# 0: 强制配置文件 API_KEY
# 2: 强制本地 Ollama
# 3: 强制规则回复
FORCE_REPLY_MODE = '4'

# OpenAI 兼容 API 基础地址（使用 API Key 时生效）
# 常见兼容服务地址：
# - OpenAI:     'https://api.openai.com/v1'
# - DeepSeek:   'https://api.deepseek.com/v1'
# - Moonshot:   'https://api.moonshot.cn/v1'
# - 智谱AI:     'https://open.bigmodel.cn/api/paas/v4'
# - 通义千问:   'https://dashscope.aliyuncs.com/compatible-mode/v1'
API_BASE_URL = 'http://127.0.0.1:8000/v1'

# 使用 API Key 时的模型名称
# 例如: 'gpt-4o-mini', 'deepseek-chat', 'moonshot-v1-8k'
API_MODEL = 'deepseek-v3'

# YuanBao-Free-API 兼容配置（参考 chenwr727/yuanbao-free-api）
# 当前集成的是仓库内置的本地中转服务：
# - 服务认证使用 API Key（Authorization: Bearer <API_KEY>）
# - 启动后会自动生成登录二维码图片并等待扫码
# - chat_id / 图片上传等能力仍沿用 OpenAI 兼容接口
YUANBAO_FREE_API = {
    'enabled': True,
    'login_url': 'https://yuanbao.tencent.com/chat/naQivTmsDa',
    'hy_source': 'web',
    'hy_user': '',
    'x_uskey': '',
    'agent_id': 'naQivTmsDa',
    'chat_id': '',
    'should_remove_conversation': True,
    'upload_images': True,
}

# ============================================================
# Ollama 本地服务配置（API Key 为空或无效时使用）
# ============================================================

OLLAMA = {
    'base_url':            'http://localhost:11434',  # Ollama API 基础地址
    'ping_interval_ms':    5000,    # Ping 定时器间隔（毫秒）
    'stream_max_secs':     30,      # 单次流式请求最大持续时间（秒）
    'api_stream_max_secs': 90,      # 外部 API 模式流式最大持续时间（秒）
    'api_connect_timeout': 6,       # 外部 API 连接超时（秒）
    'api_read_timeout':    15,      # 外部 API 首包/分片读取超时（秒）
    'api_retry_times':     2,       # 外部 API 失败重试次数（含首次）
    'api_retry_backoff':   0.8,     # 外部 API 重试退避基数（秒）
    'api_disable_env_proxy': False, # 默认遵循系统代理配置；设为 True 时优先忽略
    'api_temperature':     0.8,      # 外部 API 采样温度（0~2）
    'gsv_auto_start':      True,     # 启用 GSV 语音模块；关闭后不预热，也不响应文本语音请求
    'gsv_temperature':     1.35,      # GSV 文本转语音采样温度（0~2）
    'gsv_speed_factor':    1.0,      # GSV 文本转语音语速（0.5~2.0）
    'ai_voice_max_chars':  80,       # GSV 语音合成最大文本长度（20~80）
    'gsv_cache_max_files': 20,       # GSV 语音缓存最大保存条数（1~128）
    'memory_context_limit': 12,      # 发送给 AI 时附带的 recent memory 条数（0~48，0 = 不附带）
    'api_enable_thinking': False,   # 外部 API 思考模式（Qwen3.5-plus 默认 True；关闭可提升可见流式与命令稳定性）
    'api_thinking_budget': 0,       # >0 时限制思考 token；0 表示不指定
    'pull_emit_interval':  2.0,     # 下载进度气泡更新间隔（秒）
    'request_timeout':     60,      # HTTP 请求超时（秒）
}

# Ollama 模型配置
OLLAMA_MODEL = 'qwen2.5'

# Ollama 推理参数（直接映射到 API 请求的 options 字段）
#
# num_gpu  =  0 : 禁用 GPU，全量使用 CPU + 内存（低显存 / 无独显机器首选）
#          = -1 : 由 Ollama 自动分配（显存充足时自动利用 GPU 加速）
#          >  0 : 将指定层数卸载到 GPU，其余留在内存（显存有限时的折中方案）
#
# num_thread = 0 : 由 Ollama 自动决定（通常等于物理核心数）
#            > 0 : 手动指定 CPU 线程数，推荐设为物理核心数
OLLAMA_OPTIONS = {
    'num_gpu': -1,       # 默认纯 CPU 模式，对低端/无独显设备最友好
    'num_thread': 0,     # CPU 线程数，0 = 自动
}

# 自动陪伴配置（外部 API 模式）
AUTO_COMPANION = {
    'enabled': True,                 # 是否开启自动陪伴
    'interval_ms': (120000, 360000), # 自动陪伴间隔（毫秒），2~6 分钟
}

# ============================================================
# 通用配置
# ============================================================

# 人格文件路径（空则使用默认 resc/persona.txt）
PERSONA_FILE = ''

# ============================================================
# 辅助函数
# ============================================================

def is_api_key_configured() -> bool:
    """检查当前激活配置是否为外部 API 模式。"""
    return get_active_config().get('api_type') == 'openai_compatible'


def _normalize_force_mode(value) -> str:
    """将强制模式归一化到 '', '0', '2', '3', '4'。"""
    text = '' if value is None else str(value).strip()
    return text if text in ('', '0', '2', '3', '4') else ''


def _is_yuanbao_web_ready(api_key: str) -> bool:
    """判断当前 YuanBao-Free-API 配置是否足以优先发起请求。"""
    options = YUANBAO_FREE_API if isinstance(YUANBAO_FREE_API, dict) else {}
    if not bool(options.get('enabled', False)):
        return False
    if not (API_BASE_URL or '').strip() or not (API_MODEL or '').strip():
        return False
    if not str(api_key or '').strip():
        return False
    if not str(options.get('agent_id', '') or '').strip():
        return False
    return True


def _build_openai_config(api_key: str, key_source: str, force_mode: str) -> dict:
    """构造 OpenAI 兼容模式配置。"""
    return {
        'api_type': 'openai_compatible',
        'base_url': API_BASE_URL,
        'model': API_MODEL,
        'api_key': api_key,
        'key_source': key_source,
        'force_mode': force_mode,
        'strict_mode': bool(force_mode),
        'error': '',
        'provider_options': {
            'yuanbao_free_api': dict(YUANBAO_FREE_API),
        },
    }


def _build_ollama_config(force_mode: str) -> dict:
    """构造本地 Ollama 模式配置。"""
    return {
        'api_type': 'ollama',
        'base_url': OLLAMA['base_url'],
        'model': OLLAMA_MODEL,
        'api_key': None,
        'options': OLLAMA_OPTIONS,
        'key_source': '',
        'force_mode': force_mode,
        'strict_mode': bool(force_mode),
        'error': '',
    }


def _build_rule_reply_config(force_mode: str) -> dict:
    """构造规则回复模式配置。"""
    return {
        'api_type': 'rule_reply',
        'base_url': '',
        'model': '',
        'api_key': None,
        'options': OLLAMA_OPTIONS,
        'key_source': '',
        'force_mode': force_mode,
        'strict_mode': bool(force_mode),
        'error': '',
    }


def _build_error_config(force_mode: str, error_text: str) -> dict:
    """构造错误配置（强制模式失败时使用）。"""
    return {
        'api_type': 'error',
        'base_url': '',
        'model': '',
        'api_key': None,
        'options': OLLAMA_OPTIONS,
        'key_source': '',
        'force_mode': force_mode,
        'strict_mode': True,
        'error': error_text,
    }


def get_active_config() -> dict:
    """
    获取当前活跃的配置。

    Returns:
        包含 api_type, base_url, model 等信息的字典
    """
    config_api_key = (API_KEY or '').strip()
    env_api_key = (_ENV_API_KEY or '').strip()
    env_source = f'env:{_ENV_API_KEY_SOURCE or "FLYINGSNOWVELVET_API_KEY"}'
    force_mode = _normalize_force_mode(FORCE_REPLY_MODE)
    preferred_api_key = config_api_key or env_api_key
    preferred_source = 'config_api' if config_api_key else env_source

    # 强制模式优先（失败即报错，不再回退）
    if force_mode == '0':
        if preferred_api_key:
            return _build_openai_config(preferred_api_key, preferred_source, force_mode)
        return _build_error_config(force_mode, '强制模式0失败：接口密钥为空')
    if force_mode == '2':
        return _build_ollama_config(force_mode)
    if force_mode == '3':
        return _build_rule_reply_config(force_mode)
    if force_mode == '4' and bool(YUANBAO_FREE_API.get('enabled', False)) and not _is_yuanbao_web_ready(preferred_api_key):
        return _build_error_config(force_mode, '优先走元宝 web 失败：配置不完整，至少需要接口密钥、agent_id，并确保本地中转接口可用')
    if force_mode == '4' and _is_yuanbao_web_ready(preferred_api_key):
        cfg = _build_openai_config(preferred_api_key, preferred_source, force_mode)
        cfg['strict_mode'] = False
        return cfg
    # 默认检索顺序：
    # 1) 元宝web（当选择“优先走元宝web”且配置完整时）
    # 2) 配置文件 API_KEY
    # 3) 环境变量 API Key
    # 4) 本地 Ollama
    # 5) 规则回复（由上层在 Ollama 不可用时触发）
    if force_mode == '4' and _is_yuanbao_web_ready(preferred_api_key):
        cfg = _build_openai_config(preferred_api_key, preferred_source, '')
        cfg['strict_mode'] = False
        return cfg
    if config_api_key:
        return _build_openai_config(config_api_key, 'config_api', '')
    if env_api_key:
        return _build_openai_config(env_api_key, env_source, '')
    return _build_ollama_config('')
