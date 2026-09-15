"""Responsive layout primitives shared by workbench settings pages."""

from __future__ import annotations

from collections.abc import Callable

from PyQt5.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PyQt5.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QComboBox,
    QCheckBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from lib.core.qt_bridge.font import get_ui_font
from config.scale import scale_px


SETTINGS_LABEL_WIDTH = scale_px(176, min_abs=156)
SETTINGS_FONT_SIZE = scale_px(17, min_abs=12)
SETTINGS_HINT_FONT_SIZE = max(scale_px(12, min_abs=9), SETTINGS_FONT_SIZE - scale_px(2, min_abs=1))


class SettingsFormLayout(QFormLayout):
    """Form layout with one label column and one responsive field column."""

    def __init__(self) -> None:
        super().__init__()
        self.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.setHorizontalSpacing(scale_px(14, min_abs=10))
        self.setVerticalSpacing(scale_px(11, min_abs=8))

    def addRow(self, *args) -> None:
        super().addRow(*args)
        self._normalize_row(self.rowCount() - 1)

    def _normalize_row(self, row: int) -> None:
        label_item = self.itemAt(row, QFormLayout.LabelRole)
        label = label_item.widget() if label_item is not None else None
        if isinstance(label, QLabel):
            label.setObjectName("ConfigFormLabel")
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            label.setFixedWidth(SETTINGS_LABEL_WIDTH)

        field_item = self.itemAt(row, QFormLayout.FieldRole)
        field = field_item.widget() if field_item is not None else None
        if isinstance(field, QWidget):
            policy = field.sizePolicy()
            field.setSizePolicy(QSizePolicy.Expanding, policy.verticalPolicy())
            field.setMinimumWidth(0)
            field.setMaximumWidth(16777215)


def create_settings_form() -> SettingsFormLayout:
    return SettingsFormLayout()


class SmoothScrollArea(QScrollArea):
    """滚轮平滑滚动容器：把离散滚动步进变成短动画过渡，设置页共用同一份手感。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._wheel_target_value = 0
        self._wheel_pending_px = 0.0
        self._wheel_anim = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._wheel_anim.setEasingCurve(QEasingCurve.OutQuart)
        self._wheel_anim.setDuration(160)
        bar = self.verticalScrollBar()
        bar.setSingleStep(scale_px(24, min_abs=18))
        bar.setPageStep(scale_px(120, min_abs=96))
        bar.rangeChanged.connect(self._on_scroll_range_changed)

    def _on_scroll_range_changed(self, minimum: int, maximum: int) -> None:
        self._wheel_target_value = max(minimum, min(maximum, self._wheel_target_value))

    def wheelEvent(self, event) -> None:
        bar = self.verticalScrollBar()
        if bar is None or bar.maximum() <= bar.minimum():
            super().wheelEvent(event)
            return

        if not event.pixelDelta().isNull():
            delta_px = float(event.pixelDelta().y())
        else:
            angle_y = int(event.angleDelta().y())
            if angle_y == 0:
                super().wheelEvent(event)
                return
            delta_px = float(angle_y) / 120.0 * float(scale_px(48, min_abs=36))

        if abs(delta_px) < 1e-6:
            event.accept()
            return

        self._wheel_pending_px += delta_px
        scroll_delta = int(self._wheel_pending_px)
        if scroll_delta == 0:
            event.accept()
            return
        self._wheel_pending_px -= float(scroll_delta)

        current = int(bar.value())
        base = self._wheel_target_value if self._wheel_anim.state() == QPropertyAnimation.Running else current
        target = int(round(base - scroll_delta))
        target = max(bar.minimum(), min(bar.maximum(), target))
        if target == current:
            self._wheel_pending_px = 0.0
            event.accept()
            return

        distance = abs(target - current)
        duration = max(110, min(280, int(120 + distance * 0.45)))

        self._wheel_target_value = target
        self._wheel_anim.stop()
        self._wheel_anim.setDuration(duration)
        self._wheel_anim.setStartValue(current)
        self._wheel_anim.setEndValue(target)
        self._wheel_anim.start()
        event.accept()


class SettingsPageHeader(QFrame):
    def __init__(self, title: str, description: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsPageHeader")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("SettingsPageTitle")
        title_font = get_ui_font(size=scale_px(19, min_abs=16))
        title_font.setBold(True)
        self.title_label.setFont(title_font)

        self.description_label = QLabel(description, self)
        self.description_label.setObjectName("SettingsPageDescription")
        self.description_label.setWordWrap(True)
        self.description_label.setFont(get_ui_font(size=scale_px(11, min_abs=9)))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, scale_px(4, min_abs=3))
        layout.setSpacing(scale_px(3, min_abs=2))
        layout.addWidget(self.title_label)
        layout.addWidget(self.description_label)


class SettingsSection(QFrame):
    def __init__(self, title: str, description: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsSection")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("SettingsSectionTitle")
        title_font = get_ui_font(size=scale_px(13, min_abs=11))
        title_font.setBold(True)
        self.title_label.setFont(title_font)

        self.description_label = QLabel(description, self)
        self.description_label.setObjectName("SettingsSectionDescription")
        self.description_label.setWordWrap(True)
        self.description_label.setFont(get_ui_font(size=scale_px(10, min_abs=9)))
        self.description_label.setVisible(bool(description))

        self.body_layout = QVBoxLayout()
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(scale_px(11, min_abs=8))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            scale_px(18, min_abs=14),
            scale_px(15, min_abs=12),
            scale_px(18, min_abs=14),
            scale_px(17, min_abs=13),
        )
        layout.setSpacing(scale_px(7, min_abs=5))
        layout.addWidget(self.title_label)
        layout.addWidget(self.description_label)
        layout.addLayout(self.body_layout)


class SettingsActionBar(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsActionBar")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        #: 左侧状态文案：只在页面有话说时占位，按钮始终靠右。
        self.status_label = QLabel("", self)
        self.status_label.setObjectName("SettingsActionStatus")
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        self.button_layout = QHBoxLayout(self)
        self.button_layout.setContentsMargins(
            scale_px(12, min_abs=10),
            scale_px(9, min_abs=7),
            scale_px(12, min_abs=10),
            scale_px(9, min_abs=7),
        )
        self.button_layout.setSpacing(scale_px(8, min_abs=6))
        # 状态文案拿走左侧的富余空间：单行显示，按钮不受文案长短影响。
        self.button_layout.addWidget(self.status_label, 1)
        self.button_layout.addStretch(1)

    def set_status(self, text: str, *, tone: str = "") -> None:
        """更新左侧状态文案；空字符串即收起这一行。"""
        message = str(text or "")
        self.status_label.setText(message)
        self.status_label.setProperty("tone", tone)
        style = self.status_label.style()
        if style is not None:
            style.unpolish(self.status_label)
            style.polish(self.status_label)
        self.status_label.setVisible(bool(message))

    def add_action(self, text: str, callback: Callable[[], None], *, primary: bool = False) -> QPushButton:
        button = QPushButton(text, self)
        button.setObjectName("SettingsPrimaryAction" if primary else "SettingsSecondaryAction")
        button.setProperty("primary", primary)
        button.clicked.connect(callback)
        self.button_layout.addWidget(button)
        return button


class SettingsPageScaffold:
    def __init__(
        self,
        page: QWidget,
        title: str,
        description: str,
        *,
        scroll_factory: type[QScrollArea] = QScrollArea,
    ) -> None:
        page.setObjectName(page.objectName() or "SettingsPage")
        page.setMinimumSize(scale_px(600, min_abs=560), scale_px(420, min_abs=380))
        page.setMaximumSize(16777215, 16777215)
        page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.root_layout = QVBoxLayout(page)
        self.root_layout.setContentsMargins(
            scale_px(14, min_abs=11),
            scale_px(12, min_abs=10),
            scale_px(14, min_abs=11),
            scale_px(12, min_abs=10),
        )
        self.root_layout.setSpacing(scale_px(12, min_abs=10))

        self.header = SettingsPageHeader(title, description, page)
        self.root_layout.addWidget(self.header)

        self.scroll = scroll_factory(page)
        self.scroll.setObjectName("SettingsPageScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.content = QWidget(self.scroll)
        self.content.setObjectName("SettingsPageContent")
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, scale_px(4, min_abs=3), 0)
        self.content_layout.setSpacing(scale_px(12, min_abs=10))
        self.scroll.setWidget(self.content)
        self.root_layout.addWidget(self.scroll, 1)

        self.action_bar = SettingsActionBar(page)
        self.action_bar.hide()
        self.root_layout.addWidget(self.action_bar)

    @property
    def title_label(self) -> QLabel:
        return self.header.title_label

    @property
    def description_label(self) -> QLabel:
        return self.header.description_label

    def add_section(self, title: str, description: str = "") -> SettingsSection:
        section = SettingsSection(title, description, self.content)
        self.content_layout.addWidget(section)
        return section

    def add_action(self, text: str, callback: Callable[[], None], *, primary: bool = False) -> QPushButton:
        self.action_bar.show()
        return self.action_bar.add_action(text, callback, primary=primary)

    def set_status(self, text: str, *, tone: str = "") -> None:
        """在底部动作条左侧显示状态文案；有内容时动作条一定可见。"""
        if str(text or ""):
            self.action_bar.show()
        self.action_bar.set_status(text, tone=tone)

    def finish(self) -> None:
        apply_settings_page_fonts(self.root_layout.parentWidget())
        self.content_layout.addStretch(1)


def apply_settings_page_fonts(page: QWidget) -> None:
    """Bring generated settings pages to the same readable scale as AI settings."""
    base_font = get_ui_font(size=SETTINGS_FONT_SIZE)
    page.setFont(base_font)

    control_font = get_ui_font(size=SETTINGS_FONT_SIZE)
    control_font.setBold(True)
    for widget_type in (QLineEdit, QComboBox, QPushButton, QCheckBox):
        for widget in page.findChildren(widget_type):
            if widget.property("preserveCustomFont"):
                continue
            widget.setFont(control_font)
            if isinstance(widget, QComboBox):
                view = widget.view()
                if view is not None:
                    view_font = get_ui_font(size=SETTINGS_HINT_FONT_SIZE)
                    view_font.setBold(True)
                    view.setFont(view_font)

    label_font = get_ui_font(size=SETTINGS_FONT_SIZE)
    label_font.setBold(True)
    hint_font = get_ui_font(size=SETTINGS_HINT_FONT_SIZE)
    for label in page.findChildren(QLabel):
        if label.property("preserveCustomFont"):
            continue
        if label.objectName() in {"SettingsPageDescription", "SettingsSectionDescription"}:
            label.setFont(hint_font)
        elif label.objectName() in {"SettingsSectionTitle", "ConfigFormLabel"}:
            label.setFont(label_font)
