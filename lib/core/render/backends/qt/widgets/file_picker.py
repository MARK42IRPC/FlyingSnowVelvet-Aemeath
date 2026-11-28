"""Filesystem selection controls hosted by the shared announcement window."""

from __future__ import annotations

import os
import re
from pathlib import Path
from stat import S_ISREG

from PyQt5.QtCore import QDir, QEvent, QFileInfo, QObject, QStandardPaths, Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileSystemModel,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeView,
    QWidget,
)

from config.scale import scale_px
from lib.core.render.backends.qt.widgets import tier_d_seams
from lib.core.render.backends.qt.widgets.message_box_host import ask_confirmation
from lib.core.render.backends.qt.widgets.spec_host import build_spec_window
from lib.core.render.backends.qt.widgets.text_context_menu import install_text_context_menu
from lib.core.render.visuals.announcement_chrome import announcement_panel_body, announcement_shell_spec
from lib.core.render.visuals.window_spec import ROLE_PRIMARY, ButtonSpec

_OPEN = "open"
_SAVE = "save"
_DIRECTORY = "directory"
_ANY_FILE = "所有文件 (*)"
_last_directory: Path | None = None


def _name_filters(filters: str) -> list[tuple[str, tuple[str, ...]]]:
    """Read the `Label (*.ext *.other);;Label (*)` filter format."""
    result = []
    for entry in filters.split(";;"):
        label = entry.strip()
        if label:
            match = re.search(r"\(([^()]*)\)$", label)
            patterns = match[1].split() if match else label.split()
            # Windows file pickers use *.* to mean all files, including extensionless names.
            result.append((label, tuple("*" if pattern == "*.*" else pattern for pattern in patterns)))
    return result or [(_ANY_FILE, ("*",))]


def _absolute_path(text: str, base: Path) -> Path:
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    path = Path(os.path.abspath(base / os.path.expanduser(text)))
    return _normalize_windows_case(path)


def _normalize_windows_case(path: Path) -> Path:
    """Normalize an existing Windows prefix to its long/canonical spelling.

    GitHub's Windows runner may expose ``USERPROFILE`` through a short 8.3
    alias while ``QFileSystemModel`` returns the long name. Walk up to the
    longest existing ancestor, ask Win32 for that path's long spelling, then
    append any missing suffix components unchanged.
    """
    absolute = Path(os.path.abspath(path))
    if os.name != "nt":
        return absolute

    missing: list[str] = []
    existing = absolute
    while not existing.exists() and not existing.is_symlink():
        if existing.parent == existing:
            return absolute
        missing.append(existing.name)
        existing = existing.parent

    try:
        import ctypes
        from ctypes import wintypes

        get_long = ctypes.windll.kernel32.GetLongPathNameW
        get_long.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
        get_long.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        length = get_long(str(existing), buffer, len(buffer))
        if length and length < len(buffer):
            existing = Path(buffer.value)
    except (AttributeError, OSError, ValueError):
        pass
    return existing.joinpath(*reversed(missing))


def _start_location(start_path: str) -> tuple[Path, str]:
    base = _last_directory if _last_directory is not None and _last_directory.is_dir() else Path.cwd()
    if not start_path:
        return base, ""
    path = _absolute_path(start_path, Path.cwd())
    if path.is_dir():
        return path, ""
    for ancestor in path.parents:
        if ancestor.is_dir():
            return ancestor, path.name
    return base, path.name


def _valid_filename(name: str) -> bool:
    if not name or name in (".", "..") or "\x00" in name or Path(name).name != name:
        return False
    if os.name == "nt":
        return (
            not name.endswith((".", " "))
            and not Path(name).is_reserved()
            and not any(character in '<>:"|?*' or ord(character) < 32 for character in name)
        )
    return True


class _DirectoryModel(QFileSystemModel):
    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802 - Qt override
        if orientation == Qt.Horizontal and role == Qt.DisplayRole and 0 <= section < 4:
            return ("名称", "大小", "类型", "修改日期")[section]
        return super().headerData(section, orientation, role)


class _FilePicker(QObject):
    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        start_path: str,
        filters: str,
        *,
        mode: str,
        default_suffix: str = "",
        resolve_symlinks: bool = True,
    ) -> None:
        super().__init__()
        self._mode = mode
        self._default_suffix = default_suffix.lstrip(".")
        self._resolve_symlinks = resolve_symlinks
        self.result = ""
        label = {_OPEN: "打开", _SAVE: "保存", _DIRECTORY: "选择目录"}[mode]
        spec = announcement_shell_spec(
            object_name="FilePicker",
            title=title,
            body=announcement_panel_body(),
            actions=(ButtonSpec("取消", "cancel", id="cancel"),
                     ButtonSpec(label, "select", id="select", role=ROLE_PRIMARY)),
            width=scale_px(680, min_abs=560),
            height=scale_px(480, min_abs=430),
            close_action="cancel",
            modal=True,
            default_button_id="select",
            non_closing_actions=("select",),
            resizable=True,
        )
        self._window = build_spec_window(
            spec,
            parent=parent,
            font_factory=tier_d_seams.ui_font,
            presentation_host=tier_d_seams.presentation_host(),
            on_semantic=self._on_action,
        )
        self._window.widget.setSizeGripEnabled(True)
        body = self._window.find("body")
        layout = body.layout()

        toolbar = QHBoxLayout()
        toolbar.setSpacing(layout.spacing())
        self._locations = QComboBox(body)
        self._locations.setAccessibleName("常用位置")
        self._locations.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self._locations.addItem("常用位置")
        for name, location in (
            ("用户目录", QStandardPaths.HomeLocation),
            ("桌面", QStandardPaths.DesktopLocation),
            ("文档", QStandardPaths.DocumentsLocation),
            ("下载", QStandardPaths.DownloadLocation),
        ):
            location_path = QStandardPaths.writableLocation(location)
            if location_path:
                self._locations.addItem(name, location_path)
        for drive in QDir.drives():
            path = drive.absoluteFilePath()
            self._locations.addItem(QDir.toNativeSeparators(path), path)
        self._locations.activated[int].connect(self._go_to_shortcut)
        toolbar.addWidget(self._locations)

        self._address = self._line_edit(body, "当前目录", "输入目录后按 Enter")
        self._address.installEventFilter(self)
        self._address.textEdited.connect(self._clear_error)
        toolbar.addWidget(self._address, 1)
        self._up = self._button(body, "上一级", self._go_up)
        toolbar.addWidget(self._up)
        layout.addLayout(toolbar)

        self._model = _DirectoryModel(body)
        self._model.setReadOnly(True)
        # Open mode lists .lnk files as files even when their destinations are folders.
        self._model.setResolveSymlinks(resolve_symlinks and mode == _DIRECTORY)
        entries = QDir.AllDirs | QDir.NoDotAndDotDot | QDir.Drives
        if mode != _DIRECTORY:
            entries |= QDir.Files
        self._model.setFilter(entries)
        self._model.setNameFilterDisables(False)

        self._view = QTreeView(body)
        self._view.setAccessibleName("文件和文件夹" if mode != _DIRECTORY else "文件夹列表")
        self._view.setModel(self._model)
        self._view.setRootIsDecorated(False)
        self._view.setItemsExpandable(False)
        self._view.setUniformRowHeights(True)
        self._view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._view.setDragDropMode(QAbstractItemView.NoDragDrop)
        self._view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._view.setSelectionMode(QAbstractItemView.SingleSelection)
        self._view.setContextMenuPolicy(Qt.NoContextMenu)
        self._view.setSortingEnabled(True)
        self._view.sortByColumn(0, Qt.AscendingOrder)
        header = self._view.header()
        header.setStretchLastSection(False)
        header.setResizeContentsPrecision(80)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self._view.setColumnHidden(2, True)
        self._view.setColumnHidden(1, mode == _DIRECTORY)
        self._view.selectionModel().currentChanged.connect(self._on_selected)
        self._view.doubleClicked.connect(self._activate_index)
        self._view.installEventFilter(self)
        layout.addWidget(self._view, 1)

        self._filename = None
        self._filters = None
        row = QHBoxLayout()
        row.setSpacing(layout.spacing())
        if mode != _DIRECTORY:
            self._filename = self._line_edit(body, "文件名", "输入文件名或完整路径")
            self._filename.textEdited.connect(self._filename_edited)
            self._add_labeled(row, "文件名", self._filename, stretch=1)
            self._filters = QComboBox(body)
            self._filters.setAccessibleName("文件类型")
            self._filters.setSizeAdjustPolicy(QComboBox.AdjustToContents)
            for caption, patterns in _name_filters(filters):
                self._filters.addItem(caption, patterns)
            self._filters.currentIndexChanged.connect(self._update_filter)
            row.addWidget(self._filters)
        else:
            row.addStretch(1)
        row.addWidget(self._button(body, "新建文件夹", self._show_new_folder))
        layout.addLayout(row)

        self._new_folder = QWidget(body)
        folder_row = QHBoxLayout(self._new_folder)
        folder_row.setContentsMargins(0, 0, 0, 0)
        folder_row.setSpacing(layout.spacing())
        self._folder_name = self._line_edit(self._new_folder, "文件夹名称", "在当前目录中创建")
        self._folder_name.installEventFilter(self)
        self._folder_name.textEdited.connect(self._clear_error)
        self._add_labeled(folder_row, "文件夹名称", self._folder_name, stretch=1)
        folder_row.addWidget(self._button(self._new_folder, "创建", self._create_folder))
        folder_row.addWidget(self._button(self._new_folder, "收起", self._new_folder.hide))
        layout.addWidget(self._new_folder)
        self._new_folder.hide()

        self._error = QLabel(body)
        self._error.setProperty("tone", "error")
        self._error.setTextFormat(Qt.PlainText)
        self._error.setWordWrap(True)
        self._error.hide()
        layout.addWidget(self._error)

        directory, filename = _start_location(start_path)
        self._go_to(directory)
        if self._filters is not None:
            self._update_filter()
        if self._filename is not None:
            self._filename.setText(filename)
            self._filename.setFocus()
            self._filename.selectAll()
        else:
            self._view.setFocus()

    @staticmethod
    def _line_edit(parent, name, placeholder) -> QLineEdit:
        editor = QLineEdit(parent)
        editor.setAccessibleName(name)
        editor.setPlaceholderText(placeholder)
        install_text_context_menu(editor)
        return editor

    @staticmethod
    def _button(parent, text, on_click) -> QPushButton:
        button = QPushButton(text, parent)
        button.setAutoDefault(False)
        button.clicked.connect(on_click)
        return button

    @staticmethod
    def _add_labeled(row, text, editor, *, stretch: int = 0) -> None:
        label = QLabel(text, editor.parentWidget())
        label.setBuddy(editor)
        row.addWidget(label)
        row.addWidget(editor, stretch)

    def exec(self) -> str:
        try:
            self._window.exec_modal()
            return self.result
        finally:
            self._window.cleanup()

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        # Prevent Enter in a navigation control from also clicking the modal default button.
        if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if watched is self._address:
                self._go_to_address()
            elif watched is self._view:
                self._activate_index(self._view.currentIndex())
            elif watched is self._folder_name:
                self._create_folder()
            return True
        return super().eventFilter(watched, event)

    def _display_error(self, text: str) -> None:
        self._error.setText(text)
        self._error.show()

    def _clear_error(self) -> None:
        self._error.hide()

    def _resolved_path(self, path: Path) -> Path:
        if not self._resolve_symlinks:
            # Keep the selected symlink itself, but normalize Windows 8.3 aliases
            # so paths from QFileSystemModel compare with caller-supplied long paths.
            return _normalize_windows_case(path)
        # QFileInfo also understands Windows shortcuts, which pathlib does not resolve.
        target = QFileInfo(str(path)).symLinkTarget()
        return _normalize_windows_case(Path(target or path).resolve())

    def _go_to(self, directory: Path) -> None:
        global _last_directory
        try:
            directory = _normalize_windows_case(self._resolved_path(directory))
            if not directory.is_dir():
                self._display_error("找不到此目录，请检查路径。")
                return
        except (OSError, RuntimeError, ValueError):
            self._display_error("无法访问此目录，请检查路径和权限。")
            return
        self._directory = _last_directory = directory
        index = self._model.setRootPath(directory.as_posix())
        self._view.setRootIndex(index)
        self._view.selectionModel().clear()
        self._address.setText(str(directory))
        self._address.setToolTip(str(directory))
        self._up.setEnabled(directory.parent != directory)
        self._new_folder.hide()
        self._clear_error()
        if self._mode == _OPEN:
            self._filename.clear()

    def _go_up(self) -> None:
        self._go_to(self._directory.parent)

    def _go_to_shortcut(self, index: int) -> None:
        path = self._locations.itemData(index)
        if path:
            self._go_to(Path(path))
        self._locations.setCurrentIndex(0)

    def _go_to_address(self) -> None:
        text = self._address.text()
        if text:
            self._go_to(_absolute_path(text, self._directory))
        else:
            self._display_error("请输入目录路径。")

    def _update_filter(self) -> None:
        self._model.setNameFilters(list(self._filters.currentData()))
        self._view.selectionModel().clear()

    def _filename_edited(self) -> None:
        self._view.selectionModel().clear()
        self._clear_error()

    def _on_selected(self, index, _previous) -> None:
        if index.isValid():
            self._clear_error()
            if self._filename is not None:
                if not self._model.isDir(index):
                    self._filename.setText(self._model.fileName(index))
                elif self._mode == _OPEN:
                    self._filename.clear()

    def _index_path(self, index) -> Path:
        return _normalize_windows_case(Path(self._model.filePath(index)))

    def _activate_index(self, index) -> None:
        if not index.isValid():
            return
        path = self._index_path(index)
        if self._model.isDir(index):
            self._go_to(path)
        else:
            self._submit_file(path)

    def _show_new_folder(self) -> None:
        self._folder_name.clear()
        self._new_folder.show()
        self._folder_name.setFocus()

    def _create_folder(self) -> None:
        name = self._folder_name.text()
        if not _valid_filename(name):
            self._display_error("请输入一个有效的文件夹名称。")
            return
        folder = self._directory / name
        try:
            folder.mkdir()
        except OSError:
            self._display_error("无法新建文件夹，请检查是否重名或具有写入权限。")
            return
        self._go_to(folder)

    def _complete(self, path: Path) -> None:
        self.result = str(_normalize_windows_case(path))
        self._window.resolve_with("accepted")

    def _on_action(self, semantic: str) -> None:
        if semantic != "select":
            return
        address = self._address.text()
        if address != str(self._directory):
            if self._mode == _DIRECTORY and address:
                self._submit_directory(_absolute_path(address, self._directory))
            else:
                self._go_to_address()
            return
        index = self._view.currentIndex()
        selected = index.isValid() and self._view.selectionModel().isSelected(index)
        if self._mode == _DIRECTORY:
            self._submit_directory(self._index_path(index) if selected else self._directory)
            return
        if selected and self._model.isDir(index):
            self._go_to(self._index_path(index))
            return
        name = self._filename.text()
        if name:
            self._submit_file(_absolute_path(name, self._directory))
        else:
            self._display_error("请选择文件或输入文件名。")

    def _submit_directory(self, path: Path) -> None:
        try:
            path = self._resolved_path(path)
            if path.is_dir():
                self._complete(path)
                return
        except (OSError, RuntimeError, ValueError):
            pass
        self._display_error("请选择一个可访问的目录。")

    def _submit_file(self, path: Path) -> None:
        try:
            # A launcher can intentionally select the Windows shortcut itself.
            if self._mode == _OPEN and path.suffix.lower() != ".lnk":
                path = self._resolved_path(path)
            if path.is_dir():
                self._go_to(path)
            elif self._mode == _SAVE:
                self._save_to(path)
            elif path.is_file():
                self._complete(path)
            else:
                self._display_error("找不到所选文件，请检查文件名。")
        except (OSError, RuntimeError, ValueError):
            self._display_error("无法访问所选路径，请检查路径和权限。")

    def _save_to(self, path: Path) -> None:
        if self._default_suffix and "." not in path.name:
            path = path.with_name(f"{path.name}.{self._default_suffix}")
        if not _valid_filename(path.name):
            self._display_error("请输入一个有效的文件名。")
            return
        if not path.parent.is_dir():
            self._display_error("保存目录不存在，请选择一个已有目录。")
            return
        try:
            mode = path.stat().st_mode
        except FileNotFoundError:
            if path.is_symlink():
                self._display_error("所选文件是失效的符号链接，请更换文件名。")
                return
        else:
            if not S_ISREG(mode):
                self._display_error("该名称已被目录或其他条目占用，请更换文件名。")
                return
            if not ask_confirmation(
                self._window.widget,
                title="确认覆盖文件",
                text=f"该文件已存在。要覆盖它吗？\n{path}",
                confirm_text="覆盖",
                destructive=True,
            ):
                return
        self._complete(path)


def pick_open_file(parent, title: str, start_path: str = "", filters: str = "") -> str:
    """Return one existing file or an empty string when canceled."""
    return _FilePicker(parent, title, start_path, filters, mode=_OPEN).exec()


def pick_save_file(
    parent,
    title: str,
    start_path: str = "",
    filters: str = "",
    *,
    default_suffix: str = "",
) -> str:
    """Return a save path after explicit approval if it already exists."""
    return _FilePicker(parent, title, start_path, filters, mode=_SAVE, default_suffix=default_suffix).exec()


def pick_directory(parent, title: str, start_path: str = "", *, resolve_symlinks: bool = True) -> str:
    """Return one existing directory or an empty string when canceled."""
    return _FilePicker(parent, title, start_path, "", mode=_DIRECTORY, resolve_symlinks=resolve_symlinks).exec()


__all__ = ["pick_directory", "pick_open_file", "pick_save_file"]
