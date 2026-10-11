"""ai_settings_config_parse（产品侧再导出垫片）。

实现已按《render 层边界契约》下沉到后端中立的 `lib/core/ai_settings/config_parse.py`：
这些模块纯逻辑、不碰 Qt，因此落 core 而非档位 D。这里按原名全集再导出，既有导入面不变。
"""

from __future__ import annotations

from lib.core.ai_settings.config_parse import (  # noqa: F401
    Protocol,
    EXTERNAL_CONFIG_FIELD_KINDS,
    EditorWidgets,
    parse_text_by_template,
    parse_editor_value,
)
