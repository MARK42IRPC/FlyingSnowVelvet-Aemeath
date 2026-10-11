"""ai_settings_contributions（产品侧再导出垫片）。

实现已按《render 层边界契约》下沉到后端中立的 `lib/core/ai_settings/contributions.py`：
这些模块纯逻辑、不碰 Qt，因此落 core 而非档位 D。这里按原名全集再导出，既有导入面不变。
"""

from __future__ import annotations

from lib.core.ai_settings.contributions import (  # noqa: F401
    Path,
    _logger,
    project_root,
    contribution_list_path,
    sponsor_author_image_path,
    read_text_with_fallback,
    _CONTRIBUTION_IGNORED_TITLE_PARTS,
    _CONTRIBUTION_HIDDEN_ROLES,
    _MANUAL_CONTRIBUTION_RECORDS,
    extract_first_url,
    normalize_contribution_name,
    guess_contribution_fallback_name,
    split_contribution_header,
    parse_contribution_records,
    load_contribution_records,
)
