"""The one shared shell, body and stylesheet for every product pop-up.

All announcement-style windows in the product — announcement, help, update,
office approval, confirmation and notice — are assembled here. Product windows
only collect state and translate semantic ids into product actions; they must
not carry their own stylesheet or re-implement the chrome.

Why one module: the announcement is the visual baseline (pink/cyan accent,
frameless card, surface body, role-coloured actions). Keeping a second QSS
factory per feature is how the dialogs drifted apart in the first place.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape

from config.font_config import get_ui_font_family
from config.scale import scale_px
from lib.core.render.layers import FRAME, CONTENT, Layer, MIDDLE, OVERLAY, OVERLAY_SECOND
from lib.core.render.popup_design import POPUP_ACTION_TOKENS, POPUP_DISABLED_TOKENS, PopupChrome, popup_chrome_dimensions
from lib.core.render.visuals.commands import DrawBatch, RectCommand, TextAlignment, TextCommand
from lib.core.render.visuals.types import FontSpec, Rect, Size
from lib.core.render.visuals.window_spec import (
    ALIGN_TOP,
    ALIGN_VCENTER,
    LAYER_DIALOG,
    LAYOUT_COLUMN,
    LAYOUT_ROW,
    ROLE_GHOST,
    WINDOW_DIALOG,
    WINDOW_TOOL,
    ButtonSpec,
    LabelSpec,
    LayoutSpec,
    ProgressBarSpec,
    RichTextSpec,
    StretchSpec,
    TextAreaSpec,
    WindowSpec,
)
from lib.core.render.visuals.workbench_tokens import get_workbench_token_colors, get_workbench_tokens


def get_popup_chrome() -> PopupChrome:
    return popup_chrome_dimensions(lambda value, minimum: scale_px(value, min_abs=minimum))


@dataclass(frozen=True, slots=True)
class PopupAction:
    semantic: str
    text: str
    width: int = 0
    role: str = "default"
    state: str = "normal"


@dataclass(frozen=True, slots=True)
class PopupGeometry:
    header: Rect
    body: Rect
    action_rects: tuple[tuple[str, Rect], ...]


@dataclass(frozen=True, slots=True)
class PopupDrawChrome:
    geometry: PopupGeometry
    batch: DrawBatch

    @property
    def body(self) -> Rect:
        return self.geometry.body

    @property
    def action_rects(self) -> tuple[tuple[str, Rect], ...]:
        return self.geometry.action_rects


def resolve_popup_geometry(
    size: Size,
    *,
    actions: tuple[PopupAction, ...] = (),
    show_close: bool = False,
    show_minimize: bool = False,
) -> PopupGeometry:
    """Map an announcement shell to rectangles for batch-rendered pop-ups."""
    chrome = get_popup_chrome()
    margin = min(chrome.margin_x, max(0, int(size.width) // 4))
    inner_width = max(0, size.width - 2 * margin)
    left, right = margin, size.width - margin
    header = Rect(left, chrome.margin_top, inner_width, chrome.header_height)
    footer = size.height - chrome.margin_bottom - chrome.action_height
    body_y = header.y + header.height + chrome.row_gap
    body_end = footer - chrome.row_gap if actions else size.height - chrome.margin_bottom
    body = Rect(left, body_y, inner_width, max(0, body_end - body_y))

    controls: list[tuple[str, Rect]] = []
    header_right = right
    for semantic, visible in (("close", show_close), ("minimize", show_minimize)):
        if visible:
            header_right -= chrome.close_size
            controls.append((semantic, Rect(header_right, header.y, chrome.close_size, chrome.close_size)))
            header_right -= chrome.header_gap

    if actions:
        gap = min(chrome.action_gap, inner_width / max(1, len(actions) * 2))
        widths = [
            action.width or (len(action.text) * chrome.action_size + 2 * chrome.action_pad_x)
            for action in actions
        ]
        available = max(0, inner_width - gap * (len(actions) - 1))
        factor = min(1.0, available / max(1, sum(widths)))
        footer_rects = []
        for action, width in reversed(tuple(zip(actions, widths))):
            width *= factor
            right -= width
            footer_rects.append((action.semantic, Rect(right, footer, width, chrome.action_height)))
            right -= gap
        controls.extend(reversed(footer_rects))
    return PopupGeometry(header, body, tuple(controls))


def build_popup_draw_chrome(
    size: Size,
    *,
    title: str,
    source: str = "",
    actions: tuple[PopupAction, ...] = (),
    show_close: bool = False,
    show_minimize: bool = False,
    hovered: str = "",
    pressed: str = "",
    mode: str | None = None,
    layer: int = int(Layer.DIALOG),
) -> PopupDrawChrome:
    """Emit the announcement chrome for native drawing backends."""
    chrome = get_popup_chrome()
    geometry = resolve_popup_geometry(size, actions=actions, show_close=show_close, show_minimize=show_minimize)
    colors = get_workbench_token_colors(mode)
    header = geometry.header
    edge = min(chrome.border, chrome.accent_width)
    family = get_ui_font_family()
    commands = [
        RectCommand(Rect(0, 0, size.width, size.height), fill=colors["border_strong"], layer=layer),
        RectCommand(Rect(chrome.border, chrome.border, max(0, size.width - 2 * chrome.border),
                         max(0, size.height - 2 * chrome.border)), fill=colors["canvas"], layer=layer, z=FRAME),
        RectCommand(geometry.body, fill=colors["surface"], stroke=colors["border"], stroke_width=chrome.border,
                    radius=chrome.radius, antialias=True, layer=layer, z=MIDDLE),
        RectCommand(Rect(header.x, header.y, chrome.accent_width - edge, header.height),
                    fill=colors["pink"], layer=layer, z=MIDDLE),
        RectCommand(Rect(header.x + chrome.accent_width - edge, header.y, edge, header.height),
                    fill=colors["cyan"], layer=layer, z=MIDDLE),
    ]
    left = header.x + chrome.accent_width + chrome.header_gap
    controls = dict(geometry.action_rects)
    right = header.x + header.width
    for semantic in ("close", "minimize"):
        if semantic in controls:
            right = min(right, controls[semantic].x - chrome.header_gap)
    width = max(0, right - left)
    title_height = max(1, header.height - chrome.source_size - 4) if source else header.height
    commands.append(TextCommand(title, FontSpec(family, chrome.title_size, True), colors["text"],
                                Rect(left, header.y, width, title_height), layer=layer, z=CONTENT))
    if source:
        commands.append(TextCommand(source, FontSpec(family, chrome.source_size), colors["text_dim"],
                                    Rect(left, header.y + title_height, width, header.height - title_height),
                                    layer=layer, z=CONTENT))
    action_by_id = {action.semantic: action for action in actions}
    for semantic, rect in geometry.action_rects:
        action = action_by_id.get(semantic)
        if action is None:
            action = PopupAction(semantic, "×" if semantic == "close" else "—", role="ghost")
        state = action.state
        if state == "normal" and semantic == hovered:
            state = "pressed" if semantic == pressed else "hover"
        tokens = POPUP_ACTION_TOKENS.get(action.role, POPUP_ACTION_TOKENS["default"])
        fill, border, text = POPUP_DISABLED_TOKENS if state == "disabled" else tokens.get(state, tokens["normal"])
        if fill or border:
            commands.append(RectCommand(rect, fill=colors.get(fill), stroke=colors.get(border),
                                        stroke_width=chrome.border, radius=chrome.radius, antialias=True,
                                        layer=layer, z=OVERLAY))
        size = chrome.title_size if action.role == "ghost" else chrome.action_size
        commands.append(TextCommand(action.text, FontSpec(family, size, action.role != "ghost"), colors[text], rect,
                                    alignment=int(TextAlignment.HCENTER | TextAlignment.VCENTER),
                                    layer=layer, z=OVERLAY_SECOND))
    return PopupDrawChrome(geometry, DrawBatch(tuple(commands)))


def announcement_shell_spec(
    *,
    object_name: str,
    title: str,
    body: RichTextSpec | LayoutSpec,
    actions: tuple[ButtonSpec, ...] = (),
    width: int = 0,
    height: int = 0,
    source_text: str = "",
    channel_text: str = "",
    close_action: str,
    close_tooltip: str = "关闭",
    minimize_action: str = "",
    title_text: str | None = None,
    modal: bool = False,
    default_button_id: str = "",
    action_roles: tuple[tuple[str, str], ...] = (),
    hide_on_actions: tuple[str, ...] = (),
    non_closing_actions: tuple[str, ...] = (),
    delete_on_close: bool = False,
    resizable: bool = False,
    window_icon_path: str = "",
    mode: str | None = None,
    fade: bool = True,
    center_on_parent: bool = True,
) -> WindowSpec:
    """Assemble the shared frameless chrome around any pop-up body.

    ``hide_on_actions`` emits the action and fades the window out for reuse.
    ``non_closing_actions`` gives the caller control over whether it stays open.
    """

    chrome = get_popup_chrome()
    width = width or chrome.notice_width
    height = height or scale_px(420, min_abs=360)
    header_text = [LabelSpec(
        text=title if title_text is None else title_text,
        id="header",
        object_name="AnnouncementHeader",
        font_size=chrome.title_size,
        bold=True,
        word_wrap=modal,
        plain_text=True,
    )]
    if source_text:
        header_text.append(LabelSpec(
            text=source_text, id="source", object_name="AnnouncementSource",
            font_size=chrome.source_size, plain_text=True,
        ))
    header = [
        LayoutSpec(
            id="header_accent_bar", object_name="AnnouncementHeaderAccent", frame=True,
            fixed_width=chrome.accent_width, fixed_height=chrome.header_height,
            valign=ALIGN_VCENTER,
        ),
        LayoutSpec(id="header_text", children=tuple(header_text), stretch=1),
    ]
    roles = dict(action_roles)
    for node_id, glyph, semantic, tooltip in (
        ("minimize", "—", minimize_action, "最小化"),
        ("close", "×", close_action, close_tooltip),
    ):
        if semantic:
            header.append(ButtonSpec(
                text=glyph,
                semantic=semantic,
                id=node_id,
                object_name=f"Announcement{node_id.title()}Button",
                role=ROLE_GHOST,
                tooltip=tooltip,
                accessible_name=tooltip,
                tool_button=True,
                fixed_size=(chrome.close_size, chrome.close_size),
                valign=ALIGN_TOP,
            ))

    rows = [
        LayoutSpec(
            id="header_row", object_name="AnnouncementHeaderRow", direction=LAYOUT_ROW,
            spacing=chrome.header_gap, children=tuple(header),
        ),
        body,
    ]
    if actions or channel_text:
        if channel_text:
            # Decorative channel runs on its own line so it never squeezes the
            # action labels; the button row keeps the full inner width.
            rows.append(LayoutSpec(
                id="channel_row", collapse_when_empty=True, children=(
                    LabelSpec(
                        text=channel_text, id="channel", object_name="AnnouncementChannel",
                        font_size=chrome.channel_size, plain_text=True,
                    ),
                ),
            ))
        action_children = [StretchSpec(id="action_stretch")]
        action_children.extend(
            replace(
                action,
                role=roles.get(action.semantic, action.role),
                min_height=chrome.action_height,
                default_font=True,
                font_size=chrome.action_size,
            )
            for action in actions
        )
        rows.append(LayoutSpec(
            id="action_row", object_name="AnnouncementFooter", direction=LAYOUT_ROW,
            spacing=chrome.action_gap, collapse_when_empty=True, children=tuple(action_children),
        ))
    return WindowSpec(
        content=LayoutSpec(
            id="root", direction=LAYOUT_COLUMN, children=tuple(rows),
            margin=chrome.margins,
            spacing=chrome.row_gap,
        ),
        object_name=object_name,
        title=title,
        kind=WINDOW_DIALOG if modal else WINDOW_TOOL,
        modal=modal,
        translucent=True,
        fixed_size=(0, 0) if resizable else (width, height),
        initial_size=(width, height) if resizable else (0, 0),
        delete_on_close=delete_on_close,
        stylesheet=announcement_stylesheet(mode),
        shared_popup=True,
        theme_mode=mode,
        center_on_parent=center_on_parent,
        drag_handle_ids=("header_row", "header_text", "header"),
        close_semantic=close_action,
        default_button_id=default_button_id,
        window_icon_path=window_icon_path,
        layer=LAYER_DIALOG,
        fade=fade,
        hide_semantics=hide_on_actions,
        non_closing_actions=non_closing_actions,
        border_frame=True,
        border_width=chrome.border,
    )


def announcement_body(html: str, *, mode: str | None = None) -> RichTextSpec:
    """Rich-text body for escaped prose."""

    chrome = get_popup_chrome()
    return RichTextSpec(
        id="body", object_name="AnnouncementBody", html=html,
        document_stylesheet=announcement_document_stylesheet(mode),
        font_size=chrome.body_size, document_margin=chrome.body_padding,
    )


def announcement_panel_body(children: tuple = (), *, id: str = "body") -> LayoutSpec:
    """Body card for declared fields or controls mounted by the render host."""

    padding = get_popup_chrome().body_padding
    return LayoutSpec(
        children=children, id=id, object_name="AnnouncementBody", frame=True,
        margin=(padding,) * 4, spacing=scale_px(8, min_abs=6), stretch=1,
    )


def announcement_plain_body(text: str) -> LayoutSpec:
    """Plain-text body: scrollable label for prose that must never become markup."""

    margin = scale_px(16, min_abs=12)
    return LayoutSpec(
        id="body_area", object_name="AnnouncementBody", scroll=True,
        scroll_object_name="AnnouncementScroll",
        scroll_host_object_name="AnnouncementScrollHost",
        scroll_margin=(margin,) * 4, stretch=1,
        children=(LabelSpec(
            text=text, id="body", object_name="AnnouncementBodyLabel", plain_text=True,
            font_size=get_popup_chrome().body_size, word_wrap=True, selectable=True, valign=ALIGN_TOP,
        ),),
    )


def announcement_fields_body(
    *, status: str = "", detail: str = "", progress_min_height: int = 0,
) -> LayoutSpec:
    """Stacked label/field body used by the update window."""

    padding = get_popup_chrome().body_padding
    children: list = [
        LabelSpec(status, id="status", bold=True, word_wrap=True, plain_text=True),
        LabelSpec(detail, id="detail", word_wrap=True, plain_text=True,
                  selectable=True, stretch=1, valign=ALIGN_TOP),
    ]
    if progress_min_height:
        children.append(ProgressBarSpec(id="progress", min_height=progress_min_height))
    return LayoutSpec(
        id="body", object_name="AnnouncementBody", frame=True,
        margin=(padding,) * 4, spacing=scale_px(8, min_abs=6), stretch=1,
        children=tuple(children),
    )


def announcement_approval_body(reason: str, command_text: str = "") -> LayoutSpec:
    """Approval body: reason, optional command preview, scope note."""

    padding = get_popup_chrome().body_padding
    children: list = [
        LabelSpec(reason, id="reason", word_wrap=True, plain_text=True, selectable=True),
    ]
    if command_text:
        children.append(TextAreaSpec(
            text=command_text, id="command", object_name="AnnouncementCode",
            font_size=scale_px(10, min_abs=9), readonly=True,
            max_height=scale_px(150, min_abs=120),
        ))
    children.append(LabelSpec(
        text="“始终允许”仅对当前任务有效，任务结束后自动失效。",
        id="scope", word_wrap=True, plain_text=True,
    ))
    return LayoutSpec(
        id="body", object_name="AnnouncementBody", frame=True,
        margin=(padding,) * 4, spacing=scale_px(8, min_abs=6), stretch=1,
        children=tuple(children),
    )


def announcement_update_body(status: str = "", detail: str = "") -> LayoutSpec:
    """Update body: status, detail and the shared progress bar."""

    return announcement_fields_body(
        status=status, detail=detail, progress_min_height=scale_px(22, min_abs=18),
    )



def text_paragraph(text: str, tag: str = "p") -> str:
    """Render caller-provided text literally, including file paths and line breaks."""

    escaped = escape(str(text)).replace("\n", "<br>")
    return f"<{tag}>{escaped}</{tag}>"


def announcement_document_stylesheet(mode: str | None = None) -> str:
    c = get_workbench_tokens(mode)
    font_family = get_ui_font_family().replace("'", "\\'")
    return f"""
        body {{
            color: {c['text_muted']};
            font-family: '{font_family}';
            font-size: {scale_px(13, min_abs=11)}px;
            line-height: 1.32;
            margin: 0;
        }}
        h1 {{
            color: {c['text']};
            font-size: {scale_px(17, min_abs=14)}px;
            font-weight: 700;
            margin: 0 0 {scale_px(8, min_abs=6)}px 0;
        }}
        h2 {{
            color: {c['cyan']};
            font-size: {scale_px(14, min_abs=12)}px;
            font-weight: 700;
            margin: {scale_px(12, min_abs=9)}px 0 {scale_px(5, min_abs=3)}px 0;
        }}
        p {{
            color: {c['text_muted']};
            margin: 0 0 {scale_px(10, min_abs=7)}px 0;
            white-space: pre-wrap;
        }}
        pre {{ color: {c['text']}; white-space: pre-wrap; }}
    """


def announcement_stylesheet(mode: str | None = None) -> str:
    """The single pop-up stylesheet.

    Selectors are structural (header / body / role), never per-feature object
    names, so every pop-up window looks identical without carrying its own QSS.
    """

    c = get_workbench_tokens(mode)
    chrome = get_popup_chrome()
    font_family = get_ui_font_family().replace("'", "\\'")
    border = chrome.border
    radius = chrome.radius
    return f"""
        QWidget {{
            color: {c['text']};
            font-family: '{font_family}';
            font-size: {chrome.body_size}px;
        }}
        QFrame#AnnouncementHeaderAccent {{
            background: {c['pink']}; border: none;
            border-right: {border}px solid {c['cyan']};
        }}
        QLabel#AnnouncementHeader {{
            color: {c['text']}; font-size: {chrome.title_size}px; font-weight: 700;
        }}
        QLabel#AnnouncementSource {{
            color: {c['text_dim']}; font-size: {chrome.source_size}px; font-weight: 500;
        }}
        QLabel#AnnouncementChannel {{
            color: {c['text_dim']}; font-size: {chrome.channel_size}px; font-weight: 500;
        }}
        QLabel {{
            color: {c['text']};
            font-size: {chrome.body_size}px;
        }}
        QTextBrowser#AnnouncementBody, QFrame#AnnouncementBody {{
            background: {c['surface']}; color: {c['text_muted']};
            border: {border}px solid {c['border']}; border-top-color: {c['border_strong']};
            border-radius: {radius}px; padding: 0px;
        }}
        QLabel#AnnouncementBodyLabel {{
            background: transparent; color: {c['text_muted']};
            border: none; padding: {scale_px(14, min_abs=11)}px;
            font-size: {chrome.body_size}px;
        }}
        QScrollArea#AnnouncementBody {{
            background: {c['surface']};
            border: {border}px solid {c['border']}; border-top-color: {c['border_strong']};
            border-radius: {radius}px;
        }}
        QScrollArea#AnnouncementScroll, QWidget#AnnouncementScrollHost {{
            background: transparent; border: none;
        }}
        QScrollArea#AnnouncementBody QWidget#qt_scrollarea_viewport,
        QScrollArea#AnnouncementBody QWidget#qt_scrollarea_viewport QWidget {{
            background: transparent;
        }}
        QPlainTextEdit#AnnouncementCode {{
            background: {c['surface_raised']}; color: {c['text']};
            border: {border}px solid {c['border']}; border-radius: {radius}px;
            padding: {scale_px(8, min_abs=6)}px;
            font-size: {scale_px(10, min_abs=9)}px;
        }}
        QComboBox {{
            background: {c['surface_raised']}; color: {c['text']};
            border: {border}px solid {c['border']}; border-radius: {radius}px;
            min-height: {scale_px(32, min_abs=28)}px;
            padding: 0px {scale_px(30, min_abs=26)}px 0px {scale_px(9, min_abs=7)}px;
            font-size: {chrome.body_size}px;
        }}
        QComboBox:hover {{ background: {c['surface_hover']}; border-color: {c['cyan']}; }}
        QComboBox::drop-down {{
            subcontrol-origin: padding; subcontrol-position: top right;
            width: {scale_px(26, min_abs=22)}px; background: transparent; border: none;
        }}
        QComboBox::down-arrow {{
            image: url(resc/ui/combo_down_arrow.svg);
            width: {scale_px(12, min_abs=10)}px; height: {scale_px(8, min_abs=6)}px;
        }}
        QComboBox QAbstractItemView {{
            background: {c['surface']}; color: {c['text']};
            border: {border}px solid {c['border_strong']}; outline: none;
            selection-background-color: {c['surface_hover']}; selection-color: {c['text']};
            font-size: {chrome.body_size}px;
        }}
        QCheckBox {{ color: {c['text']}; font-size: {chrome.body_size}px; }}
        QLineEdit, QTextEdit, QPlainTextEdit {{
            background: {c['surface_raised']}; color: {c['text']};
            border: {border}px solid {c['border']}; border-radius: {radius}px;
            padding: {scale_px(4, min_abs=3)}px {scale_px(7, min_abs=5)}px;
            font-size: {chrome.body_size}px;
            selection-background-color: {c['surface_hover']}; selection-color: {c['text']};
        }}
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border-color: {c['cyan']}; }}
        QTreeView, QListView {{
            background: {c['surface']}; color: {c['text']};
            border: {border}px solid {c['border']}; border-radius: {radius}px;
            font-size: {chrome.body_size}px;
            alternate-background-color: {c['surface_raised']};
            selection-background-color: {c['surface_hover']}; selection-color: {c['text']};
        }}
        QTreeView::item:hover, QListView::item:hover {{ background: {c['surface_hover']}; }}
        QHeaderView::section {{
            background: {c['surface_raised']}; color: {c['text_muted']};
            border: none; border-bottom: {border}px solid {c['border']};
            padding: {scale_px(3, min_abs=2)}px {scale_px(6, min_abs=4)}px;
            font-size: {chrome.action_size}px;
        }}
        QLabel[tone="error"] {{ color: {c['danger']}; }}
        QToolTip {{
            background: {c['surface_raised']}; color: {c['text']};
            border: {border}px solid {c['border_strong']}; padding: {scale_px(4, min_abs=3)}px;
        }}
        QLabel#WorkbenchFloatingTitle {{ color: {c['text']}; font-weight: 700; }}
        QLabel#WorkbenchFloatingSubtitle, QLabel#WorkbenchFloatingMeta {{
            color: {c['text_muted']};
        }}
        QToolButton {{
            background: transparent; color: {c['text_muted']};
            border: {border}px solid transparent; border-radius: {radius}px;
            font-size: {scale_px(17, min_abs=15)}px; font-weight: 500; padding: 0px;
        }}
        QToolButton:hover {{
            background: {c['surface_hover']}; color: {c['text']}; border-color: {c['border']};
        }}
        QToolButton#WorkbenchWindowButton {{
            background: transparent; border: none; border-radius: {radius}px;
            min-width: {scale_px(32, min_abs=28)}px; min-height: {scale_px(32, min_abs=28)}px;
            max-width: {scale_px(32, min_abs=28)}px; max-height: {scale_px(32, min_abs=28)}px;
            padding: 0px;
        }}
        QToolButton#WorkbenchWindowButton:hover {{ background: {c['surface_hover']}; }}
        QToolButton#WorkbenchWindowButton[danger="true"]:hover {{ background: {c['danger']}; }}
        QPushButton {{
            background: {c['surface_raised']}; color: {c['text']};
            border: {border}px solid {c['border']}; border-radius: {radius}px;
            padding: {scale_px(4, min_abs=3)}px {scale_px(10, min_abs=8)}px;
            font-size: {chrome.action_size}px; font-weight: 600;
        }}
        QPushButton:hover {{ background: {c['surface_hover']}; border-color: {c['cyan']}; }}
        QPushButton:pressed {{ background: {c['border_strong']}; }}
        QPushButton:focus {{ border-color: {c['cyan']}; }}
        QPushButton:disabled {{ color: {c['text_dim']}; }}
        QPushButton[windowActionRole="primary"] {{
            background: {c['pink']}; color: {c['canvas']}; border-color: {c['pink']};
        }}
        QPushButton[windowActionRole="primary"]:hover {{
            background: {c['pink_hover']}; border-color: {c['pink_hover']};
        }}
        QPushButton[windowActionRole="accent"] {{
            background: {c['cyan']}; color: {c['canvas']}; border-color: {c['cyan']};
        }}
        QPushButton[windowActionRole="accent"]:hover {{
            background: {c['pink_hover']}; border-color: {c['pink_hover']};
        }}
        QPushButton[windowActionRole="danger"] {{
            background: {c['danger']}; color: {c['canvas']}; border-color: {c['danger']};
        }}
        QPushButton[windowActionRole="danger"]:hover {{ border-color: {c['text']}; }}
        QProgressBar {{
            background: {c['surface_raised']}; color: {c['text']}; text-align: center;
            border: {border}px solid {c['border']}; border-radius: {radius}px;
        }}
        QProgressBar::chunk {{ background: {c['cyan']}; border-radius: {radius}px; }}
        QProgressBar[windowProgressRole="accent"]::chunk {{ background: {c['cyan']}; }}
        QProgressBar[windowProgressRole="highlight"]::chunk {{ background: {c['pink']}; }}
        QScrollBar:vertical {{
            background: {c['surface']}; width: {scale_px(8, min_abs=7)}px; border: none;
        }}
        QScrollBar::handle:vertical {{
            background: {c['border_strong']}; min-height: {scale_px(28, min_abs=22)}px;
            border: none; border-radius: {scale_px(3, min_abs=2)}px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {c['cyan']}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; border: none; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    """


__all__ = [
    "PopupAction",
    "PopupChrome",
    "PopupDrawChrome",
    "PopupGeometry",
    "announcement_approval_body",
    "announcement_body",
    "announcement_document_stylesheet",
    "announcement_fields_body",
    "announcement_panel_body",
    "announcement_plain_body",
    "announcement_shell_spec",
    "announcement_stylesheet",
    "announcement_update_body",
    "build_popup_draw_chrome",
    "get_popup_chrome",
    "resolve_popup_geometry",
    "text_paragraph",
]
