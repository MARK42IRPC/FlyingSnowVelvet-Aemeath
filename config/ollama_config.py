from __future__ import annotations

import ast
import json
import os
from pathlib import Path

from config.shared_storage_paths import get_shared_config_path, get_shared_root_dir
from config.user_settings import load_section, migrate_section_once
from config.user_storage_paths import get_user_secrets_dir

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


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _local_secret_path() -> Path:
    return _project_root() / 'resc' / 'user' / 'ai' / 'ollama_secrets.json'


def _candidate_secret_paths() -> list[Path]:
    candidates: list[Path] = [get_user_secrets_dir('ai.json')]

    local_path = _local_secret_path()

    try:
        shared_path = get_shared_root_dir() / 'resc' / 'user' / 'ai' / 'ollama_secrets.json'
    except Exception:
        shared_path = None

    for candidate in (shared_path, local_path):
        if candidate is None:
            continue
        try:
            if all(candidate.resolve() != existing.resolve() for existing in candidates):
                candidates.append(candidate)
        except Exception:
            if candidate not in candidates:
                candidates.append(candidate)

    return candidates


def _normalize_secret_text(value: object) -> str:
    return str(value or '').strip()


def _load_local_secret_overrides(path: Path | None = None) -> dict[str, str]:
    secret_paths = [path] if path is not None else _candidate_secret_paths()
    for secret_path in secret_paths:
        try:
            payload = json.loads(secret_path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        normalized = {
            str(key or '').strip(): _normalize_secret_text(value)
            for key, value in payload.items()
            if str(key or '').strip()
        }
        if normalized:
            return normalized
    return {}


_LOCAL_SECRET_OVERRIDES = _load_local_secret_overrides()

# API Key 配置（优先使用）
# 如果设置了有效的 API Key，将使用 OpenAI 兼容 API 而非本地 Ollama。
# 默认保持为空；AI 设置面板会把本地密钥写到 user/secrets/ai.json，
# 避免把密钥提交到仓库。
API_KEY = _LOCAL_SECRET_OVERRIDES.get('api_key', '')

# 回复模式（留空=默认路由）
# 1: 优先使用福利 API
# 0: 优先使用手动 OpenAI 兼容 API，失败回退福利 API
# 2: 强制本地 Ollama
# 3: 强制规则回复
# 4: 强制元宝 Web 本地中转
FORCE_REPLY_MODE = '1'

# OpenAI 兼容 API 基础地址（使用 API Key 时生效）
# 常见兼容服务地址：
# - OpenAI:     'https://api.openai.com/v1'
# - DeepSeek:   'https://api.deepseek.com/v1'
# - Moonshot:   'https://api.moonshot.cn/v1'
# - 智谱AI:     'https://open.bigmodel.cn/api/paas/v4'
# - 通义千问:   'https://dashscope.aliyuncs.com/compatible-mode/v1'
API_BASE_URL = _LOCAL_SECRET_OVERRIDES.get('api_base_url', '')

# 使用 API Key 时的模型名称
# 例如: 'gpt-4o-mini', 'deepseek-chat', 'moonshot-v1-8k'
API_MODEL = _LOCAL_SECRET_OVERRIDES.get('api_model', 'gpt-5.4')

# YuanBao-Free-API 固定本地回环配置。
# 这套地址 / 占位密钥 / 默认模型由程序内部管理，不再从控制面板复用手动 OpenAI 配置。
YUANBAO_FREE_API_LOCAL = {
    'base_url': 'http://127.0.0.1:8000/v1',
    'api_key': 'sk-yuanbao-local',
    'model': 'deepseek-v3',
}

# 福利 API 固定配置：默认优先使用；手动配置为空或不可用时不影响保存。
WELFARE_API = {
    'base_url': 'https://apihub.agnes-ai.com/v1',
    'api_key': 'sk-VKpQS18S751kmeVVJ42aFdZgeY1J1BlOzTI3MyBvu9bbyEEi',
    'model': 'agnes-2.0-flash',
}

# YuanBao-Free-API 登录/会话配置（参考 chenwr727/yuanbao-free-api）
# 当前集成的是仓库内置的本地中转服务：
# - 启动后会自动生成登录二维码图片并等待扫码
# - chat_id / 图片上传等能力仍沿用 OpenAI 兼容接口
YUANBAO_FREE_API = {
    'login_url': 'https://yuanbao.tencent.com/chat/naQivTmsDa',
    'hy_source': 'web',
    'hy_user': _LOCAL_SECRET_OVERRIDES.get('yuanbao_hy_user', ''),
    'x_uskey': _LOCAL_SECRET_OVERRIDES.get('yuanbao_x_uskey', ''),
    'agent_id': 'naQivTmsDa',
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
    'api_temperature':     1.35,      # 外部 API 采样温度（0~2）
    'gsv_auto_start':      False,     # 启用 GSV 语音模块；关闭后不预热，也不响应文本语音请求
    'gsv_temperature':     1.35,      # GSV 文本转语音采样温度（0~2）
    'gsv_speed_factor':    1.05,      # GSV 文本转语音语速（0.5~2.0）
    'ai_voice_max_chars':  80,       # GSV 语音合成最大文本长度（20~80）
    'gsv_cache_max_files': 20,       # GSV 语音缓存最大保存条数（1~128）
    'memory_context_limit': 12,      # 发送给 AI 时附带的 recent memory 条数（0~48，0 = 不附带）
    'memory_recall_count': 30,        # 回忆工具单次提取条数（5~50）
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


_AI_SETTING_DEFAULTS = {
    'force_reply_mode': FORCE_REPLY_MODE,
    'api_base_url': '',
    'api_model': 'gpt-5.4',
    'yuanbao_login_url': YUANBAO_FREE_API['login_url'],
    'yuanbao_hy_source': YUANBAO_FREE_API['hy_source'],
    'yuanbao_agent_id': YUANBAO_FREE_API['agent_id'],
    'ollama_base_url': OLLAMA['base_url'],
    'ollama_model': OLLAMA_MODEL,
    'num_gpu': OLLAMA_OPTIONS['num_gpu'],
    'num_thread': OLLAMA_OPTIONS['num_thread'],
    'api_temperature': OLLAMA['api_temperature'],
    'gsv_auto_start': OLLAMA['gsv_auto_start'],
    'gsv_temperature': OLLAMA['gsv_temperature'],
    'gsv_speed_factor': OLLAMA['gsv_speed_factor'],
    'ai_voice_max_chars': OLLAMA['ai_voice_max_chars'],
    'gsv_cache_max_files': OLLAMA['gsv_cache_max_files'],
    'memory_context_limit': OLLAMA['memory_context_limit'],
    'memory_recall_count': OLLAMA['memory_recall_count'],
    'api_enable_thinking': OLLAMA['api_enable_thinking'],
    'auto_companion_enabled': AUTO_COMPANION['enabled'],
}


def get_ai_setting_defaults() -> dict:
    return dict(_AI_SETTING_DEFAULTS)


def _literal_python_config(path: Path) -> dict[str, object]:
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'))
    except (OSError, SyntaxError, UnicodeError):
        return {}
    values: dict[str, object] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if isinstance(node.value, ast.Dict):
            partial: dict[object, object] = {}
            for key_node, value_node in zip(node.value.keys, node.value.values):
                if key_node is None:
                    continue
                try:
                    key = ast.literal_eval(key_node)
                    value = ast.literal_eval(value_node)
                except (ValueError, TypeError):
                    continue
                partial[key] = value
            values[target.id] = partial
            continue
        try:
            values[target.id] = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            continue
    return values


def _legacy_ai_setting_values() -> dict:
    values = {
        'force_reply_mode': FORCE_REPLY_MODE,
        'api_base_url': API_BASE_URL,
        'api_model': API_MODEL,
        'yuanbao_login_url': YUANBAO_FREE_API['login_url'],
        'yuanbao_hy_source': YUANBAO_FREE_API['hy_source'],
        'yuanbao_agent_id': YUANBAO_FREE_API['agent_id'],
        'ollama_base_url': OLLAMA['base_url'],
        'ollama_model': OLLAMA_MODEL,
        'num_gpu': OLLAMA_OPTIONS['num_gpu'],
        'num_thread': OLLAMA_OPTIONS['num_thread'],
        'api_temperature': OLLAMA['api_temperature'],
        'gsv_auto_start': OLLAMA['gsv_auto_start'],
        'gsv_temperature': OLLAMA['gsv_temperature'],
        'gsv_speed_factor': OLLAMA['gsv_speed_factor'],
        'ai_voice_max_chars': OLLAMA['ai_voice_max_chars'],
        'gsv_cache_max_files': OLLAMA['gsv_cache_max_files'],
        'memory_context_limit': OLLAMA['memory_context_limit'],
        'memory_recall_count': OLLAMA['memory_recall_count'],
        'api_enable_thinking': OLLAMA['api_enable_thinking'],
        'auto_companion_enabled': AUTO_COMPANION['enabled'],
    }
    legacy = _literal_python_config(get_shared_config_path('ollama_config.py'))
    scalar_map = {
        'FORCE_REPLY_MODE': 'force_reply_mode',
        'API_BASE_URL': 'api_base_url',
        'API_MODEL': 'api_model',
        'OLLAMA_MODEL': 'ollama_model',
    }
    for legacy_key, setting_key in scalar_map.items():
        if legacy_key in legacy:
            values[setting_key] = legacy[legacy_key]
    for source_name, mapping in (
        ('YUANBAO_FREE_API', {
            'login_url': 'yuanbao_login_url',
            'hy_source': 'yuanbao_hy_source',
            'agent_id': 'yuanbao_agent_id',
        }),
        ('OLLAMA', {
            'base_url': 'ollama_base_url',
            'api_temperature': 'api_temperature',
            'gsv_auto_start': 'gsv_auto_start',
            'gsv_temperature': 'gsv_temperature',
            'gsv_speed_factor': 'gsv_speed_factor',
            'ai_voice_max_chars': 'ai_voice_max_chars',
            'gsv_cache_max_files': 'gsv_cache_max_files',
            'memory_context_limit': 'memory_context_limit',
            'memory_recall_count': 'memory_recall_count',
            'api_enable_thinking': 'api_enable_thinking',
        }),
        ('OLLAMA_OPTIONS', {'num_gpu': 'num_gpu', 'num_thread': 'num_thread'}),
        ('AUTO_COMPANION', {'enabled': 'auto_companion_enabled'}),
    ):
        source = legacy.get(source_name)
        if not isinstance(source, dict):
            continue
        for source_key, setting_key in mapping.items():
            if source_key in source:
                values[setting_key] = source[source_key]
    return values


def _apply_ai_setting_values(values: dict) -> None:
    global FORCE_REPLY_MODE, API_BASE_URL, API_MODEL, OLLAMA_MODEL
    FORCE_REPLY_MODE = values['force_reply_mode']
    API_BASE_URL = values['api_base_url']
    API_MODEL = values['api_model']
    YUANBAO_FREE_API['login_url'] = values['yuanbao_login_url']
    YUANBAO_FREE_API['hy_source'] = values['yuanbao_hy_source']
    YUANBAO_FREE_API['agent_id'] = values['yuanbao_agent_id']
    OLLAMA['base_url'] = values['ollama_base_url']
    OLLAMA_MODEL = values['ollama_model']
    OLLAMA_OPTIONS['num_gpu'] = values['num_gpu']
    OLLAMA_OPTIONS['num_thread'] = values['num_thread']
    OLLAMA['api_temperature'] = values['api_temperature']
    OLLAMA['gsv_auto_start'] = values['gsv_auto_start']
    OLLAMA['gsv_temperature'] = values['gsv_temperature']
    OLLAMA['gsv_speed_factor'] = values['gsv_speed_factor']
    OLLAMA['ai_voice_max_chars'] = values['ai_voice_max_chars']
    OLLAMA['gsv_cache_max_files'] = values['gsv_cache_max_files']
    OLLAMA['memory_context_limit'] = values['memory_context_limit']
    OLLAMA['memory_recall_count'] = values['memory_recall_count']
    OLLAMA['api_enable_thinking'] = values['api_enable_thinking']
    AUTO_COMPANION['enabled'] = values['auto_companion_enabled']


migrate_section_once(
    'legacy_ai_python_v1',
    'ai',
    _legacy_ai_setting_values(),
    _AI_SETTING_DEFAULTS,
)
_apply_ai_setting_values(load_section('ai', _AI_SETTING_DEFAULTS))

# ============================================================
# 辅助函数
# ============================================================

def is_api_key_configured() -> bool:
    """检查当前激活配置是否为外部 API 模式。"""
    return get_active_config().get('api_type') == 'openai_compatible'


def _normalize_force_mode(value) -> str:
    """将强制模式归一化到 '', '0', '2', '3', '4'。"""
    text = '' if value is None else str(value).strip()
    return text if text in ('', '0', '1', '2', '3', '4') else ''


def get_yuanbao_local_base_url() -> str:
    return str((YUANBAO_FREE_API_LOCAL or {}).get('base_url', '') or '').strip() or 'http://127.0.0.1:8000/v1'


def get_yuanbao_local_api_key() -> str:
    return str((YUANBAO_FREE_API_LOCAL or {}).get('api_key', '') or '').strip() or 'sk-yuanbao-local'


def get_yuanbao_local_model() -> str:
    return str((YUANBAO_FREE_API_LOCAL or {}).get('model', '') or '').strip() or 'deepseek-v3'


def _build_yuanbao_provider_options(*, enabled: bool) -> dict:
    options = dict(YUANBAO_FREE_API)
    options['enabled'] = bool(enabled)
    options['base_url'] = get_yuanbao_local_base_url()
    options['model'] = get_yuanbao_local_model()
    return {
        'yuanbao_free_api': options,
    }


def _is_yuanbao_web_ready() -> bool:
    """判断当前 YuanBao-Free-API 配置是否足以优先发起请求。"""
    if not get_yuanbao_local_base_url():
        return False
    if not get_yuanbao_local_model():
        return False
    if not get_yuanbao_local_api_key():
        return False
    if not str((YUANBAO_FREE_API or {}).get('agent_id', '') or '').strip():
        return False
    return True


def _build_openai_config(
    api_key: str,
    key_source: str,
    force_mode: str,
    *,
    base_url: str | None = None,
    model: str | None = None,
    provider_options: dict | None = None,
    fallback_config: dict | None = None,
) -> dict:
    """构造 OpenAI 兼容模式配置。"""
    return {
        'api_type': 'openai_compatible',
        'base_url': API_BASE_URL if base_url is None else base_url,
        'model': API_MODEL if model is None else model,
        'api_key': api_key,
        'key_source': key_source,
        'force_mode': force_mode,
        'strict_mode': bool(force_mode),
        'error': '',
        'provider_options': provider_options or _build_yuanbao_provider_options(enabled=False),
        'fallback_config': fallback_config,
    }


def _build_welfare_config(force_mode: str) -> dict:
    """构造固定福利 API 配置。"""
    return _build_openai_config(
        WELFARE_API['api_key'],
        'welfare_api',
        force_mode,
        base_url=WELFARE_API['base_url'],
        model=WELFARE_API['model'],
    )


def _build_yuanbao_web_config(force_mode: str) -> dict:
    """构造 YuanBao-Free-API 本地中转配置。"""
    return {
        'api_type': 'openai_compatible',
        'base_url': get_yuanbao_local_base_url(),
        'model': get_yuanbao_local_model(),
        'api_key': get_yuanbao_local_api_key(),
        'key_source': 'yuanbao_local',
        'force_mode': force_mode,
        'strict_mode': bool(force_mode),
        'error': '',
        'provider_options': _build_yuanbao_provider_options(enabled=True),
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
    获取当前活跃配置。

    福利 API 是默认首选；手动 OpenAI 兼容配置不完整时仍允许保存，运行时自动回退福利 API。
    """
    config_api_key = (API_KEY or '').strip()
    env_api_key = (_ENV_API_KEY or '').strip()
    env_source = f'env:{_ENV_API_KEY_SOURCE or "FLYINGSNOWVELVET_API_KEY"}'
    force_mode = _normalize_force_mode(FORCE_REPLY_MODE)
    preferred_api_key = config_api_key or env_api_key
    preferred_source = 'config_api' if config_api_key else env_source
    welfare_config = _build_welfare_config(force_mode or '1')
    manual_ready = bool(
        preferred_api_key
        and str(API_BASE_URL or '').strip()
        and str(API_MODEL or '').strip()
    )

    if force_mode == '1':
        return welfare_config
    if force_mode == '0':
        if manual_ready:
            return _build_openai_config(
                preferred_api_key,
                preferred_source,
                force_mode,
                fallback_config=welfare_config,
            )
        return welfare_config
    if force_mode == '2':
        return _build_ollama_config(force_mode)
    if force_mode == '3':
        return _build_rule_reply_config(force_mode)
    if force_mode == '4':
        if not _is_yuanbao_web_ready():
            return _build_error_config(force_mode, '优先走元宝 web 失败：配置不完整，至少需要 agent_id，并确保本地中转接口可用')
        return _build_yuanbao_web_config(force_mode)

    # 未指定模式时也优先福利 API，保持默认行为一致。
    return welfare_config
