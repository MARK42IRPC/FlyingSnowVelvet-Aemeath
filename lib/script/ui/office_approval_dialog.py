"""办公权限许可弹窗：审批状态 → 后端中立的窗口描述 → 后端宿主。

本模块不再 ``import PyQt5``、也不再是 ``QDialog`` 子类。它只做三件事：

1. 从审批载荷收集状态（标题、原因、命令预览、窗口图标、尺寸档）；
2. 用 ``lib/core/render/visuals/window_specs.py`` 的装配件产出一棵 ``WindowSpec``；
3. 把语义回调（``reject`` / ``allow`` / ``allow_task``）翻译成 ``decision_made``。

真实控件树由 ``render_bridge.create_spec_window()`` 交给 Qt 宿主
（``lib/core/render/backends/qt/widgets/spec_host.py``）搭建。换后端时替换的是那一层，
不是审批语义。

为兼容既有调用方（``office_approval_controller``）与测试，本类保留原 ``QDialog`` 的
属性面：``approval_id`` / ``decision_made`` / ``dismiss_without_decision()`` /
``close()`` / ``findChild()`` / ``open()`` / ``raise_()`` / ``activateWindow()`` /
``destroyed``，其中 ``findChild`` 与 ``destroyed`` 转发到底层 ``QWidget``，调用方
因此不需要知道自己拿到的不再是 ``QDialog``。
"""

from __future__ import annotations

import json
from pathlib import Path

from config.scale import scale_px
from lib.core.render.visuals.window_specs import (
    APPROVAL_ALLOW,
    APPROVAL_ALLOW_TASK,
    APPROVAL_REJECT,
    office_approval_window_spec,
)
from lib.script.ui import render_bridge
from lib.script.ui.office_style import office_stylesheet


_OFFICE_ICON_PATH = Path(__file__).resolve().parents[3] / "resc" / "icon.ico"

#: 语义 id → 产品决策名。三个按钮与关闭按钮共用一张表。
_DECISIONS = {
    APPROVAL_REJECT: "reject",
    APPROVAL_ALLOW: "allow",
    APPROVAL_ALLOW_TASK: "allow_task",
}

#: 反查表：产品决策名 → 描述层的语义 id（`_resolve` 与测试用）。
_SEMANTIC_BY_DECISION = {decision: semantic for semantic, decision in _DECISIONS.items()}


class _ApprovalDecisionSignal:
    """``pyqtSignal(str, str)`` 的后端中立替身：只保留 ``connect`` / ``emit``。"""

    def __init__(self) -> None:
        self._slots: list = []

    def connect(self, slot) -> None:
        if slot not in self._slots:
            self._slots.append(slot)

    def disconnect(self, slot=None) -> None:
        if slot is None:
            self._slots.clear()
            return
        try:
            self._slots.remove(slot)
        except ValueError:
            pass

    def emit(self, *args) -> None:
        for slot in tuple(self._slots):
            slot(*args)


class OfficeApprovalDialog:
    """一个按窗口描述装配出来的办公审批弹窗。"""

    def __init__(self, approval: dict, parent=None) -> None:
        self._approval = dict(approval or {})
        self._approval_id = str(self._approval.get("approval_id", ""))
        self.decision_made = _ApprovalDecisionSignal()
        self._spec = self._build_spec()
        self._window = render_bridge.create_spec_window(
            self._spec,
            parent=parent,
            on_semantic=self._on_semantic,
        )

    # ── 描述 ─────────────────────────────────────────────────────────

    def _build_spec(self):
        approval = self._approval
        command = approval.get("command")
        return office_approval_window_spec(
            title=str(approval.get("tool_name") or "执行受限操作"),
            reason=str(approval.get("reason") or "需要用户许可"),
            command_text=(
                json.dumps(command, ensure_ascii=False, indent=2)
                if command is not None
                else ""
            ),
            accent_height=scale_px(5, min_abs=4),
            icon_size=scale_px(24, min_abs=21),
            root_margin=(
                scale_px(22, min_abs=18),
                scale_px(20, min_abs=16),
                scale_px(22, min_abs=18),
                scale_px(18, min_abs=15),
            ),
            root_spacing=scale_px(12, min_abs=9),
            header_margin=(
                scale_px(12, min_abs=10),
                scale_px(10, min_abs=8),
                scale_px(12, min_abs=10),
                scale_px(10, min_abs=8),
            ),
            header_spacing=scale_px(10, min_abs=8),
            title_column_spacing=scale_px(3, min_abs=2),
            actions_margin=(0, scale_px(4, min_abs=3), 0, 0),
            actions_spacing=scale_px(8, min_abs=6),
            kicker_size=scale_px(10, min_abs=9),
            title_size=scale_px(15, min_abs=13),
            reason_size=scale_px(11, min_abs=10),
            command_size=scale_px(10, min_abs=9),
            command_max_height=scale_px(150, min_abs=120),
            min_width=scale_px(500, min_abs=460),
            max_width=scale_px(680, min_abs=620),
            stylesheet=office_stylesheet(),
            window_icon_path=str(_OFFICE_ICON_PATH),
        )

    # ── 语义与生命周期 ───────────────────────────────────────────────

    def _on_semantic(self, semantic: str) -> None:
        decision = _DECISIONS.get(str(semantic))
        if decision is None:
            return
        self.decision_made.emit(self._approval_id, decision)

    def _resolve(self, decision: str) -> None:
        """按产品决策名给出决定（既有调用方与测试的入口，语义等价于点按钮）。"""

        semantic = _SEMANTIC_BY_DECISION.get(str(decision))
        if semantic is None:
            return
        self._window.resolve_with(semantic)

    def apply_theme(self) -> None:
        """按当前工作台主题重刷样式表与图标色（主题切换后调用）。"""

        self._spec = self._build_spec()
        self._window.apply_theme(self._spec)

    @property
    def approval_id(self) -> str:
        return self._approval_id

    def dismiss_without_decision(self) -> None:
        """不发决定地收起（审批请求已被上游撤销）。"""

        self._window.dismiss_without_decision()

    # ── 底层窗口的属性面（调用方按原 QDialog 用法）───────────────────

    def widget(self):
        """底层真实窗口，供诊断与需要 ``QWidget`` 的调用方取用。"""

        return self._window.widget

    @property
    def destroyed(self):
        return self._window.widget.destroyed

    def findChild(self, *args, **kwargs):  # noqa: N802 - 沿用 Qt 命名
        return self._window.widget.findChild(*args, **kwargs)

    def styleSheet(self) -> str:  # noqa: N802 - 沿用 Qt 命名
        return self._window.widget.styleSheet()

    def windowFlags(self):  # noqa: N802 - 沿用 Qt 命名
        return self._window.widget.windowFlags()

    def testAttribute(self, attribute) -> bool:  # noqa: N802 - 沿用 Qt 命名
        return self._window.widget.testAttribute(attribute)

    def open(self) -> None:
        widget = self._window.widget
        opener = getattr(widget, "open", None)
        if callable(opener):
            opener()
        else:
            widget.show()

    def show(self) -> None:
        self._window.widget.show()

    def close(self) -> None:
        self._window.widget.close()

    def raise_(self) -> None:
        self._window.widget.raise_()

    def activateWindow(self) -> None:  # noqa: N802 - 沿用 Qt 命名
        self._window.widget.activateWindow()


__all__ = [
    "APPROVAL_ALLOW",
    "APPROVAL_ALLOW_TASK",
    "APPROVAL_REJECT",
    "OfficeApprovalDialog",
]
