"""AI 接口的固定网络策略（超时与重试次数）。

这些数字原先住在 `lib/script/chat/network_policy.py`。它们只是三个常量，却被
`lib/core/services/api_endpoints.py`（本机/手动 OpenAI 兼容接口探测）与
`lib/script/chat/*` 两边同时需要；放在 `lib/script/chat/` 会让 `lib/core` 反向依赖
产品包，所以按「后端中立能力住 `lib/core`」的边界搬到 `lib/core/services/`。

`lib/script/chat/network_policy.py` 保留一层同名重导出，老导入路径继续可用。
本模块不 import PyQt5、不 import lib.script。
"""

from __future__ import annotations

API_TIMEOUT_SECS = 10.0
API_RETRY_COUNT = 3
API_TOTAL_ATTEMPTS = 1 + API_RETRY_COUNT
