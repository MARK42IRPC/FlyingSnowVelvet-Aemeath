"""Shared Ollama constants and logging."""

from config.ollama_config import OLLAMA
from lib.core.logger import get_logger

logger = get_logger(__name__)

OLLAMA_BASE_URL = OLLAMA.get('base_url', 'http://localhost:11434')
PING_INTERVAL_MS = OLLAMA.get('ping_interval_ms', 5000)
PULL_EMIT_INTERVAL = OLLAMA.get('pull_emit_interval', 2.0)
API_RATE_LIMIT_WINDOW_SECS = 60
API_RATE_LIMIT_MAX_REQUESTS = 10
