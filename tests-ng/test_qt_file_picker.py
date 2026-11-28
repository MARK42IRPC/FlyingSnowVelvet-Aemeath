"""Shared picker behavior and the product seams that select its modes."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests_ng.support.qt import app

from PyQt5.QtCore import QCoreApplication, QEvent, QTimer, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QAbstractItemView, QLineEdit

from lib.core.render.backends.qt.widgets import (
    ai_settings_editors, file_picker, forum_composer, game_manager_window,
    office_manager_card, office_page,
)


class QtFilePickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.folder = self.root / "目录"
        self.folder.mkdir()
        self.zip = self.root / "中文包.zip"
        self.zip.write_bytes(b"original")
        self.other = self.root / "README"
        self.other.touch()
        last_directory = patch.object(file_picker, "_last_directory", None)
        last_directory.start()
        self.addCleanup(last_directory.stop)

    def _picker(self, mode, start_path=None, **kwargs):
        picker = file_picker._FilePicker(
            None, "选取本地路径", str(self.root if start_path is None else start_path),
            "压缩包 (*.zip);;所有文件 (*.*)", mode=mode, **kwargs,
        )
        self.addCleanup(self._cleanup_picker, picker)
        return picker

    @staticmethod
    def _cleanup_picker(picker):
        picker._window.cleanup()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def _visible_names(self, picker, expected):
        model = picker._model
        root = picker._view.rootIndex()
        names = set()
        for _ in range(50):
            names = {model.fileName(model.index(row, 0, root)) for row in range(model.rowCount(root))}
            if names == expected:
                break
            QTest.qWait(10)
        self.assertEqual(names, expected)

    def test_windows_short_8dot3_and_long_user_paths_compare_by_canonical_component_names(self):
        if os.name != "nt":
            self.skipTest("8.3 alias behavior is Windows-specific")
        long_user = Path(os.environ.get("USERPROFILE", Path.home()))
        import ctypes
        from ctypes import wintypes

        get_short = ctypes.windll.kernel32.GetShortPathNameW
        get_short.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
        get_short.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        length = get_short(str(long_user), buffer, len(buffer))
        if not length or length >= len(buffer):
            self.skipTest("The runner has no short 8.3 user-profile alias")
        short_user = Path(buffer.value)
        expected = long_user / "AppData" / "Local" / "Temp" / "缺失目录"
        short = short_user / "AppData" / "Local" / "Temp" / "缺失目录"
        normalized = file_picker._normalize_windows_case(short)
        self.assertEqual(normalized, expected)
        self.assertEqual(file_picker._absolute_path(str(short), Path.cwd()), normalized)

    def test_windows_alias_is_canonicalized_for_last_directory_and_symlink_selection(self):
        if os.name != "nt":
            self.skipTest("Windows path aliases are platform-specific")
        short_temp = file_picker._normalize_windows_case(Path(os.environ["TEMP"]))
        expected_temp = Path(os.environ["TEMP"]).resolve()
        self.assertEqual(short_temp, expected_temp)
        self.assertEqual(file_picker._normalize_windows_case(self.folder), self.folder)


    def test_open_uses_shared_chrome_and_filter_then_opens_existing_file(self):
        picker = self._picker(file_picker._OPEN)
        window = picker._window
        self.assertTrue(window.spec.shared_popup)
        self.assertEqual(window.find("body").objectName(), "AnnouncementBody")
        self.assertEqual(window.find("header").text(), "选取本地路径")
        self.assertTrue(picker._model.isReadOnly())
        self.assertEqual(picker._view.editTriggers(), QAbstractItemView.NoEditTriggers)
        self.assertEqual(picker._view.dragDropMode(), QAbstractItemView.NoDragDrop)
        self.assertEqual(picker._view.contextMenuPolicy(), Qt.NoContextMenu)
        self._visible_names(picker, {"目录", "中文包.zip"})
        picker._filters.setCurrentIndex(1)
        self._visible_names(picker, {"目录", "中文包.zip", "README"})
        index = picker._model.index(self.other.as_posix())
        QTimer.singleShot(0, lambda: picker._activate_index(index))
        self.assertEqual(Path(picker.exec()), self.other)

    def test_open_shortcut_keeps_the_filename_for_the_launcher(self):
        shortcut = self.root / "Launcher.lnk"
        shortcut.touch()
        picker = self._picker(file_picker._OPEN)
        with patch.object(picker, "_resolved_path", wraps=picker._resolved_path) as resolve:
            picker._filename.setText(str(shortcut))
            QTimer.singleShot(0, picker._window.find("select").click)
            self.assertEqual(Path(picker.exec()), shortcut)
        resolve.assert_not_called()

    def test_directory_mode_only_lists_folders_and_chooses_current_or_selected(self):
        current = self._picker(file_picker._DIRECTORY)
        self._visible_names(current, {"目录"})
        QTimer.singleShot(0, current._window.find("select").click)
        self.assertEqual(Path(current.exec()), self.root)

        selected = self._picker(file_picker._DIRECTORY)
        self._visible_names(selected, {"目录"})
        selected._view.setCurrentIndex(selected._model.index(self.folder.as_posix()))
        QTimer.singleShot(0, selected._window.find("select").click)
        self.assertEqual(Path(selected.exec()), self.folder)

    def test_directory_resolution_can_keep_symlink_path(self):
        link = self.root / "别名"
        try:
            link.symlink_to(self.folder, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                raise
            from _winapi import CreateJunction
            CreateJunction(str(self.folder), str(link))
        for resolve_symlinks, expected in ((False, link), (True, self.folder)):
            with self.subTest(resolve_symlinks=resolve_symlinks):
                picker = self._picker(file_picker._DIRECTORY, resolve_symlinks=resolve_symlinks)
                picker._address.setText(str(link))
                QTimer.singleShot(0, picker._window.find("select").click)
                self.assertEqual(Path(picker.exec()), expected)

    def test_enter_in_location_navigates_without_accepting(self):
        picker = self._picker(file_picker._DIRECTORY)
        picker._address.setText(str(self.folder))
        observed = []

        def navigate():
            QTest.keyClick(picker._address, Qt.Key_Return)
            observed.append((picker._directory, picker.result, picker._window.widget.isVisible()))
            picker._window.find("cancel").click()

        QTimer.singleShot(0, navigate)
        self.assertEqual(picker.exec(), "")
        self.assertEqual(observed, [(self.folder, "", True)])

    def test_missing_parent_preserves_save_filename_and_empty_start_remembers_location(self):
        picker = self._picker(file_picker._SAVE, self.folder / "not-created" / "导出.zip")
        self.assertEqual(picker._directory, self.folder)
        self.assertEqual(picker._filename.text(), "导出.zip")
        following = self._picker(file_picker._OPEN, "")
        self.assertEqual(following._directory, self.folder)
        self.assertEqual(following._filename.text(), "")

    def test_save_seeds_name_applies_default_suffix_and_never_writes(self):
        for typed, expected in (("另存为", "另存为.zip"), ("output.custom", "output.custom")):
            with self.subTest(typed=typed):
                picker = self._picker(file_picker._SAVE, self.folder / "默认.zip", default_suffix="zip")
                self.assertEqual(picker._address.text(), str(self.folder))
                self.assertEqual(picker._filename.selectedText(), "默认.zip")
                picker._filename.setText(typed)
                QTimer.singleShot(0, picker._window.find("select").click)
                result = picker.exec()
                self.assertEqual(Path(result), self.folder / expected)
                self.assertFalse(Path(result).exists())

    def test_overwrite_requires_approval_and_refusal_leaves_picker_open(self):
        picker = self._picker(file_picker._SAVE, self.zip, default_suffix="zip")
        picker._filename.setText(self.zip.stem)
        select = picker._window.find("select")
        observed = []

        def attempt_twice():
            select.click()
            observed.append((picker.result, picker._window.widget.isVisible()))
            select.click()

        with patch.object(file_picker, "ask_confirmation", side_effect=[False, True]) as confirm:
            QTimer.singleShot(0, attempt_twice)
            self.assertEqual(Path(picker.exec()), self.zip)
        self.assertEqual(observed, [("", True)])
        self.assertEqual(confirm.call_count, 2)
        self.assertIn(str(self.zip), confirm.call_args.kwargs["text"])
        self.assertTrue(confirm.call_args.kwargs["destructive"])
        self.assertEqual(self.zip.read_bytes(), b"original")

    def test_invalid_paths_show_error_without_dismissing_and_cancel_discards(self):
        cases = ((file_picker._OPEN, "missing.zip"), (file_picker._SAVE, "missing/file.zip"))
        for mode, invalid in cases:
            with self.subTest(mode=mode):
                picker = self._picker(mode, default_suffix="zip")
                picker._filename.setText(invalid)
                observed = []

                def invalid_then_close():
                    picker._window.find("select").click()
                    observed.append((picker._error.isVisible(), picker._error.textFormat()))
                    QTest.keyClick(picker._filename, Qt.Key_Escape)

                QTimer.singleShot(0, invalid_then_close)
                self.assertEqual(picker.exec(), "")
                self.assertEqual(observed, [(True, Qt.PlainText)])

    def test_create_folder_does_not_accept_or_allow_nested_paths(self):
        picker = self._picker(file_picker._DIRECTORY)
        picker._show_new_folder()
        picker._folder_name.setText("../escape")
        picker._create_folder()
        self.assertEqual(picker._directory, self.root)
        self.assertIn("有效", picker._error.text())
        picker._folder_name.setText("新文件夹")
        picker._create_folder()
        self.assertEqual(picker._directory, self.root / "新文件夹")
        self.assertTrue(picker._directory.is_dir())
        self.assertEqual(picker.result, "")

    def test_game_import_and_export_pass_the_same_start_paths_and_filters(self):
        holder = SimpleNamespace(
            _selected_game=lambda: SimpleNamespace(
                game_id="dango", manifest=SimpleNamespace(game_id="dango", version="1.2.3")
            ),
            _service=Mock(),
        )
        with patch.object(game_manager_window, "pick_open_file", return_value="") as open_file:
            game_manager_window.GameManagerWindow._install_zip(holder)
        open_file.assert_called_once_with(holder, "选择游戏 ZIP", str(Path.home()), "Game Packages (*.zip)")
        with patch.object(game_manager_window, "pick_save_file", return_value="") as save_file:
            game_manager_window.GameManagerWindow._export_selected_game(holder)
        save_file.assert_called_once_with(
            holder, "导出游戏包", str(Path.home() / "Desktop" / "dango-1.2.3.zip"),
            "Game Packages (*.zip)", default_suffix="zip",
        )
        self.assertEqual(holder._service.mock_calls, [])

    def test_settings_office_and_forum_pass_expected_picker_modes_and_arguments(self):
        editor = QLineEdit(str(self.zip))
        self.addCleanup(editor.deleteLater)
        holder = SimpleNamespace(_editor_project_root=lambda: self.root)
        with patch.object(ai_settings_editors, "pick_directory", return_value="") as choose:
            ai_settings_editors.ConfigEditorMixin._browse_local_music_dir(holder, editor)
        choose.assert_called_once_with(holder, "选择本地音乐文件夹", str(self.root), resolve_symlinks=False)
        with patch.object(ai_settings_editors, "pick_open_file", return_value="") as choose:
            ai_settings_editors.ConfigEditorMixin._browse_launch_wuwa_file(holder, editor)
        self.assertEqual(choose.call_args.args[2], str(self.root))
        self.assertIn("启动文件 (*.exe *.bat *.lnk)", choose.call_args.args[3])

        office = SimpleNamespace(_workspace_edit=QLineEdit(str(self.folder)))
        self.addCleanup(office._workspace_edit.deleteLater)
        with patch.object(office_page, "pick_directory", return_value="") as choose:
            office_page.OfficeWorkbenchPage._choose_workspace(office)
        choose.assert_called_once_with(office, "选择工作目录", str(self.folder))

        manager = SimpleNamespace(_pick_title="选择插件目录", install_from=Mock())
        with patch.object(office_manager_card, "pick_directory", return_value=str(self.folder)) as choose:
            office_manager_card.OfficeManagerCard._choose_install_source(manager)
        choose.assert_called_once_with(manager, "选择插件目录")
        manager.install_from.assert_called_once_with(self.folder)

        composer = SimpleNamespace(_compose_images=[], _service=Mock())
        with patch.object(forum_composer, "pick_open_file", return_value="") as choose:
            forum_composer.ForumComposerMixin._on_add_image(composer)
        choose.assert_called_once_with(composer, "选一张图片", "", "图片 (*.png *.jpg *.jpeg *.gif *.webp);;所有文件 (*)")
        composer._service.upload_image.assert_not_called()


if __name__ == "__main__":
    unittest.main()
