"""办公模式的配置控件（产品侧再导出垫片）。

`OfficeModeSettings` 与三个共用控件（`WatermarkComboBox` / `ApiKeyLineEdit`）、字段说明辅助
（`describe_form_row` / `set_widget_description` / `create_field_row_group`）以及常用提供商
预设已按《render 层边界契约》第 3 节的档位 D 下沉到
`lib/core/render/backends/qt/widgets/office_mode_settings.py`。本模块按原名再导出，既有导入面
不变；地址补全 / 模型列表解析仍来自后端中立的 `lib/core/services/api_endpoints`。

宿主不 import 产品包：本机 DSH 探测入口由这里经 `render_bridge.install_tier_d_hook()` 注入，
注入物是一个**惰性解析器**——每次调用都重新读本模块的 `probe_local_dsh`，因此测试对垫片
`probe_local_dsh` 的 patch 照旧生效（与原实现「构造期取模块全局」的语义一致）。
"""

from __future__ import annotations

from lib.script.ui import render_bridge

(
    ApiKeyLineEdit,
    MANUAL_API_PROVIDER_PRESETS,
    OfficeModeSettings,
    WatermarkComboBox,
    create_field_row_group,
    describe_form_row,
    fetch_api_models,
    manual_api_models_url,
    normalize_api_base_url,
    parse_api_models,
    set_widget_description,
) = render_bridge.tier_d_reexports("office_mode_settings", (
    "ApiKeyLineEdit",
    "MANUAL_API_PROVIDER_PRESETS",
    "OfficeModeSettings",
    "WatermarkComboBox",
    "create_field_row_group",
    "describe_form_row",
    "fetch_api_models",
    "manual_api_models_url",
    "normalize_api_base_url",
    "parse_api_models",
    "set_widget_description",
))


def probe_local_dsh() -> dict:
    """读取启动期探测结果，没有缓存时按需只读探测；失败不影响界面。

    这是真正 import 产品包（`lib.script.office`）的落点，也是测试的 patch 目标：宿主经 seam
    注入的解析器每次都会回到本模块取它，所以替换模块属性即可改变构造期读到的探测结果。
    """
    from lib.script.office import local_dsh

    cached = local_dsh.cached_local_dsh_status()
    if cached is not None:
        return dict(cached)
    return dict(local_dsh.local_dsh_status())


def _office_local_dsh_probe() -> dict:
    """宿主经 `tier_d_seams.hook("office_local_dsh_probe")` 调用的惰性解析器。"""
    from lib.script.ui import office_mode_settings as shim

    return shim.probe_local_dsh()


render_bridge.install_tier_d_hook("office_local_dsh_probe", _office_local_dsh_probe)

render_bridge.install_tier_d_seams(ui_font=render_bridge.ui_font)

__all__ = [
    "ApiKeyLineEdit",
    "MANUAL_API_PROVIDER_PRESETS",
    "OfficeModeSettings",
    "WatermarkComboBox",
    "create_field_row_group",
    "describe_form_row",
    "fetch_api_models",
    "manual_api_models_url",
    "normalize_api_base_url",
    "parse_api_models",
    "probe_local_dsh",
    "set_widget_description",
]
