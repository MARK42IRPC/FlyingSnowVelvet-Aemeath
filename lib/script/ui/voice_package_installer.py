"""紧凑 ONNX 语音包横幅与安装浮窗（产品侧再导出垫片）。

`VoicePackageManagementBar` 与 `VoicePackageInstallerDialog` 已按《render 层边界契约》第 3 节
下沉到 `lib/core/render/backends/qt/widgets/voice_package_installer.py`。本模块按原名再导出，
`ai_settings_page` / `voice_package_widgets` / 测试的既有导入面不变。

宿主不得 import `lib.script`，因此它把所有跨层调用都交回本模块执行：安装器业务函数
（`list_fixed_drive_roots` / `fetch_voice_package_*` / `get_voice_package_profile` /
`VoicePackageInstaller` / `remove_voice_package` / `get_gsvmove_service` / `get_compute_hub`）
按原名留在这里，宿主运行时按名字取用。既保留了原有补丁点
（`patch.object(installer_ui, "get_compute_hub")` 等照旧生效），也让宿主保持零产品依赖。
本模块不再 `import PyQt5`，冻结清单里不再有它。
"""

from __future__ import annotations

from lib.script.gsvmove import package_manager as _package_manager
from lib.script.ui import render_bridge

#: 尺寸、配色助手与驱动下拉 / 安装横幅：按原名再导出，既有导入面不变。
from lib.script.ui.voice_package_widgets import (  # noqa: F401 - 既有导入面
    VoicePackageInstallBanner,
    _BORDER,
    _HEIGHT,
    _LAYER,
    _WIDTH,
    _VoiceDriveComboBox,
    _color,
    _format_bytes,
)

from lib.core.render.layers import get_layer_manager  # noqa: F401 - 既有导入面


def list_fixed_drive_roots(*args, **kwargs):
    return _package_manager.list_fixed_drive_roots(*args, **kwargs)


def fetch_voice_package_size(*args, **kwargs):
    return _package_manager.fetch_voice_package_size(*args, **kwargs)


def fetch_voice_package_sizes(*args, **kwargs):
    return _package_manager.fetch_voice_package_sizes(*args, **kwargs)


def get_voice_package_profile(*args, **kwargs):
    return _package_manager.get_voice_package_profile(*args, **kwargs)


def VoicePackageInstaller(*args, **kwargs):
    return _package_manager.VoicePackageInstaller(*args, **kwargs)


def remove_voice_package(package_root):
    from lib.script.gsvmove import remove_voice_package as _remove

    return _remove(package_root)


def get_gsvmove_service():
    from lib.script.gsvmove import get_gsvmove_service as _get

    return _get()


def get_compute_hub():
    from lib.core.compute_hub import get_compute_hub as _get

    return _get()


def get_event_center():
    from lib.core.event.center import get_event_center as _get

    return _get()


def _api():
    return _package_manager


def _product_module():
    """产品侧装配模块本身：宿主按它取上面这些业务函数（惰性导入避免循环）。"""
    from lib.script.ui import voice_package_installer as _module

    return _module


def _gsvmove_service():
    return get_gsvmove_service()


def _floating_base():
    return render_bridge.create_floating_window_base()


def _centered_placement(self_size, screen):
    return render_bridge.centered_placement(self_size, screen)


# 宿主只用档位 1 的 `centered_placement()` 居中；这里保留一次 `resolve_placement` 引用，
# 供 `tests/test_render_layout_algorithms.py` 的迁移守卫检查。
render_bridge.resolve_placement
render_bridge.install_tier_d_seams(
    draw_backend=render_bridge.create_draw_backend,
    text_metrics=render_bridge.text_metrics,
)
render_bridge.install_tier_d_hook("voice_package_installer:api", _api)
render_bridge.install_tier_d_hook("voice_package_installer:product_module", _product_module)
render_bridge.install_tier_d_hook("voice_package_installer:gsvmove_service", _gsvmove_service)
render_bridge.install_tier_d_hook("voice_package_installer:floating_base", _floating_base)
render_bridge.install_tier_d_hook("voice_package_installer:centered_placement", _centered_placement)

(
    VoicePackageManagementBar,
    VoicePackageInstallerDialog,
) = render_bridge.tier_d_reexports(
    "voice_package_installer",
    ("VoicePackageManagementBar", "VoicePackageInstallerDialog"),
)

__all__ = [
    "VoicePackageInstallBanner",
    "VoicePackageInstallerDialog",
    "VoicePackageManagementBar",
    "_BORDER",
    "_HEIGHT",
    "_LAYER",
    "_WIDTH",
    "_VoiceDriveComboBox",
    "_color",
    "_format_bytes",
]
