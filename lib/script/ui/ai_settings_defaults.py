"""ai_settings_defaults（产品侧再导出垫片）。

实现已按《render 层边界契约》下沉到后端中立的 `lib/core/ai_settings/defaults.py`：
这些模块纯逻辑、不碰 Qt，因此落 core 而非档位 D。这里按原名全集再导出，既有导入面不变。
"""

from __future__ import annotations

from lib.core.ai_settings.defaults import (  # noqa: F401
    get_ai_setting_defaults,
    _LOCAL_ONLY_DEFAULTS,
    _EXCLUDED_KEYS,
    build_ai_default_values,
    AI_DEFAULT_VALUES,
)
