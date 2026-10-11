"""ai_settings_labels（产品侧再导出垫片）。

实现已按《render 层边界契约》下沉到后端中立的 `lib/core/ai_settings/labels.py`：
这些模块纯逻辑、不碰 Qt，因此落 core 而非档位 D。这里按原名全集再导出，既有导入面不变。
"""

from __future__ import annotations

from lib.core.ai_settings.labels import (  # noqa: F401
    SECTION_HELP_TEXTS,
    section_help_text,
    DICT_FRIENDLY_NAME,
    KEY_FRIENDLY_NAME,
    friendly_section_name,
    friendly_field_section_name,
    friendly_key_name,
    animation_folder_display_name,
)
