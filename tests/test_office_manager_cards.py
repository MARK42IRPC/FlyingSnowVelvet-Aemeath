"""工作台「技能管理 / 插件管理」卡片的行为测试。"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TEST_HOME = tempfile.mkdtemp(prefix="office-manager-cards-test-")
os.environ["AEMEATH_DESK_PET_HOME"] = _TEST_HOME
atexit.register(shutil.rmtree, _TEST_HOME, ignore_errors=True)

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFontMetrics
from PyQt5.QtWidgets import QApplication, QMessageBox

from config.scale import scale_px
from lib.script.office import plugins as office_plugins
from lib.script.office import skills as office_skills
from lib.script.ui.office_manager_card import VISIBLE_ROWS, OfficeManagerCard
from lib.script.ui.workbench_settings_layout import SETTINGS_FONT_SIZE


class _Entry:
    """卡片只读 name/version/description/path/bundled/removable，用最小对象代替真实技能。"""

    def __init__(self, name: str, *, version: str = "", description: str = "", bundled: bool = False) -> None:
        self.name = name
        self.version = version
        self.description = description
        self.path = Path(f"C:/tmp/{name}")
        self.bundled = bundled

    @property
    def removable(self) -> bool:
        return not self.bundled


def _write_skill(root: Path, name: str, description: str = "") -> Path:
    directory = Path(root) / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n正文\n",
        encoding="utf-8",
    )
    return directory


def _write_plugin(root: Path, name: str, version: str = "1.0.0") -> Path:
    directory = Path(root) / name.split("/")[-1]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "package.json").write_text(
        json.dumps({"name": name, "version": version, "description": "测试插件"}),
        encoding="utf-8",
    )
    return directory


def _reset_office_state() -> None:
    state = Path(_TEST_HOME) / "user" / "state" / "office"
    if state.exists():
        shutil.rmtree(state, ignore_errors=True)


class OfficeManagerCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        _reset_office_state()

    def _card(self, entries, *, installer=None, remover=None):
        card = OfficeManagerCard(
            "技能管理",
            "",
            kind="技能",
            loader=lambda: list(entries),
            installer=installer or (lambda source: None),
            remover=remover or (lambda name: None),
            pick_title="选择技能目录",
        )
        self.addCleanup(card.deleteLater)
        return card

    def test_card_keeps_five_visible_rows_and_scrolls_the_rest(self):
        entries = [_Entry(f"skill-{index}") for index in range(8)]
        card = self._card(entries)
        widget = card.list_widget()

        self.assertEqual(widget.count(), 8)
        self.assertEqual(VISIBLE_ROWS, 5)
        self.assertEqual(card.visible_rows, VISIBLE_ROWS)
        self.assertEqual(widget.verticalScrollBarPolicy(), Qt.ScrollBarAsNeeded)
        self.assertEqual(
            card.rows_height(),
            VISIBLE_ROWS * card._row_height() + 2 * scale_px(1, min_abs=1),
        )
        self.assertEqual(widget.height(), card.rows_height())
        self.assertEqual(
            {widget.item(index).sizeHint().height() for index in range(8)},
            {card._row_height()},
        )

    def test_card_height_does_not_grow_with_entry_count(self):
        short = self._card([_Entry("a"), _Entry("b")])
        tall = self._card([_Entry(f"skill-{index}") for index in range(12)])

        self.assertEqual(short.list_widget().height(), tall.list_widget().height())

    def test_card_rows_use_workbench_settings_font_size(self):
        card = self._card([_Entry("skill")])
        widget = card.list_widget()

        # 卡片列表字号跟工作台设置页同档，行高由这份字号算出来。
        self.assertEqual(widget.font().pixelSize(), SETTINGS_FONT_SIZE)
        self.assertEqual(
            card._row_height(),
            QFontMetrics(widget.font()).lineSpacing() + scale_px(14, min_abs=12),
        )

    def test_delete_button_tracks_selection_and_blocks_bundled_entries(self):
        bundled = _Entry("fsv-bundled", bundled=True)
        user = _Entry("fsv-user")
        removed: list[str] = []
        card = self._card([bundled, user], remover=removed.append)

        self.assertEqual(card.selected_entry().name, "fsv-bundled")
        self.assertFalse(card.delete_button.isEnabled())
        self.assertFalse(card.remove_selected())
        self.assertEqual(removed, [])

        card.list_widget().setCurrentRow(1)
        self.assertTrue(card.delete_button.isEnabled())
        with patch(
            "lib.script.ui.office_manager_card.QMessageBox.exec_",
            return_value=QMessageBox.Yes,
        ):
            self.assertTrue(card.remove_selected())
        self.assertEqual(removed, ["fsv-user"])

    def test_remove_is_cancelled_when_the_user_declines(self):
        removed: list[str] = []
        card = self._card([_Entry("fsv-user")], remover=removed.append)

        with patch(
            "lib.script.ui.office_manager_card.QMessageBox.exec_",
            return_value=QMessageBox.No,
        ):
            self.assertFalse(card.remove_selected())
        self.assertEqual(removed, [])

    def test_install_from_selects_the_new_entry(self):
        created = _Entry("fsv-new")
        entries: list[_Entry] = [_Entry("fsv-old")]

        def installer(source):
            entries.append(created)
            return created

        card = self._card(entries, installer=installer)
        installed = card.install_from(Path("C:/tmp/fsv-new"))

        self.assertIs(installed, created)
        self.assertEqual([entry.name for entry in card.entries()], ["fsv-old", "fsv-new"])
        self.assertEqual(card.selected_entry().name, "fsv-new")

    def test_install_failure_reports_the_error_hint(self):
        def installer(source):
            raise RuntimeError("缺少 SKILL.md")

        card = self._card([_Entry("fsv-old")], installer=installer)
        with patch(
            "lib.script.ui.office_manager_card.QMessageBox.exec_",
            return_value=QMessageBox.Ok,
        ):
            self.assertIsNone(card.install_from(Path("C:/tmp/bad")))

        self.assertEqual(card.hint_label.property("tone"), "error")
        self.assertIn("缺少 SKILL.md", card.hint_label.text())

    def test_long_skill_description_is_summarized_to_one_line(self):
        long_text = "Use for user-requested web research, " * 6
        card = self._card([_Entry("fsv-browser-research", description=long_text)])

        hint = card.hint_label.text()
        self.assertEqual(hint, f"fsv-browser-research：{long_text[:48].rstrip()}…")
        self.assertEqual(card.hint_label.wordWrap(), True)
        # 完整说明仍留在条目的悬停气泡里。
        self.assertEqual(card.list_widget().item(0).toolTip().splitlines()[0], long_text.strip())

    def test_idle_hint_reports_the_scrollable_row_budget(self):
        card = self._card([_Entry("a"), _Entry("b")])
        card.list_widget().setCurrentItem(None)

        self.assertEqual(
            card.hint_label.text(),
            "共 2 个技能；列表显示 5 项，多出的在卡片内滚动查看。",
        )


class OfficeSkillCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        _reset_office_state()

    def test_skill_card_lists_bundled_skills_and_manages_user_skills(self):
        bundled_names = {skill.name for skill in office_skills.list_skills()}
        self.assertIn("fsv-office-workflow", bundled_names)

        card = OfficeManagerCard(
            "技能管理",
            "",
            kind="技能",
            loader=office_skills.list_skills,
            installer=office_skills.install_skill,
            remover=office_skills.remove_skill,
            pick_title="选择技能目录",
        )
        self.addCleanup(card.deleteLater)

        self.assertEqual({entry.name for entry in card.entries()}, bundled_names)
        self.assertTrue(all(not entry.removable for entry in card.entries()))

        with tempfile.TemporaryDirectory() as tmp:
            source = _write_skill(Path(tmp), "fsv-user-skill", "用户技能")
            installed = card.install_from(source)
            self.assertIsNotNone(installed)
            self.assertEqual(card.selected_entry().name, "fsv-user-skill")
            self.assertTrue(card.selected_entry().removable)

            with patch(
                "lib.script.ui.office_manager_card.QMessageBox.exec_",
                return_value=QMessageBox.Yes,
            ):
                self.assertTrue(card.remove_selected())

        self.assertNotIn("fsv-user-skill", {entry.name for entry in card.entries()})


class OfficePluginCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        _reset_office_state()

    def _card(self):
        card = OfficeManagerCard(
            "插件管理",
            "",
            kind="插件",
            loader=office_plugins.list_plugins,
            installer=office_plugins.install_plugin,
            remover=office_plugins.remove_plugin,
            pick_title="选择插件目录",
        )
        self.addCleanup(card.deleteLater)
        return card

    def test_plugin_card_marks_bundled_bundles_and_manages_user_plugins(self):
        card = self._card()
        names = {entry.name for entry in card.entries()}
        self.assertIn("@deepseek-ai/dsh-base", names)
        base = next(entry for entry in card.entries() if entry.name == "@deepseek-ai/dsh-base")
        self.assertTrue(base.bundled)
        self.assertFalse(base.removable)

        with tempfile.TemporaryDirectory() as tmp:
            source = _write_plugin(Path(tmp), "fsv-test-plugin")
            installed = card.install_from(source)
            self.assertIsNotNone(installed)
            self.assertEqual(card.selected_entry().name, "fsv-test-plugin")
            self.assertIn("fsv-test-plugin", office_plugins.registered_bundles())

            with patch(
                "lib.script.ui.office_manager_card.QMessageBox.exec_",
                return_value=QMessageBox.Yes,
            ):
                self.assertTrue(card.remove_selected())

        self.assertNotIn("fsv-test-plugin", office_plugins.registered_bundles())

    def test_registered_bundles_are_merged_into_the_provisioned_profile(self):
        office_plugins.registry_path().parent.mkdir(parents=True, exist_ok=True)
        office_plugins.registry_path().write_text(
            json.dumps({"bundles": ["fsv-extra-plugin"]}),
            encoding="utf-8",
        )
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / "package.json"
            package.write_text(
                json.dumps({"name": "p", "dsh": {"profile": {"bundles": ["@deepseek-ai/dsh-base"]}}}),
                encoding="utf-8",
            )

            office_plugins.apply_registered_bundles(package)
            office_plugins.apply_registered_bundles(package)

            payload = json.loads(package.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["dsh"]["profile"]["bundles"],
            ["@deepseek-ai/dsh-base", "fsv-extra-plugin"],
        )


if __name__ == "__main__":
    unittest.main()
