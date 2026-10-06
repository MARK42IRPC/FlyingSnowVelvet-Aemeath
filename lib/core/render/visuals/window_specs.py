"""成套的窗口描述装配件：把某个窗口的「长什么样」收成可复用的描述工厂。

`window_spec.py` 定义的是描述**词汇**（节点、容器、窗口字段），本模块补上按窗口
语义组装这些词汇的**产品共享装配件**。它仍然只有后端中立类型：尺寸由调用方用
`scale_px` 算好传进来，本模块不 import `config`、`PyQt5`、`backends/*` 或 `lib.script`。

把装配件放在这里而不是产品窗口内部，是为了让「审批弹窗长什么样」成为一份可被
测试单独读的数据：产品窗口只收集审批状态、给出语义回调，控件树由后端宿主按这份
描述搭建。
"""

from __future__ import annotations

from .window_spec import (
    ALIGN_TOP,
    COLOR_CANVAS,
    COLOR_TEXT,
    COLOR_WARNING,
    AccentBarSpec,
    ButtonSpec,
    IconSpec,
    LAYOUT_COLUMN,
    LAYOUT_ROW,
    LabelSpec,
    LayoutSpec,
    ROLE_DANGER,
    TextAreaSpec,
    StretchSpec,
    WINDOW_DIALOG,
    WindowSpec,
)


#: 审批弹窗里各按钮的语义 id（宿主把它回填成产品动作）。
APPROVAL_REJECT = "reject"
APPROVAL_ALLOW = "allow"
APPROVAL_ALLOW_TASK = "allow_task"


def office_approval_window_spec(
    *,
    title: str,
    reason: str,
    command_text: str = "",
    accent_height: int = 0,
    icon_size: int = 0,
    root_margin: tuple[int, int, int, int] = (0, 0, 0, 0),
    root_spacing: int = 0,
    header_margin: tuple[int, int, int, int] = (0, 0, 0, 0),
    header_spacing: int = 0,
    title_column_spacing: int = 0,
    actions_margin: tuple[int, int, int, int] = (0, 0, 0, 0),
    actions_spacing: int = 0,
    kicker_size: int = 0,
    title_size: int = 0,
    reason_size: int = 0,
    command_size: int = 0,
    command_max_height: int = 0,
    min_width: int = 0,
    max_width: int = 0,
    stylesheet: str = "",
    window_icon_path: str = "",
    close_tooltip: str = "关闭（视为拒绝）",
    kicker_text: str = "办公模式 · 权限许可",
    scope_text: str = "“始终允许”仅对当前任务有效，任务结束后自动失效。",
) -> WindowSpec:
    """办公权限许可弹窗的窗口描述。

    ``command_text`` 为空时不生成命令预览区——条件子控件因此是描述层的一等公民，
    不需要产品窗口自己判断控件树该怎么长。
    """

    header_children = [
        IconSpec(
            name="warning",
            id="icon",
            object_name="OfficeApprovalIcon",
            color=COLOR_WARNING,
            size=icon_size,
            valign=ALIGN_TOP,
        ),
        LayoutSpec(
            id="title_column",
            direction=LAYOUT_COLUMN,
            spacing=title_column_spacing,
            stretch=1,
            children=(
                LabelSpec(
                    text=kicker_text,
                    id="kicker",
                    object_name="OfficeApprovalKicker",
                    font_size=kicker_size,
                ),
                LabelSpec(
                    text=title,
                    id="title",
                    object_name="OfficeApprovalTitle",
                    font_size=title_size,
                    bold=True,
                    word_wrap=True,
                ),
            ),
        ),
        ButtonSpec(
            text="",
            semantic=APPROVAL_REJECT,
            id="close",
            object_name="OfficeApprovalClose",
            role=ROLE_DANGER,
            window_icon="close",
            tooltip=close_tooltip,
        ),
    ]

    body_children = [
        LayoutSpec(
            id="header",
            object_name="OfficeApprovalHeader",
            direction=LAYOUT_ROW,
            margin=header_margin,
            spacing=header_spacing,
            frame=True,
            children=tuple(header_children),
        ),
        LabelSpec(
            text=reason,
            id="reason",
            object_name="OfficeApprovalReason",
            font_size=reason_size,
            word_wrap=True,
            selectable=True,
        ),
    ]
    if command_text:
        body_children.append(
            LabelSpec(
                text="请求内容",
                id="command_label",
                object_name="OfficeApprovalCommandLabel",
            )
        )
        body_children.append(
            TextAreaSpec(
                text=command_text,
                id="command",
                object_name="OfficeApprovalCommand",
                font_size=command_size,
                readonly=True,
                monospace=True,
                max_height=command_max_height,
            )
        )
    body_children.append(
        LabelSpec(
            text=scope_text,
            id="scope",
            object_name="OfficeApprovalScope",
            word_wrap=True,
        )
    )
    body_children.append(
        LayoutSpec(
            id="actions",
            direction=LAYOUT_ROW,
            margin=actions_margin,
            spacing=actions_spacing,
            children=(
                ButtonSpec(
                    text="拒绝",
                    semantic=APPROVAL_REJECT,
                    id="reject",
                    object_name="OfficeApprovalReject",
                    icon="reject",
                    icon_color=COLOR_TEXT,
                ),
                StretchSpec(),
                ButtonSpec(
                    text="允许",
                    semantic=APPROVAL_ALLOW,
                    id="allow",
                    object_name="OfficeApprovalAllow",
                    icon="allow",
                    icon_color=COLOR_CANVAS,
                ),
                ButtonSpec(
                    text="始终允许",
                    semantic=APPROVAL_ALLOW_TASK,
                    id="allow_task",
                    object_name="OfficeApprovalAllowTask",
                    icon="allow_task",
                    icon_color=COLOR_CANVAS,
                ),
            ),
        )
    )

    content = LayoutSpec(
        id="root",
        direction=LAYOUT_COLUMN,
        margin=root_margin,
        spacing=root_spacing,
        children=(
            AccentBarSpec(id="accent_bar", height=accent_height),
            *body_children,
        ),
    )

    return WindowSpec(
        content=content,
        object_name="OfficeApprovalDialog",
        title="办公权限许可",
        kind=WINDOW_DIALOG,
        frameless=True,
        stay_on_top=True,
        modal=True,
        delete_on_close=True,
        styled_background=True,
        min_width=min_width,
        max_width=max_width,
        stylesheet=stylesheet,
        window_icon_path=window_icon_path,
        center_on_parent=True,
        center_on_cursor_screen=True,
        drag_handle_ids=("header",),
        close_semantic=APPROVAL_REJECT,
    )


__all__ = [
    "APPROVAL_ALLOW",
    "APPROVAL_ALLOW_TASK",
    "APPROVAL_REJECT",
    "office_approval_window_spec",
]
