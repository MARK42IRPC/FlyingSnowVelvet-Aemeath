"""办公任务独立窗口页面（产品侧装配垫片）。

`OfficeWorkbenchPage`（办公任务工作台页）与 `open_office_window` / `get_office_window` /
`cleanup_office_window` 的窗口单例管理已按《render 层边界契约》第 3 节下沉到
`lib/core/render/backends/qt/widgets/office_page.py`。本模块按原名再导出，并把宿主需要的产品
侧事实经共享 seam 注入（`office_active_statuses` / `office_default_effort` /
`office_default_workspace_name` / `office_desktop_dir` / `office_file_ipc_factory` /
`particle_spawn` / `office_warmup_runtime`）；既有导入面不变。

注入物一律**惰性解析**：测试对 `lib.script.ui.office_page` 属性的 patch 照旧生效。
"""

from __future__ import annotations

from lib.script.ui import render_bridge


def get_office_window():
    """当前独立办公页面；没有打开过时返回 None。"""
    from lib.script.ui import office_page as shim

    return shim._window()


def open_office_window():
    """打开（或复用）独立办公页面，并拉一次最新任务状态。"""
    from lib.script.ui import office_page as shim

    return shim._open()


def cleanup_office_window() -> None:
    """关闭并释放独立办公页面。"""
    from lib.script.ui import office_page as shim

    shim._cleanup()


def _office_active_statuses():
    from lib.script.office.contracts import ACTIVE_TASK_STATUSES

    return tuple(ACTIVE_TASK_STATUSES)


def _office_default_effort() -> str:
    from lib.script.office.contracts import DEFAULT_REASONING_EFFORT

    return DEFAULT_REASONING_EFFORT


def _office_desktop_dir():
    from lib.script.office.workspace import resolve_desktop_dir

    return str(resolve_desktop_dir())


def _office_default_workspace_name() -> str:
    from lib.script.office.workspace import DEFAULT_WORKSPACE_NAME

    return DEFAULT_WORKSPACE_NAME


def _office_ipc():
    from lib.script.office.ipc import OfficeFileIpc

    return OfficeFileIpc()


def _office_particle(x, y, particle_id) -> None:
    from lib.core.particle_utils import spawn_particle_at_point

    spawn_particle_at_point(x, y, particle_id)


def _office_warmup() -> None:
    from lib.script.office.service import get_office_service

    get_office_service().warmup_runtime()


render_bridge.install_tier_d_hook("office_active_statuses", _office_active_statuses)
render_bridge.install_tier_d_hook("office_default_effort", _office_default_effort)
render_bridge.install_tier_d_hook("office_desktop_dir", _office_desktop_dir)
render_bridge.install_tier_d_hook("office_default_workspace_name", _office_default_workspace_name)
render_bridge.install_tier_d_hook("office_file_ipc_factory", _office_ipc)
render_bridge.install_tier_d_hook("office_effort_steps", lambda: 5)
render_bridge.install_tier_d_hook("particle_spawn", _office_particle)
render_bridge.install_tier_d_hook("office_warmup_runtime", _office_warmup)


def _reexports():
    return render_bridge.tier_d_reexports(
        "office_page",
        (
            "OfficeWorkbenchPage",
            "get_office_window",
            "open_office_window",
            "cleanup_office_window",
            "EFFORT_STAR_PARTICLE_ID",
            "DEFAULT_WINDOW_WIDTH",
            "DEFAULT_WINDOW_HEIGHT",
        ),
    )


(
    OfficeWorkbenchPage,
    _window,
    _open,
    _cleanup,
    EFFORT_STAR_PARTICLE_ID,
    DEFAULT_WINDOW_WIDTH,
    DEFAULT_WINDOW_HEIGHT,
) = _reexports()

__all__ = [
    "DEFAULT_WINDOW_HEIGHT",
    "DEFAULT_WINDOW_WIDTH",
    "EFFORT_STAR_PARTICLE_ID",
    "OfficeWorkbenchPage",
    "cleanup_office_window",
    "get_office_window",
    "open_office_window",
]
