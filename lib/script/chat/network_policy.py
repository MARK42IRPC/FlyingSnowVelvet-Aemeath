"""Fixed network policy shared by AI API requests."""

API_TIMEOUT_SECS = 10.0
API_RETRY_COUNT = 3
API_TOTAL_ATTEMPTS = 1 + API_RETRY_COUNT
