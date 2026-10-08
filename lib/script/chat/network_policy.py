"""兼容垫片：网络策略已搬到 `lib.core.services.network_policy`。

历史导入路径（`from lib.script.chat.network_policy import API_TIMEOUT_SECS`）继续
可用，实现只有一份在 `lib/core/services/network_policy.py`。
"""

from lib.core.services.network_policy import (  # noqa: F401
    API_RETRY_COUNT,
    API_TIMEOUT_SECS,
    API_TOTAL_ATTEMPTS,
)

__all__ = [
    "API_RETRY_COUNT",
    "API_TIMEOUT_SECS",
    "API_TOTAL_ATTEMPTS",
]
