"""ai_settings_validation（产品侧再导出垫片）。

实现已按《render 层边界契约》下沉到后端中立的 `lib/core/ai_settings/validation.py`：
这些模块纯逻辑、不碰 Qt，因此落 core 而非档位 D。这里按原名全集再导出，既有导入面不变。
"""

from __future__ import annotations

from lib.core.ai_settings.validation import (  # noqa: F401
    Path,
    Callable,
    parse_hotkey_binding,
    GENERAL_BOOL_KEYS,
    GENERAL_CHOICE_FIELD_OPTIONS,
    GENERAL_NUMERIC_RULES,
    GENERAL_RANGE_RELATIONS,
    GENERAL_TUPLE_INT_RULES,
    friendly_key_name,
    FolderChoices,
    ConfigValueError,
    validate_general_config_values,
    get_choice_field_options,
    raise_config_value_error,
    validate_general_numeric,
    validate_general_config_value,
    validate_general_config_relations,
)
