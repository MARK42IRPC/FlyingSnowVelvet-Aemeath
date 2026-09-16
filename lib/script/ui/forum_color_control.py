"""雪绒论坛共用的取色控件：一个复选框 + 两条「色相 / 明度」滑条。

留言墙与主论坛发帖页都要选颜色，控件放在这里两边共用，别各写一份：`forum_window` 的留言
发帖框用它给整张卡片选色，`forum_board` 的发帖页用它给选中的一段文字选色（一篇帖子因此
可以有几段不同的颜色）。

`ForumColorToggle` 是复选框本身：样式表给 `::indicator` 画了背景之后 Qt 就不画原生勾选标记
了，所以这里按 `QStyle` 给出的 indicator 矩形自己描一个对勾。`ForumColorControl` 把它与
`ForumColorSlider` 的两条滑条装在一起，`color()` / `token_color()` 给出「当前色」与「写进
正文令牌的颜色」——不勾选就返回主题默认色，卡片上因此不会平白多出一串颜色令牌。

`format_button_font()` 也放在这里：它是「按钮字符自己就是效果示例」的字体（B 加粗、I 斜体、
U 下划线、S 删除线），两边的格式工具栏都用它。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QStyle,
    QStyleOptionButton,
    QVBoxLayout,
    QWidget,
)

from config.scale import scale_px
from lib.core.qt_bridge.font import get_ui_font
from lib.script.ui.forum_color_picker import (
    MAX_LIGHTNESS,
    MIN_LIGHTNESS,
    ForumColorSlider,
    color_to_hsl,
    hsl_color,
    hue_gradient_stops,
)
from lib.script.ui.forum_style import forum_card_text_color


def format_button_font(key: str):
    """按钮字符自己就是效果示例：B 加粗、I 斜体、U 下划线、S 删除线。"""
    font = get_ui_font(size=scale_px(12, min_abs=10))
    font.setBold(key == "bold")
    font.setItalic(key == "italic")
    font.setUnderline(key == "underline")
    font.setStrikeOut(key == "strike")
    return font


class ForumColorToggle(QCheckBox):
    """论坛取色开关：勾选后在方框里补一个对勾。

    样式表一旦给 `::indicator` 画了背景，Qt 就不再画原生的勾选标记，方框会变成一个实心
    色块——看上去分不清是「已勾选」还是「只是个色块」。这里按 `QStyle` 给出的 indicator
    矩形自己描一个对勾，不依赖字体字形，12px 的小方框里也能画准。
    """

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self.isChecked():
            return
        option = QStyleOptionButton()
        self.initStyleOption(option)
        rect = self.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, self)
        if rect.width() <= 2 or rect.height() <= 2:
            return
        pen = QPen(QColor(0, 0, 0, 170))
        pen.setWidthF(max(1.4, rect.height() * 0.16))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        path = QPainterPath()
        path.moveTo(rect.x() + rect.width() * 0.26, rect.y() + rect.height() * 0.52)
        path.lineTo(rect.x() + rect.width() * 0.44, rect.y() + rect.height() * 0.70)
        path.lineTo(rect.x() + rect.width() * 0.76, rect.y() + rect.height() * 0.30)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(pen)
        painter.drawPath(path)
        painter.end()


class ForumColorControl(QWidget):
    """一组「色相 + 明度」滑条：勾选后才给正文或描边选颜色。

    每条颜色控件自带一个复选框：不勾选就收起两条滑条、直接用主题默认色，卡片上也就不写
    颜色令牌；勾选后才铺开滑条自选。默认的留言因此不会平白多出一串颜色令牌，想改色的
    人也有一个明确的入口。

    两个滑条都是 0.0–1.0 的比例，真正换算成颜色的是 `forum_color_picker.hsl_color()`；
    色相条铺整圈彩虹，明度条铺当前色相的暗→亮渐变，拖哪一条都能立刻从滑条本身看出结果。
    控件对外暴露 `color()`（当前色）、`token_color()`（写进正文的颜色）、`is_enabled()`
    与 `colorChanged`，调用方不用认识 HSL。

    `show_toggle=False` 给主论坛发帖页用：那里的开关是工具栏上的「文字色 / 描边色」两个按钮
    （一篇帖子可以有好几段颜色，颜色得跟着选区走），所以这一个复选框反而是多余的；标题改成
    一行小字，滑条常驻。留言墙不传它，还是「勾了才铺开滑条」那套。
    """

    colorChanged = pyqtSignal(str)

    def __init__(
        self, parent: QWidget | None = None, label: str = "颜色", *, show_toggle: bool = True
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ForumColorControl")
        self._hue = 0.0
        self._lightness = 0.85
        self._show_toggle = bool(show_toggle)
        # 没有复选框时滑条常驻（发帖页的按钮就是那个开关），所以初始就是启用状态。
        self._enabled = not self._show_toggle

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(scale_px(3, min_abs=2))

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(scale_px(5, min_abs=4))
        # 复选框自己带标题：文字也是可点区域，比一个光秃秃的小方框好按。
        self._toggle = ForumColorToggle(label, self)
        self._toggle.setObjectName("ForumColorToggle")
        self._toggle.setFont(get_ui_font(size=scale_px(11, min_abs=9)))
        self._toggle.setCursor(Qt.PointingHandCursor)
        self._toggle.setToolTip(f"勾选后才能自定义{label}；不勾选就跟着主题的默认色走。")
        self._toggle.setVisible(self._show_toggle)
        self._toggle.toggled.connect(self._on_toggled)
        if not self._show_toggle:
            title = QLabel(label, self)
            # 标题这一格与复选框同高同字，只是不可点：按钮在工具栏上，这里只报色。
            title.setObjectName("ForumFieldLabel")
            title.setFont(get_ui_font(size=scale_px(11, min_abs=9)))
            header.addWidget(title, 0)
        self._preview = QFrame(self)
        self._preview.setObjectName("ForumColorPreview")
        self._preview.setFixedSize(scale_px(22, min_abs=19), scale_px(12, min_abs=10))
        header.addWidget(self._toggle, 0)
        header.addWidget(self._preview, 0)
        header.addStretch(1)
        layout.addLayout(header)

        self._hue_slider = ForumColorSlider(
            tooltip=f"{label}：拖动选择色相",
            gradient_stops=hue_gradient_stops(),
            parent=self,
        )
        # 高度留给 ForumColorSlider 自己按共享滑条（音乐进度条）定，这里只定宽度：
        # 两个颜色控件并排，各自给一条能看出渐变的宽度。
        self._hue_slider.setMinimumWidth(scale_px(170, min_abs=140))
        layout.addWidget(self._hue_slider)

        self._lightness_slider = ForumColorSlider(
            tooltip=f"{label}：拖动选择明度",
            parent=self,
        )
        self._lightness_slider.setMinimumWidth(scale_px(170, min_abs=140))
        layout.addWidget(self._lightness_slider)

        self._hue_slider.valueChanged.connect(self._on_hue_changed)
        self._lightness_slider.valueChanged.connect(self._on_lightness_changed)
        self._apply_enabled()
        self._sync_preview()

    # ── 对外 ─────────────────────────────────────────────────────────

    def is_enabled(self) -> bool:
        """复选框是否勾选；没勾选时滑条收起，用的就是主题默认色。"""
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._toggle.setChecked(bool(enabled))

    def color(self) -> str:
        """当前颜色：没勾选时是主题默认色，勾选后是滑条上的颜色。"""
        return self._slider_color() if self._enabled else self._default_color()

    def token_color(self) -> str:
        """写进正文令牌的颜色；没勾选时返回空串，卡片于是用默认色。"""
        return self._slider_color() if self._enabled else ""

    def set_color(self, color: str | None) -> None:
        """按颜色反推两个滑条的位置并勾选这条颜色；传 None 表示回到默认色。"""
        if color:
            hue, lightness = color_to_hsl(color)
            self._hue = max(0.0, min(1.0, hue / 360.0))
            span = max(1e-6, MAX_LIGHTNESS - MIN_LIGHTNESS)
            self._lightness = max(
                0.0, min(1.0, (lightness - MIN_LIGHTNESS) / span)
            )
        else:
            self._hue, self._lightness = 0.0, 0.85
        # 先摆好滑条再切复选框：切换会发 colorChanged，那一刻颜色应该已经是新的。
        self._hue_slider.set_ratio(self._hue)
        self._lightness_slider.set_ratio(self._lightness)
        self._toggle.setChecked(bool(color))
        self._sync_preview()

    # ── 内部 ─────────────────────────────────────────────────────────

    def _on_toggled(self, checked: bool) -> None:
        self._enabled = bool(checked)
        self._apply_enabled()
        self._sync_preview()
        self.colorChanged.emit(self.color())

    def _apply_enabled(self) -> None:
        """没勾选就把两条滑条收起来，控件只剩标题与预览色块。"""
        for slider in (self._hue_slider, self._lightness_slider):
            slider.setVisible(self._enabled)

    @staticmethod
    def _default_color() -> str:
        return forum_card_text_color()

    def _slider_color(self) -> str:
        return hsl_color(self._hue * 360.0, self._lightness).name()

    def _on_hue_changed(self, ratio: float) -> None:
        self._hue = float(ratio)
        self._sync_preview()
        self.colorChanged.emit(self.color())

    def _on_lightness_changed(self, ratio: float) -> None:
        self._lightness = float(ratio)
        self._sync_preview()
        self.colorChanged.emit(self.color())

    def _sync_preview(self) -> None:
        color = self.color()
        self._preview.setStyleSheet(
            f"background: {color}; border: 1px solid rgba(0, 0, 0, 0.35);"
        )
        # 明度条按当前色相重铺渐变，拖色相时它跟着变。
        stops = tuple(
            (index / 6.0, hsl_color(self._hue * 360.0, MIN_LIGHTNESS + (MAX_LIGHTNESS - MIN_LIGHTNESS) * index / 6.0).name())
            for index in range(7)
        )
        self._lightness_slider.set_gradient_stops(stops)


__all__ = [
    "ForumColorControl",
    "ForumColorToggle",
    "format_button_font",
]
