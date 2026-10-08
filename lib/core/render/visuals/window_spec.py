"""窗口级描述层：把「一个窗口长什么样」从工具包里抽出来。

`controls.py` 描述的是**单个叶控件**（气泡、进度条、按钮…）；本模块补上它上一级的
缺口——描述**一个窗口**：窗口标志、内容树（容器 / 文本 / 图标 / 按钮 / 输入区）与
交互绑定点。产品窗口只负责收集状态并产出一棵 `WindowSpec`，真实控件树由后端宿主
（`lib/core/render/backends/qt/widgets/spec_host.py`）按描述装配。

这一层回答的问题是「窗口里有什么、怎么排、点下去意味着哪个语义 id」，不回答
「用什么工具包搭出来」。因此本模块只依赖后端中立类型，不 import `PyQt5`、任一
`backends/*`、`lib.script` 或 `config.config_ui`——与 `visuals/` 其余模块同一约束。

尺寸、间距、字号都是显式数值：产品面用 `config.scale.scale_px` 算好再填进来，
`lib/core/render` 因此不需要反向 import 产品配置。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterator

# ── 窗口种类 ─────────────────────────────────────────────────────────────
WINDOW_DIALOG = "dialog"
WINDOW_TOOL = "tool"
WINDOW_WINDOW = "window"

# ── 顶层窗口层级（宿主按名字注册到 `LayerManager`）────────────────────────
LAYER_DIALOG = "dialog"

# ── 排布方向 ─────────────────────────────────────────────────────────────
LAYOUT_ROW = "row"
LAYOUT_COLUMN = "column"

# ── 对齐 ─────────────────────────────────────────────────────────────────
ALIGN_TOP = "top"
ALIGN_VCENTER = "vcenter"
ALIGN_BOTTOM = "bottom"
ALIGN_LEFT = "left"
ALIGN_HCENTER = "hcenter"
ALIGN_RIGHT = "right"

# ── 按钮语义 ─────────────────────────────────────────────────────────────
ROLE_DEFAULT = "default"
ROLE_PRIMARY = "primary"
ROLE_DANGER = "danger"
ROLE_GHOST = "ghost"

# ── 颜色语义（宿主按当前主题解析成具体颜色）───────────────────────────────
COLOR_TEXT = "text"
COLOR_CANVAS = "canvas"
COLOR_WARNING = "warning"
COLOR_DANGER = "danger"

_MARGIN = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class StretchSpec:
    """弹性空隙：吸收所在主轴上的剩余空间。"""

    weight: float = 1.0
    id: str = ""


@dataclass(frozen=True, slots=True)
class SpacerSpec:
    """固定空隙。"""

    size: int = 0
    id: str = ""


@dataclass(frozen=True, slots=True)
class LabelSpec:
    """一行文字。字号与字重由产品面算好，宿主只负责装配。"""

    text: str
    id: str = ""
    object_name: str = ""
    font_size: int = 0
    bold: bool = False
    word_wrap: bool = False
    selectable: bool = False
    #: 按纯文本渲染（``Qt.PlainText``）：帮助正文一类的说明文字不该被当成富文本。
    plain_text: bool = False
    align: str = ""
    valign: str = ""
    #: 在父布局里占的伸缩权重（原文用 ``addWidget(label, 1)`` 撑满剩余空间）。
    stretch: int = 0


@dataclass(frozen=True, slots=True)
class IconSpec:
    """一枚按名字渲染的矢量图标（图形事实源在 `visuals/office_icons.py`）。"""

    name: str
    id: str = ""
    object_name: str = ""
    color: str = COLOR_TEXT
    size: int = 0
    valign: str = ALIGN_TOP


@dataclass(frozen=True, slots=True)
class AccentBarSpec:
    """办公面顶部的双色 accent bar（青 / 粉各半）。"""

    id: str = ""
    object_name: str = "OfficeAccentBar"
    height: int = 0


@dataclass(frozen=True, slots=True)
class TextAreaSpec:
    """只读/只进的多行文本区（命令预览一类的等宽展示）。"""

    text: str
    id: str = ""
    object_name: str = ""
    font_size: int = 0
    readonly: bool = True
    monospace: bool = False
    max_height: int = 0


@dataclass(frozen=True, slots=True)
class RichTextSpec:
    """富文本视图（``QTextBrowser``）：公告正文一类的只读 HTML 展示。

    ``document_stylesheet`` 是 QTextDocument 层的默认样式表（不是控件 QSS），
    调用方已经算好颜色与字号；``scroll`` 由承载它的 ``LayoutSpec`` 决定。
    """

    html: str = ""
    id: str = ""
    object_name: str = ""
    document_stylesheet: str = ""
    font_size: int = 0
    document_margin: int = 0
    open_external_links: bool = False


@dataclass(frozen=True, slots=True)
class ProgressBarSpec:
    """一条进度条（``QProgressBar``）：更新浮窗的下载/同步进度。"""

    id: str = ""
    object_name: str = ""
    text_visible: bool = True
    minimum: int = 0
    maximum: int = 1
    value: int = 0
    fmt: str = ""
    min_height: int = 0


@dataclass(frozen=True, slots=True)
class ButtonSpec:
    """一枚按钮：`semantic` 是给宿主回填回调用的语义 id。"""

    text: str
    semantic: str
    id: str = ""
    object_name: str = ""
    role: str = ROLE_DEFAULT
    icon: str = ""
    icon_color: str = COLOR_TEXT
    tooltip: str = ""
    font_size: int = 0
    bold: bool = False
    #: 无障碍名（读屏与自动化测试用的可读标签）。
    accessible_name: str = ""
    #: 固定尺寸档（0 表示不设）；窗眉里的关闭按钮靠它定宽高。
    fixed_size: tuple[int, int] = (0, 0)
    #: 本按钮在父布局里的对齐（``ALIGN_TOP`` 等）。
    valign: str = ""
    #: 用 ``QToolButton`` 而不是 ``QPushButton`` 承载（QSS 的 ``QToolButton#Name`` 选择器需要它）。
    tool_button: bool = False
    #: 用标准窗眉字形（``close`` / ``minimize`` 一类）而不是产品图标；非空时忽略 ``icon``。
    window_icon: str = ""
    #: 最小高度档（0 表示不设）；公告动作按钮靠它对齐工作台 34px 档。
    min_height: int = 0
    #: 用后端 UI 字体（调用方不指定字号时的默认字体）渲染按钮文字。
    default_font: bool = False


@dataclass(frozen=True, slots=True)
class LayoutSpec:
    """一个容器：沿 `direction` 排列 `children`。"""

    children: tuple = ()
    id: str = ""
    object_name: str = ""
    direction: str = LAYOUT_COLUMN
    margin: _MARGIN = (0, 0, 0, 0)
    spacing: int = 0
    stretch: int = 0
    top_margin: int = 0
    #: 用 ``QFrame`` 而不是裸 ``QWidget`` 承载（QSS 的 ``QFrame#Name`` 选择器需要它）。
    frame: bool = False
    #: 本容器作为一个整体在父布局里的对齐（``LayoutSpec`` 也能当孩子，理由同 ``LabelSpec``）。
    align: str = ""
    valign: str = ""
    #: 固定尺寸档（0 表示不设）；窗眉里的横条 / 竖条靠它定宽高。
    fixed_width: int = 0
    fixed_height: int = 0
    #: 让本容器的孩子住进一个平滑滚动视口（长正文用）。
    scroll: bool = False
    #: 滚动视口与内部承载控件的对象名（QSS 选择器用）。
    scroll_object_name: str = ""
    scroll_host_object_name: str = ""
    #: 滚动视口内部承载控件的内边距。
    scroll_margin: _MARGIN = (0, 0, 0, 0)
    #: 子控件全部隐藏时自动收起本行（连同父布局间距）。
    collapse_when_empty: bool = False


@dataclass(frozen=True, slots=True)
class WindowSpec:
    """一个窗口的完整后端中立描述。"""

    content: LayoutSpec
    object_name: str = ""
    title: str = ""
    kind: str = WINDOW_DIALOG
    frameless: bool = True
    stay_on_top: bool = True
    modal: bool = False
    translucent: bool = False
    delete_on_close: bool = False
    styled_background: bool = True
    min_width: int = 0
    max_width: int = 0
    stylesheet: str = ""
    window_icon_path: str = ""
    center_on_parent: bool = True
    center_on_cursor_screen: bool = True
    #: 这些 id 指向的子项可拖动整窗（无原生标题栏时用窗眉拖）。
    drag_handle_ids: tuple[str, ...] = ()
    #: 关闭按钮语义（点它等于选哪个动作）；空串表示不拦截关闭。
    close_semantic: str = ""
    #: 顶层窗口层级名（``LAYER_*``）；空串表示不进 `LayerManager`。
    layer: str = ""
    #: 固定尺寸（0 表示不设）；两个方向都给值时等价于 ``setFixedSize()``。
    fixed_size: tuple[int, int] = (0, 0)
    #: 显示/隐藏走 ``windowOpacity`` 淡入淡出（时长由宿主取共享的 UI 淡入时长）。
    fade: bool = False
    #: 这几个语义只收起窗口（淡出后 ``hide()``），不结束窗口生命周期。
    hide_semantics: tuple[str, ...] = ()
    #: 用 ``paintEvent`` 画一层描边外壳（外描边色 + 内容底色），与公告 / 更新浮窗一致。
    border_frame: bool = False
    border_width: int = 0
    #: 描边外壳的中间内衬色 token（空串表示两层式；更新浮窗用 ``border``）。
    border_mid: str = ""
    #: 描边外壳的内容底色 token（默认 ``canvas``；更新浮窗用 ``surface``）。
    border_fill: str = "canvas"

    def with_text(self, node_id: str, text: str) -> "WindowSpec":
        """返回把 `node_id` 文本换成 `text` 的新描述（见 `retarget_text`）。"""

        return retarget_text(self, node_id, text)


# ── 遍历与查询（纯数据，供产品面与测试使用）─────────────────────────────

def iter_specs(spec) -> Iterator:
    """深度优先遍历描述树，产出每个节点（含根内容布局）。

    传入 `WindowSpec` 时从其 `content` 起遍历，传入任一节点时从该节点起遍历，
    产品面与测试因此不必先自己摘出根布局。
    """

    stack = [spec.content if isinstance(spec, WindowSpec) else spec]
    while stack:
        node = stack.pop()
        if node is None:
            continue
        yield node
        children = getattr(node, "children", None)
        if children:
            stack.extend(reversed(children))


def find_spec(spec, node_id: str):
    """按 `id` 找节点；找不到返回 `None`。"""

    if not node_id:
        return None
    for node in iter_specs(spec):
        if getattr(node, "id", "") == node_id:
            return node
    return None


def button_specs(spec) -> tuple[ButtonSpec, ...]:
    """描述树里所有按钮，按出现顺序。"""

    return tuple(node for node in iter_specs(spec) if isinstance(node, ButtonSpec))


def retarget_text(spec: WindowSpec, node_id: str, text: str) -> WindowSpec:
    """返回一份把 `node_id` 的文本换成 `text` 的新描述（窗口复用换内容用）。

    只重建从根到目标的那条路径，其余节点共享原对象——描述是不可变数据，
    允许安全共享，避免为了改一行字把整棵树重搭一遍。
    """

    def _replace(node):
        if getattr(node, "id", "") == node_id and hasattr(node, "text"):
            return replace(node, text=str(text))
        children = getattr(node, "children", None)
        if children:
            replaced = tuple(_replace(child) for child in children)
            if replaced != children:
                return replace(node, children=replaced)
            return node
        content = getattr(node, "content", None)
        if content is not None:
            replaced = _replace(content)
            if replaced is not content:
                return replace(node, content=replaced)
        return node

    return _replace(spec)


__all__ = [
    "ALIGN_BOTTOM",
    "ALIGN_HCENTER",
    "ALIGN_LEFT",
    "ALIGN_RIGHT",
    "ALIGN_TOP",
    "ALIGN_VCENTER",
    "AccentBarSpec",
    "ButtonSpec",
    "COLOR_CANVAS",
    "COLOR_DANGER",
    "COLOR_TEXT",
    "COLOR_WARNING",
    "IconSpec",
    "LAYER_DIALOG",
    "LAYOUT_COLUMN",
    "LAYOUT_ROW",
    "LabelSpec",
    "LayoutSpec",
    "ProgressBarSpec",
    "RichTextSpec",
    "ROLE_DANGER",
    "ROLE_DEFAULT",
    "ROLE_GHOST",
    "ROLE_PRIMARY",
    "SpacerSpec",
    "StretchSpec",
    "TextAreaSpec",
    "WINDOW_DIALOG",
    "WINDOW_TOOL",
    "WINDOW_WINDOW",
    "WindowSpec",
    "button_specs",
    "find_spec",
    "iter_specs",
    "retarget_text",
]
