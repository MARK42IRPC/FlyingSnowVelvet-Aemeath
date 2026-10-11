"""办公审批弹窗的 Qt 控制器（产品侧装配垫片）。

`OfficeApprovalController` 已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/office_approval_controller.py`。这里按原名再导出，并把
宿主需要的两处产品入口经共享 seam 注入（`office_file_ipc_factory` /
`office_approval_dialog_class`）；既有导入面不变。

注入物是**惰性解析器**：每次调用都重新读本模块的 `OfficeFileIpc` / `OfficeApprovalDialog`，
所以测试对本模块属性的 patch 照旧生效。
"""

from __future__ import annotations

from lib.script.office.ipc import OfficeFileIpc  # noqa: F401 - 宿主经本模块属性惰性解析
from lib.script.ui import render_bridge
from lib.script.ui.office_approval_dialog import (  # noqa: F401 - 宿主经本模块属性惰性解析
    OfficeApprovalDialog,
)


def _office_file_ipc():
    from lib.script.ui import office_approval_controller as shim

    return shim.OfficeFileIpc()


def _office_approval_dialog():
    from lib.script.ui import office_approval_controller as shim

    return shim.OfficeApprovalDialog


render_bridge.install_tier_d_hook("office_file_ipc_factory", _office_file_ipc)
render_bridge.install_tier_d_hook("office_approval_dialog_class", _office_approval_dialog)

(OfficeApprovalController, ) = render_bridge.tier_d_reexports(
    "office_approval_controller", ("OfficeApprovalController",)
)

__all__ = ["OfficeApprovalController"]
