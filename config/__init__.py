# Config module

from config.shared_storage import ensure_shared_config_ready

ensure_shared_config_ready()

from config.config import *
from config.general_user_settings import initialize_general_user_settings

initialize_general_user_settings()

from config.ollama_config import (
    OLLAMA, OLLAMA_MODEL, OLLAMA_OPTIONS, PERSONA_FILE,
    API_KEY, API_BASE_URL, API_MODEL, FORCE_REPLY_MODE,
    AUTO_COMPANION,
    is_api_key_configured, get_active_config,
)
