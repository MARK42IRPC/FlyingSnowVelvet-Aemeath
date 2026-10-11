"""ai_settings_config_schema（产品侧再导出垫片）。

实现已按《render 层边界契约》下沉到后端中立的 `lib/core/ai_settings/config_schema.py`：
这些模块纯逻辑、不碰 Qt，因此落 core 而非档位 D。这里按原名全集再导出，既有导入面不变。
"""

from __future__ import annotations

from lib.core.ai_settings.config_schema import (  # noqa: F401
    LAYER_VALUES,
    get_backend_descriptors,
    _ANIMATION_CONFIG,
    DEFAULT_START_ANIMATION_FOLDER,
    DEFAULT_EXIT_ANIMATION_FOLDER,
    _friendly_key_name,
    CATEGORY_KEY_ALLOWLIST,
    GENERAL_BOOL_KEYS,
    GENERAL_NUMERIC_RULES,
    GENERAL_TUPLE_INT_RULES,
    GENERAL_RANGE_RELATIONS,
    VOLUME_SLIDER_FIELDS,
    GENERAL_DECIMAL_SLIDER_SPECS,
    GENERAL_CHOICE_FIELD_OPTIONS,
    GENERAL_CONFIG_DEFAULTS,
    GENERAL_MIXED_SECTION_FIELDS,
    category_section_entries,
    choice_label_for_value,
    hardcoded_general_default,
    range_pair_signature,
    friendly_range_name,
    is_supported_config_value,
    format_config_editor_value,
)
