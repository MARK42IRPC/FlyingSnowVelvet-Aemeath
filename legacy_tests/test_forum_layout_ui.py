"""发帖页的段落排版：字号下拉、三个对齐按钮，以及详情页把它们渲染出来。"""

from __future__ import annotations

import unittest

from PyQt5.QtWidgets import QLabel

from lib.script.ui.forum_board import FORUM_SIZE_STEPS
from lib.script.ui.forum_text import MarkupText
from tests.test_forum_board_ui import BoardPageTestCase, post


class ComposerLayoutTests(BoardPageTestCase):
    """字号与对齐是段落属性：点一下改整段，再点一下回到默认。"""

    def open_composer(self) -> None:
        self.login()
        self.page.open_composer()
        self.app.processEvents()

    def set_caret(self, position: int) -> None:
        cursor = self.page._thread_body.textCursor()
        cursor.setPosition(position)
        self.page._thread_body.setTextCursor(cursor)
        self.app.processEvents()

    def test_size_dropdown_offers_the_default_and_the_steps(self) -> None:
        self.open_composer()
        self.assertEqual(self.page._size_combo.itemData(0), 0)
        listed = [
            self.page._size_combo.itemData(index)
            for index in range(1, self.page._size_combo.count())
        ]
        self.assertEqual(listed, list(FORUM_SIZE_STEPS))

    def test_picking_a_size_writes_the_token_for_that_paragraph(self) -> None:
        self.open_composer()
        self.page._thread_body.setPlainText("一段话")
        self.set_caret(1)
        self.page._size_combo.setCurrentIndex(self.page._size_combo.findData(24))
        self.assertEqual(self.page._body_text(), "[size=24]一段话")

    def test_going_back_to_default_removes_the_token(self) -> None:
        self.open_composer()
        self.page._thread_body.setPlainText("一段话")
        self.set_caret(1)
        self.page._size_combo.setCurrentIndex(self.page._size_combo.findData(24))
        self.page._size_combo.setCurrentIndex(0)
        self.assertEqual(self.page._body_text(), "一段话")

    def test_size_applies_to_every_paragraph_in_the_selection(self) -> None:
        self.open_composer()
        self.page._thread_body.setPlainText("甲\n乙")
        cursor = self.page._thread_body.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(3, cursor.KeepAnchor)
        self.page._thread_body.setTextCursor(cursor)
        self.page._size_combo.setCurrentIndex(self.page._size_combo.findData(18))
        self.assertEqual(self.page._body_text(), "[size=18]甲\n[size=18]乙")

    def test_centre_and_right_buttons_are_mutually_exclusive(self) -> None:
        self.open_composer()
        self.page._thread_body.setPlainText("一段话")
        self.set_caret(1)
        self.page._align_buttons["center"].click()
        self.assertEqual(self.page._body_text(), "[center]一段话")
        self.assertTrue(self.page._align_buttons["center"].isChecked())
        self.assertFalse(self.page._align_buttons["right"].isChecked())
        self.page._align_buttons["right"].click()
        self.assertEqual(self.page._body_text(), "[right]一段话")
        self.assertTrue(self.page._align_buttons["right"].isChecked())
        self.assertFalse(self.page._align_buttons["center"].isChecked())

    def test_pressing_the_same_alignment_again_goes_back_to_left(self) -> None:
        self.open_composer()
        self.page._thread_body.setPlainText("一段话")
        self.set_caret(1)
        self.page._align_buttons["center"].click()
        self.page._align_buttons["center"].click()
        self.assertEqual(self.page._body_text(), "一段话")

    def test_controls_follow_the_caret_into_a_formatted_paragraph(self) -> None:
        self.open_composer()
        self.page._thread_body.setPlainText("[right][size=20]甲\n乙")
        self.set_caret(1)
        self.assertEqual(self.page._size_combo.currentData(), 20)
        self.assertTrue(self.page._align_buttons["right"].isChecked())
        self.set_caret(self.page._body_text().index("乙") + 1)
        self.assertEqual(self.page._size_combo.currentData(), 0)
        self.assertTrue(self.page._align_buttons["left"].isChecked())

    def test_layout_wont_blow_the_character_limit(self) -> None:
        from lib.core.forum_api import FORUM_CONTENT_MAX

        self.open_composer()
        # 令牌本身也占字数：正文已经贴到上限时再加排版应当只提示、不改正文。
        self.page._thread_body.setPlainText("x" * FORUM_CONTENT_MAX)
        self.set_caret(1)
        self.page._align_buttons["center"].click()
        self.assertIn("超过", self.page._thread_error.text())
        self.assertEqual(len(self.page._body_text()), FORUM_CONTENT_MAX)

    def test_publishing_keeps_the_layout_tokens_in_the_body(self) -> None:
        self.open_composer()
        self.page._thread_body.setPlainText("一段话")
        self.set_caret(1)
        self.page._align_buttons["center"].click()
        self.page._thread_title.setText("标题")
        self.page._compose_send.click()
        call = [item for item in self.service.calls if item[0] == "post_thread"][-1]
        self.assertIn("[center]", call[1]["content"])


class DetailLayoutTests(BoardPageTestCase):
    """详情页按令牌排版：字号铺到字符格式上，对齐落在块格式上。"""

    def open_post(self, content: str) -> None:
        """直接喂给详情页一段正文（绕开服务层），排版渲染是这一层的事。"""
        entry = post(1, content=content)
        self.service.current_post = entry
        self.page.on_post(entry)
        self.app.processEvents()

    def rich_widget(self) -> MarkupText:
        widgets = self.page._detail_host.findChildren(MarkupText)
        self.assertTrue(widgets)
        return widgets[0]

    def test_size_token_resizes_the_text(self) -> None:
        self.open_post("[size=28]大字")
        widget = self.rich_widget()
        sizes = set()
        block = widget.document().begin()
        while block.isValid():
            fragment = block.begin()
            while not fragment.atEnd():
                sizes.add(fragment.fragment().charFormat().font().pixelSize())
                fragment += 1
            block = block.next()
        self.assertIn(28, sizes)

    def test_alignment_token_moves_the_block(self) -> None:
        from PyQt5.QtCore import Qt

        self.open_post("[center]居中一段")
        self.assertEqual(
            int(self.rich_widget().document().begin().blockFormat().alignment()),
            int(Qt.AlignHCenter),
        )

    def test_tokens_are_not_printed_in_the_body(self) -> None:
        self.open_post("[size=28][center]正文")
        widgets = self.page._detail_host.findChildren(MarkupText)
        self.assertTrue(widgets)
        for widget in widgets:
            self.assertNotIn("[size=", widget.toPlainText())
            self.assertNotIn("[center]", widget.toPlainText())
        self.assertEqual("".join(widget.toPlainText() for widget in widgets), "正文")

    def test_a_paragraph_without_tokens_stays_on_the_plain_path(self) -> None:
        """没写令牌的段落不该被拽进富文本：排版令牌只影响写过它的那一段。"""
        self.open_post("[center]居中\n\n普通一段")
        self.assertEqual(len(self.page._detail_host.findChildren(MarkupText)), 1)

    def test_every_centred_line_stays_on_its_own_row(self) -> None:
        """发帖框按行写令牌：三行都居中时，详情页得是三段居中，而不是并成一行。"""
        from PyQt5.QtCore import Qt

        self.open_post("[center]第一行\n[center]第二行\n[center]第三行")
        widgets = self.page._detail_host.findChildren(MarkupText)
        self.assertEqual(len(widgets), 3)
        for widget in widgets:
            self.assertEqual(
                int(widget.document().begin().blockFormat().alignment()),
                int(Qt.AlignHCenter),
            )
        self.assertEqual(
            [widget.toPlainText() for widget in widgets], ["第一行", "第二行", "第三行"]
        )

    def test_a_token_line_does_not_swallow_the_next_plain_line(self) -> None:
        """令牌行后面紧跟的普通行是另一段：该回到默认的左对齐，也不该被并进去。"""
        from PyQt5.QtCore import Qt

        self.open_post("[center]居中\n普通一段")
        widgets = self.page._detail_host.findChildren(MarkupText)
        self.assertEqual(len(widgets), 1)
        labels = [
            child for child in self.page._detail_host.findChildren(QLabel)
            if child.objectName() == "ForumPostText"
        ]
        self.assertEqual([label.text() for label in labels], ["普通一段"])
        self.assertEqual(
            int(widgets[0].document().begin().blockFormat().alignment()),
            int(Qt.AlignHCenter),
        )

    def test_left_token_puts_a_paragraph_back_on_the_left(self) -> None:
        from PyQt5.QtCore import Qt

        self.open_post("[center]居中\n\n[left]靠左")
        widgets = self.page._detail_host.findChildren(MarkupText)
        self.assertEqual(len(widgets), 2)
        alignments = {
            int(widget.document().begin().blockFormat().alignment()) for widget in widgets
        }
        self.assertEqual(alignments, {int(Qt.AlignHCenter), int(Qt.AlignLeft)})


if __name__ == "__main__":
    unittest.main()
