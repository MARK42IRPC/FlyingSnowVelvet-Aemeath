"""ai_settings_descriptions（产品侧再导出垫片）。

实现已按《render 层边界契约》下沉到后端中立的 `lib/core/ai_settings/descriptions.py`：
这些模块纯逻辑、不碰 Qt，因此落 core 而非档位 D。这里按原名全集再导出，既有导入面不变。
"""

from __future__ import annotations

from lib.core.ai_settings.descriptions import (  # noqa: F401
    choice_label_for_value,
    format_config_editor_value,
    friendly_field_section_name,
    friendly_section_name,
    description_preview_value,
    description_value_type,
    build_config_single_description,
    build_config_range_description,
)
