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
    ALIGN_HCENTER,
    ALIGN_TOP,
    ALIGN_VCENTER,
    COLOR_CANVAS,
    COLOR_TEXT,
    COLOR_WARNING,
    AccentBarSpec,
    ButtonSpec,
    IconSpec,
    LAYOUT_COLUMN,
    LAYOUT_ROW,
    LAYER_DIALOG,
    LabelSpec,
    LayoutSpec,
    ProgressBarSpec,
    RichTextSpec,
    ROLE_DANGER,
    ROLE_DEFAULT,
    ROLE_GHOST,
    SpacerSpec,
    TextAreaSpec,
    StretchSpec,
    WINDOW_DIALOG,
    WINDOW_TOOL,
    WindowSpec,
)


#: 审批弹窗里各按钮的语义 id（宿主把它回填成产品动作）。
APPROVAL_REJECT = "reject"
APPROVAL_ALLOW = "allow"
APPROVAL_ALLOW_TASK = "allow_task"

#: 帮助浮窗里关闭按钮的语义 id。
HELP_CLOSE = "close"

#: 更新 / 开发版同步浮窗里的动作语义 id。
UPDATE_CLOSE = "update_close"
UPDATE_PRIMARY = "update_primary"
UPDATE_SECONDARY = "update_secondary"
UPDATE_MINIMIZE = "update_minimize"

#: 公告浮窗里的动作语义 id（宿主回填成产品动作）。
ANNOUNCEMENT_CLOSE = "announcement_close"
ANNOUNCEMENT_SUPPRESS_TODAY = "announcement_suppress_today"
ANNOUNCEMENT_SUPPRESS_FOREVER = "announcement_suppress_forever"
ANNOUNCEMENT_RETRY = "announcement_retry"


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


def help_window_spec(
    *,
    title: str,
    text: str,
    width: int = 0,
    height: int = 0,
    border_width: int = 0,
    header_height: int = 0,
    accent_width: int = 0,
    accent_height: int = 0,
    close_size: int = 0,
    root_margin: tuple[int, int, int, int] = (0, 0, 0, 0),
    root_spacing: int = 0,
    header_spacing: int = 0,
    body_margin: tuple[int, int, int, int] = (0, 0, 0, 0),
    header_size: int = 0,
    source_size: int = 0,
    body_size: int = 0,
    close_size_font: int = 0,
    stylesheet: str = "",
    empty_text: str = "",
    source_text: str = "HELP  /  FSV",
    close_tooltip: str = "关闭帮助",
) -> WindowSpec:
    """帮助浮窗的窗口描述：窗眉（accent + 标题 + 来源）+ 可滚动正文 + 关闭。

    结构此前长在 ``help_window.py`` 的构造函数里；搬到这里之后，产品模块只收集
    「标题 + 正文」两个状态，控件树与语义 id 都由这份中立描述产出。
    """

    body_text = str(text or "").strip() or str(empty_text or "")
    header_children = (
        LayoutSpec(
            id="header_accent",
            object_name="HelpHeaderAccent",
            frame=True,
            fixed_width=accent_width,
            fixed_height=accent_height,
            valign=ALIGN_VCENTER,
        ),
        LayoutSpec(
            id="header_text",
            direction=LAYOUT_COLUMN,
            spacing=0,
            stretch=1,
            children=(
                LabelSpec(
                    text=title,
                    id="header",
                    object_name="HelpHeader",
                    font_size=header_size,
                    bold=True,
                ),
                LabelSpec(
                    text=source_text,
                    id="source",
                    object_name="HelpSource",
                    font_size=source_size,
                ),
            ),
        ),
        ButtonSpec(
            text="×",
            semantic=HELP_CLOSE,
            id="close",
            object_name="HelpCloseButton",
            role=ROLE_GHOST,
            tooltip=close_tooltip,
            accessible_name=close_tooltip,
            font_size=close_size_font,
            tool_button=True,
            fixed_size=(close_size, close_size),
            valign=ALIGN_TOP,
        ),
    )

    content = LayoutSpec(
        id="root",
        direction=LAYOUT_COLUMN,
        margin=root_margin,
        spacing=root_spacing,
        children=(
            LayoutSpec(
                id="header_row",
                object_name="HelpHeaderRow",
                direction=LAYOUT_ROW,
                spacing=header_spacing,
                fixed_height=header_height,
                children=header_children,
            ),
            LayoutSpec(
                id="body_area",
                direction=LAYOUT_COLUMN,
                stretch=1,
                scroll=True,
                scroll_object_name="HelpScroll",
                scroll_host_object_name="HelpScrollHost",
                scroll_margin=body_margin,
                children=(
                    LabelSpec(
                        text=body_text,
                        id="body",
                        object_name="HelpBody",
                        font_size=body_size,
                        word_wrap=True,
                        plain_text=True,
                        align="left",
                        valign=ALIGN_TOP,
                    ),
                ),
            ),
        ),
    )

    return WindowSpec(
        content=content,
        object_name="DesktopPetHelpDialog",
        title="帮助",
        kind=WINDOW_TOOL,
        frameless=True,
        stay_on_top=True,
        modal=False,
        translucent=True,
        styled_background=True,
        fixed_size=(width, height),
        stylesheet=stylesheet,
        center_on_parent=False,
        center_on_cursor_screen=True,
        drag_handle_ids=("header_row", "header_text", "header.source"),
        layer=LAYER_DIALOG,
        fade=True,
        hide_semantics=(HELP_CLOSE,),
        border_frame=True,
        border_width=border_width,
    )


def announcement_window_spec(
    *,
    title: str,
    html: str,
    document_stylesheet: str,
    stylesheet: str,
    width: int,
    height: int,
    border_width: int,
    root_margin: tuple[int, int, int, int],
    root_spacing: int,
    header_height: int,
    header_spacing: int,
    accent_width: int,
    accent_height: int,
    header_size: int,
    source_size: int,
    channel_size: int,
    body_size: int,
    close_size: int,
    close_size_font: int,
    button_height: int,
    button_row_spacing: int,
    document_margin: int,
    source_text: str = "SYSTEM BROADCAST  /  FSV",
    channel_text: str = "REMOTE CHANNEL  /  01",
    today_text: str = "今日不再显示",
    forever_text: str = "永远不再显示",
    error_close_text: str = "关闭",
    retry_text: str = "重新加载",
    close_tooltip: str = "关闭公告",
    minimize_tooltip: str = "最小化",
    minimizable: bool = True,
) -> WindowSpec:
    """公告浮窗的窗口描述：窗眉（accent + 标题 + 来源 + 最小化 + 关闭）、富文本正文、动作行。

    动作行里四个按钮全部由描述产出，可见性由产品面按 `show_document` / `show_error`
    决定（``_set_action_mode``）；宿主只负责把它们建出来并回填语义 id。
    """

    header_children = [
        LayoutSpec(
            id="header_text",
            direction=LAYOUT_COLUMN,
            spacing=0,
            stretch=1,
            children=(
                LabelSpec(
                    text=title,
                    id="header",
                    object_name="AnnouncementHeader",
                    font_size=header_size,
                    bold=True,
                ),
                LabelSpec(
                    text=source_text,
                    id="source",
                    object_name="AnnouncementSource",
                    font_size=source_size,
                ),
            ),
        ),
    ]
    if minimizable:
        header_children.append(
            ButtonSpec(
                text="—",
                semantic=ANNOUNCEMENT_CLOSE,  # 占位：最小化不改状态，仅收起窗口
                id="minimize",
                object_name="AnnouncementMinimizeButton",
                role=ROLE_GHOST,
                tooltip=minimize_tooltip,
                accessible_name=minimize_tooltip,
                font_size=close_size_font,
                tool_button=True,
                fixed_size=(close_size, close_size),
                valign=ALIGN_TOP,
            )
        )
    header_children.append(
        ButtonSpec(
            text="×",
            semantic=ANNOUNCEMENT_CLOSE,
            id="close",
            object_name="AnnouncementCloseButton",
            role=ROLE_GHOST,
            tooltip=close_tooltip,
            accessible_name=close_tooltip,
            font_size=close_size_font,
            tool_button=True,
            fixed_size=(close_size, close_size),
            valign=ALIGN_TOP,
        )
    )

    # 窗眉的 accent 竖条：公告用 `QFrame#AnnouncementHeaderAccent`（粉底 + 右侧青描边）。
    accent = LayoutSpec(
        id="header_accent",
        object_name="",
        direction=LAYOUT_COLUMN,
        spacing=0,
        fixed_width=accent_width,
        fixed_height=accent_height,
        valign=ALIGN_VCENTER,
        children=(
            LayoutSpec(
                id="header_accent_bar",
                object_name="AnnouncementHeaderAccent",
                direction=LAYOUT_COLUMN,
                frame=True,
                fixed_width=accent_width,
                fixed_height=accent_height,
            ),
        ),
    )

    action_row = LayoutSpec(
        id="action_row",
        direction=LAYOUT_ROW,
        spacing=button_row_spacing,
        children=(
            LabelSpec(
                text=channel_text,
                id="channel",
                object_name="AnnouncementChannel",
                font_size=channel_size,
            ),
            StretchSpec(weight=1.0, id="action_stretch"),
            ButtonSpec(
                text=error_close_text,
                semantic=ANNOUNCEMENT_CLOSE,
                id="error_close",
                role=ROLE_DEFAULT,
                font_size=0,
                min_height=button_height,
                default_font=True,
            ),
            ButtonSpec(
                text=retry_text,
                semantic=ANNOUNCEMENT_RETRY,
                id="retry",
                object_name="AnnouncementRetryButton",
                role=ROLE_DEFAULT,
                font_size=0,
                min_height=button_height,
                default_font=True,
            ),
            ButtonSpec(
                text=today_text,
                semantic=ANNOUNCEMENT_SUPPRESS_TODAY,
                id="today",
                object_name="AnnouncementTodayButton",
                role=ROLE_DEFAULT,
                font_size=0,
                min_height=button_height,
                default_font=True,
            ),
            ButtonSpec(
                text=forever_text,
                semantic=ANNOUNCEMENT_SUPPRESS_FOREVER,
                id="forever",
                object_name="AnnouncementForeverButton",
                role=ROLE_DEFAULT,
                font_size=0,
                min_height=button_height,
                default_font=True,
            ),
        ),
    )

    content = LayoutSpec(
        id="root",
        direction=LAYOUT_COLUMN,
        margin=root_margin,
        spacing=root_spacing,
        children=(
            LayoutSpec(
                id="header_row",
                object_name="AnnouncementHeaderRow",
                direction=LAYOUT_ROW,
                spacing=header_spacing,
                fixed_height=header_height,
                children=(accent, *header_children),
            ),
            RichTextSpec(
                id="body",
                object_name="AnnouncementBody",
                html=html,
                document_stylesheet=document_stylesheet,
                font_size=body_size,
                document_margin=document_margin,
                open_external_links=False,
            ),
            action_row,
        ),
    )

    return WindowSpec(
        content=content,
        object_name="DesktopPetAnnouncementDialog",
        title="桌宠公告",
        kind=WINDOW_TOOL,
        frameless=True,
        stay_on_top=True,
        modal=False,
        translucent=True,
        styled_background=True,
        fixed_size=(width, height),
        stylesheet=stylesheet,
        center_on_parent=False,
        center_on_cursor_screen=True,
        drag_handle_ids=("header_row", "header_text", "header"),
        layer=LAYER_DIALOG,
        fade=True,
        hide_semantics=(),
        border_frame=True,
        border_width=border_width,
    )


def update_window_spec(
    *,
    status: str,
    detail: str,
    stylesheet: str,
    width: int,
    height: int,
    border_width: int,
    root_margin: tuple[int, int, int, int],
    root_spacing: int,
    header_spacing: int,
    header_lead: int,
    title_size: int,
    status_size: int,
    detail_size: int,
    minimize_size: int,
    progress_min_height: int = 0,
    primary_object_name: str = "WorkbenchFloatingPrimary",
    minimize_tooltip: str = "最小化",
) -> WindowSpec:
    """更新 / 开发版同步浮窗的窗口描述。

    可见性、按钮文案与进度状态都是运行期状态，因此按钮与进度条先按初始值建出来，
    由产品面（`DesktopPetUpdateDialog`）通过 ``find()`` 拿到的真实控件更新。
    """

    # 窗眉与原文一致：左留白 → 标题占满剩余宽度（居中） → 最小化按钮定宽。
    header = LayoutSpec(
        id="header_row",
        direction=LAYOUT_ROW,
        spacing=header_spacing,
        fixed_height=0,
        children=(
            SpacerSpec(size=header_lead, id="header_lead"),
            LabelSpec(
                text="",
                id="title",
                object_name="UpdateTitle",
                font_size=title_size,
                bold=True,
                align=ALIGN_HCENTER,
                valign=ALIGN_VCENTER,
                stretch=1,
            ),
            ButtonSpec(
                text="—",
                semantic=UPDATE_MINIMIZE,
                id="minimize",
                window_icon="minimize",
                tooltip=minimize_tooltip,
                accessible_name=minimize_tooltip,
                fixed_size=(minimize_size, minimize_size),
                valign=ALIGN_TOP,
            ),
        ),
    )

    content = LayoutSpec(
        id="root",
        direction=LAYOUT_COLUMN,
        margin=root_margin,
        spacing=root_spacing,
        children=(
            header,
            LabelSpec(
                text=status,
                id="status",
                object_name="UpdateStatus",
                font_size=status_size,
                word_wrap=True,
                align=ALIGN_HCENTER,
            ),
            LabelSpec(
                text=detail,
                id="detail",
                object_name="UpdateDetail",
                font_size=detail_size,
                word_wrap=True,
                align=ALIGN_HCENTER,
                valign=ALIGN_TOP,
                stretch=1,
            ),
            ProgressBarSpec(
                id="progress",
                object_name="UpdateProgress",
                text_visible=True,
                minimum=0,
                maximum=1,
                value=0,
                min_height=progress_min_height,
            ),
            LayoutSpec(
                id="button_row",
                direction=LAYOUT_ROW,
                spacing=0,
                collapse_when_empty=True,
                children=(
                    StretchSpec(weight=1.0, id="button_stretch"),
                    ButtonSpec(
                        text="",
                        semantic=UPDATE_SECONDARY,
                        id="secondary",
                        default_font=True,
                    ),
                    ButtonSpec(
                        text="",
                        semantic=UPDATE_PRIMARY,
                        id="primary",
                        object_name=primary_object_name,
                        default_font=True,
                    ),
                ),
            ),
        ),
    )

    return WindowSpec(
        content=content,
        object_name="DesktopPetUpdateDialog",
        title="",
        kind=WINDOW_TOOL,
        frameless=True,
        stay_on_top=True,
        modal=False,
        translucent=True,
        styled_background=True,
        fixed_size=(width, height),
        stylesheet=stylesheet,
        center_on_parent=False,
        center_on_cursor_screen=True,
        drag_handle_ids=("header_row",),
        layer=LAYER_DIALOG,
        fade=True,
        hide_semantics=(),
        border_frame=True,
        border_width=border_width,
        border_mid="border",
        border_fill="surface",
    )


__all__ = [
    "ANNOUNCEMENT_CLOSE",
    "ANNOUNCEMENT_RETRY",
    "ANNOUNCEMENT_SUPPRESS_FOREVER",
    "ANNOUNCEMENT_SUPPRESS_TODAY",
    "APPROVAL_ALLOW",
    "APPROVAL_ALLOW_TASK",
    "APPROVAL_REJECT",
    "HELP_CLOSE",
    "UPDATE_CLOSE",
    "UPDATE_MINIMIZE",
    "UPDATE_PRIMARY",
    "UPDATE_SECONDARY",
    "announcement_window_spec",
    "help_window_spec",
    "office_approval_window_spec",
    "update_window_spec",
]
