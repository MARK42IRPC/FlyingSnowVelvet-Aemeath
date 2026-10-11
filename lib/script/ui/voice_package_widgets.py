"""语音包安装器的共享尺寸 / 配色助手、驱动下拉与安装横幅（产品侧再导出垫片）。

`VoicePackageInstallBanner` / `_VoiceDriveComboBox` / 尺寸常量与 `_color` / `_format_bytes`
助手已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/voice_package_widgets.py`。本模块按原名再导出，既有
导入面不变。

`voice_package_installer` 需要 `VoicePackageStatus` 做类型标注与状态分派；宿主不得
import `lib.script`，因此产品侧把该类经 seam 钩子登记（宿主未注入时按鸭子类型工作）。
"""

from __future__ import annotations

from lib.script.ui import render_bridge


def _voice_package_status_class():
    from lib.script.gsvmove.package_manager import VoicePackageStatus

    return VoicePackageStatus


render_bridge.install_tier_d_hook(
    "voice_package_status_class", _voice_package_status_class
)

(
    VoicePackageInstallBanner,
    _BORDER,
    _HEIGHT,
    _LAYER,
    _WIDTH,
    _VoiceDriveComboBox,
    _color,
    _format_bytes,
) = render_bridge.tier_d_reexports("voice_package_widgets", (
    "VoicePackageInstallBanner",
    "_BORDER",
    "_HEIGHT",
    "_LAYER",
    "_WIDTH",
    "_VoiceDriveComboBox",
    "_color",
    "_format_bytes",
))

__all__ = [
    "VoicePackageInstallBanner",
    "_BORDER",
    "_HEIGHT",
    "_LAYER",
    "_WIDTH",
    "_VoiceDriveComboBox",
    "_color",
    "_format_bytes",
]
