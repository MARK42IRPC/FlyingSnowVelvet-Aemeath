"""工作台风格的「技能管理 / 插件管理」卡片。

卡片只负责展示与交互：条目由 `loader()` 现取，安装与删除交给 `installer` / `remover`。
「哪些是内置、装到哪里、登记在哪个文件」全部留在 `lib.script.office` 里判断，这里只读
条目自己的 `bundled` / `removable`，所以技能与插件共用同一份实现。

列表固定露出 `VISIBLE_ROWS` 行，多出来的条目在卡片内滚动；行高由字体度量算出，不随条目
内容变化，卡片高度因此是固定的，不会把工作台页面越撑越长。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtGui import QFontMetrics
from PyQt5.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config.scale import scale_px
from lib.core.logger import get_logger
from lib.core.qt_bridge.font import get_ui_font
from lib.script.ui.workbench_settings_layout import SETTINGS_FONT_SIZE, SettingsSection

logger = get_logger(__name__)

#: 卡片内一次露出多少项；多出来的项在卡片内滚动查看。
VISIBLE_ROWS = 5
#: 选中项说明的截断长度：说明可能是一整段 SKILL.md 描述，卡片里只给一行摘要。
_SUMMARY_LIMIT = 48


def _short_summary(text: str) -> str:
    """把条目说明压成一行摘要；空白与换行折叠成一个空格。"""
    collapsed = " ".join(str(text or "").split())
    if len(collapsed) <= _SUMMARY_LIMIT:
        return collapsed
    return f"{collapsed[:_SUMMARY_LIMIT].rstrip()}…"


class OfficeManagerCard(SettingsSection):
    """「技能管理 / 插件管理」卡片的共用实现。"""

    changed = pyqtSignal()

    def __init__(
        self,
        title: str,
        description: str,
        *,
        kind: str,
        loader: Callable[[], Sequence[object]],
        installer: Callable[[Path], object],
        remover: Callable[[str], object],
        pick_title: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, description, parent)
        self._kind = str(kind)
        self._loader = loader
        self._installer = installer
        self._remover = remover
        self._pick_title = str(pick_title)
        self._entries: list[object] = []
        self._build_toolbar()
        self._build_list()
        self.refresh()

    # ── 构建 ─────────────────────────────────────────────────────────

    def _build_toolbar(self) -> None:
        row = QHBoxLayout()
        row.setSpacing(scale_px(8, min_abs=6))

        button_column = QVBoxLayout()
        button_column.setContentsMargins(0, 0, 0, 0)
        button_column.setSpacing(scale_px(6, min_abs=5))

        self._hint = QLabel("", self)
        self._hint.setObjectName("OfficeManagerHint")
        self._hint.setWordWrap(True)
        # 说明只占左侧一列：条目摘要再长也不会把按钮挤到另一行，卡片高度因此稳定。
        button_column.addWidget(self._hint)

        self._install_button = QPushButton(f"安装{self._kind}…", self)
        self._install_button.setObjectName("OfficeManagerInstall")
        self._install_button.setToolTip(f"选择一个{self._kind}目录")
        self._install_button.clicked.connect(self._choose_install_source)

        self._delete_button = QPushButton(f"删除{self._kind}", self)
        self._delete_button.setObjectName("OfficeManagerRemove")
        self._delete_button.setToolTip(f"删除选中的{self._kind}；内置项不可删除")
        self._delete_button.setEnabled(False)
        self._delete_button.clicked.connect(self.remove_selected)
        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(scale_px(6, min_abs=5))
        button_row.addWidget(self._install_button, 0)
        button_row.addWidget(self._delete_button, 0)
        button_row.addStretch(1)
        button_column.addLayout(button_row)
        row.addLayout(button_column, 1)
        # 卡片内的删除按钮归列表选中状态管，先记下来方便测试与主题刷新。
        self.install_button = self._install_button
        self.delete_button = self._delete_button
        self.hint_label = self._hint

        self.body_layout.addLayout(row)

    def _build_list(self) -> None:
        self._list = QListWidget(self)
        self._list.setObjectName("OfficeManagerList")
        # 行高由这份字体度量算出（`rows_height()`），跟办公面其余正文同档。
        self._list.setFont(get_ui_font(size=SETTINGS_FONT_SIZE))
        self._list.setWordWrap(False)
        self._list.setTextElideMode(Qt.ElideRight)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self._list.setSelectionMode(QListWidget.SingleSelection)
        self._list.setFixedHeight(self.rows_height())
        self._list.currentItemChanged.connect(self._on_selection_changed)
        self.body_layout.addWidget(self._list)

    def _row_height(self) -> int:
        """单行高度：一行文字加上下内边距，滚动时行高不跳动。"""
        metrics = QFontMetrics(self._list.font())
        return metrics.lineSpacing() + scale_px(14, min_abs=12)

    def rows_height(self) -> int:
        """列表的固定高度：正好露出 `VISIBLE_ROWS` 行，多余 2px 给边框。"""
        border = 2 * scale_px(1, min_abs=1)
        return VISIBLE_ROWS * self._row_height() + border

    # ── 数据 ─────────────────────────────────────────────────────────

    def entries(self) -> list[object]:
        """当前卡片里的条目快照。"""
        return list(self._entries)

    @property
    def visible_rows(self) -> int:
        return VISIBLE_ROWS

    def list_widget(self) -> QListWidget:
        return self._list

    def selected_entry(self) -> object | None:
        item = self._list.currentItem()
        return None if item is None else item.data(Qt.UserRole)

    def refresh(self, *, selected_name: str | None = None) -> None:
        """重新从 loader 取条目并重建列表；只改界面，不弹对话框。"""
        entries: list[object] = []
        failed: str | None = None
        try:
            entries = list(self._loader())
        except Exception as exc:
            logger.warning("[OfficeManager] 读取%s列表失败: %s", self._kind, exc)
            failed = str(exc)
        self._entries = entries

        row_height = self._row_height()
        wanted = str(selected_name or "")
        self._list.clear()
        current: QListWidgetItem | None = None
        for entry in entries:
            item = QListWidgetItem(self._format_entry(entry), self._list)
            item.setSizeHint(QSize(0, row_height))
            item.setData(Qt.UserRole, entry)
            tooltip = self._entry_tooltip(entry)
            if tooltip:
                item.setToolTip(tooltip)
            if wanted and str(getattr(entry, "name", "") or "") == wanted:
                current = item
        if current is None and self._list.count():
            current = self._list.item(0)
        if current is not None:
            self._list.setCurrentItem(current)

        if failed is not None:
            self._set_hint(f"读取{self._kind}列表失败：{failed}", tone="error")
        self._on_selection_changed()

    def _format_entry(self, entry: object) -> str:
        name = str(getattr(entry, "name", "") or "")
        version = str(getattr(entry, "version", "") or "")
        text = f"{name}  {version}".strip() if version else name
        if bool(getattr(entry, "bundled", False)):
            text = f"{text}（内置）"
        return text

    def _entry_tooltip(self, entry: object) -> str:
        parts = [
            str(getattr(entry, "description", "") or "").strip(),
            str(getattr(entry, "path", "") or "").strip(),
        ]
        return "\n".join(part for part in parts if part)

    def _idle_hint(self) -> str:
        """空闲提示：只报数量与滚动方式，条目自身的说明留给悬停气泡。"""
        return (
            f"共 {len(self._entries)} 个{self._kind}；"
            f"列表显示 {VISIBLE_ROWS} 项，多出的在卡片内滚动查看。"
        )

    def _set_hint(self, text: str, *, tone: str = "") -> None:
        self._hint.setText(str(text))
        self._hint.setProperty("tone", tone)
        style = self._hint.style()
        if style is not None:
            style.unpolish(self._hint)
            style.polish(self._hint)

    def _on_selection_changed(self, *_args) -> None:
        entry = self.selected_entry()
        removable = entry is not None and bool(getattr(entry, "removable", False))
        self._delete_button.setEnabled(removable)
        if entry is None:
            self._set_hint(self._idle_hint())
            return
        name = str(getattr(entry, "name", "") or "")
        summary = _short_summary(str(getattr(entry, "description", "") or ""))
        self._set_hint(f"{name}：{summary}" if summary else f"{name}：{self._idle_hint()}")

    # ── 安装 / 删除 ──────────────────────────────────────────────────

    def _choose_install_source(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, self._pick_title)
        if not directory:
            return
        self.install_from(Path(directory))

    def install_from(self, source: Path) -> object | None:
        """安装用户选定的目录；成功返回装好的条目，失败返回 None。"""
        try:
            installed = self._installer(Path(source))
        except Exception as exc:
            logger.warning("[OfficeManager] 安装%s失败: %s", self._kind, exc)
            self._set_hint(f"安装{self._kind}失败：{exc}", tone="error")
            self._warn(f"安装{self._kind}失败", str(exc))
            return None
        self.refresh(selected_name=str(getattr(installed, "name", "") or "") or None)
        self.changed.emit()
        return installed

    def remove_selected(self) -> bool:
        """删除当前选中项；内置项、未选中或用户取消都返回 False。"""
        entry = self.selected_entry()
        if entry is None:
            return False
        if not bool(getattr(entry, "removable", False)):
            self._set_hint(f"内置{self._kind}随程序发布，不能在这里删除。", tone="error")
            return False
        name = str(getattr(entry, "name", "") or "")
        if not self._confirm_remove(name):
            return False
        try:
            self._remover(name)
        except Exception as exc:
            logger.warning("[OfficeManager] 删除%s失败: %s", self._kind, exc)
            self._set_hint(f"删除{self._kind}失败：{exc}", tone="error")
            self._warn(f"删除{self._kind}失败", str(exc))
            return False
        self.refresh()
        self.changed.emit()
        return True

    def _confirm_remove(self, name: str) -> bool:
        dialog = QMessageBox(self)
        dialog.setObjectName("OfficeConfirmDialog")
        dialog.setWindowTitle(f"删除{self._kind}")
        dialog.setIcon(QMessageBox.Warning)
        dialog.setText(f"确定删除{self._kind}“{name}”？")
        dialog.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        confirm = dialog.button(QMessageBox.Yes)
        if confirm is not None:
            confirm.setText("删除")
            confirm.setObjectName("OfficeConfirmDelete")
        cancel = dialog.button(QMessageBox.No)
        if cancel is not None:
            cancel.setText("取消")
            cancel.setObjectName("OfficeConfirmCancel")
        dialog.setDefaultButton(QMessageBox.No)
        return dialog.exec_() == QMessageBox.Yes

    def _warn(self, title: str, text: str) -> None:
        dialog = QMessageBox(self)
        dialog.setObjectName("OfficeConfirmDialog")
        dialog.setWindowTitle(title)
        dialog.setIcon(QMessageBox.Warning)
        dialog.setText(text)
        dialog.setStandardButtons(QMessageBox.Ok)
        dialog.exec_()


__all__ = ["OfficeManagerCard", "VISIBLE_ROWS"]
