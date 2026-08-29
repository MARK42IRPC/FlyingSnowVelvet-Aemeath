"""Markdown chat-bubble view for office task details."""

from __future__ import annotations

import re
from html import escape

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from config.scale import scale_px
from lib.core.qt_bridge.font import get_ui_font
from lib.script.ui.office_style import OFFICE_BUBBLE_PAD_H
from lib.script.workbench.theme import get_workbench_colors

_SENDER_LABELS = {"user": "你", "assistant": "助手", "system": "系统"}
_CODE_FONT = "Consolas"
_BUBBLE_PAD_H_TOTAL = 2 * OFFICE_BUBBLE_PAD_H
_RICH_TEXT_SLACK = 4

_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_+.-]*\s*$")
_BULLET_RE = re.compile(r"^[-*+]\s+(.*)$")
_NUMBER_RE = re.compile(r"^\d+\.\s+(.*)$")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+?)\*(?!\*)")


def _code_span(text: str, bg: str, fg: str) -> str:
    return (
        f"<span style=\"font-family:'{_CODE_FONT}',monospace;"
        f"background-color:{bg}; color:{fg};\">{text}</span>"
    )


def _fence_html(lines: list[str], bg: str, fg: str) -> str:
    body = "\n".join(escape(line) for line in lines)
    return (
        f"<pre style=\"font-family:'{_CODE_FONT}',monospace;"
        f"background-color:{bg}; color:{fg}; padding:6px;"
        f"border-radius:4px;\">{body}</pre>"
    )


def _inline_md(text: str, bg: str, fg: str) -> str:
    escaped = escape(text)
    escaped = _INLINE_CODE_RE.sub(lambda m: _code_span(m.group(1), bg, fg), escaped)
    escaped = _BOLD_RE.sub(r"<b>\1</b>", escaped)
    escaped = _ITALIC_RE.sub(r"<i>\1</i>", escaped)
    return escaped


def _md_to_rich(text: str, bg: str, fg: str) -> str:
    if not text:
        return ""
    blocks: list[str] = []
    fence: list[str] | None = None
    list_items: list[str] | None = None
    paragraph: list[str] | None = None

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            content = "<br/>".join(_inline_md(p, bg, fg) for p in paragraph)
            blocks.append(f"<p>{content}</p>")
            paragraph = None

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            blocks.append("<ul>" + "".join(f"<li>{item}</li>" for item in list_items) + "</ul>")
            list_items = None

    for raw in str(text).split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if fence is not None:
            if stripped.startswith("```"):
                blocks.append(_fence_html(fence, bg, fg))
                fence = None
            else:
                fence.append(line)
            continue
        if _FENCE_RE.match(stripped):
            flush_paragraph()
            flush_list()
            fence = []
            continue
        if not stripped:
            flush_paragraph()
            flush_list()
            continue
        if stripped.startswith("#"):
            flush_paragraph()
            flush_list()
            heading = _inline_md(stripped.lstrip("#").strip(), bg, fg)
            blocks.append(f"<b style='font-size:115%'>{heading}</b>")
            continue
        bullet = None
        bullet_match = _BULLET_RE.match(stripped)
        if bullet_match is not None:
            bullet = bullet_match.group(1).strip()
        else:
            number_match = _NUMBER_RE.match(stripped)
            if number_match is not None:
                bullet = number_match.group(1).strip()
        if bullet is not None:
            flush_paragraph()
            if list_items is None:
                list_items = []
            list_items.append(_inline_md(bullet, bg, fg))
            continue
        flush_list()
        if paragraph is None:
            paragraph = []
        paragraph.append(line.strip())

    if fence is not None:
        blocks.append(_fence_html(fence, bg, fg))
    flush_list()
    flush_paragraph()
    return "".join(blocks)


class OfficeConversationView(QScrollArea):
    """Chat detail with Markdown bubbles: user right / assistant left."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("OfficeConversation")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._message_key: tuple | None = None
        self._messages: list[tuple[str, str, bool, str]] = []
        self._rows: list[QWidget] = []
        self._bubbles: list[QFrame] = []
        self._bubble_naturals: list[int] = []
        self._message_text_labels: list[QLabel] = []
        self._message_headers: list[QLabel | None] = []
        self._message_bubble_refs: list[QFrame | None] = []
        self._bubble_max_width = scale_px(420, min_abs=380)

        self._container = QWidget(self)
        self._container.setObjectName("OfficeConversationContainer")
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(
            scale_px(8, min_abs=6),
            scale_px(10, min_abs=8),
            scale_px(8, min_abs=6),
            scale_px(10, min_abs=8),
        )
        self._layout.setSpacing(scale_px(7, min_abs=5))
        self._layout.addStretch(1)
        self.setWidget(self._container)

        self.refresh()

    def toPlainText(self) -> str:
        return "\n".join(text for _role, text, _streaming, _time in self._messages)

    def clear(self) -> None:
        self.set_messages([])

    def set_messages(
        self,
        messages: list[tuple[str, str, bool, str]],
    ) -> None:
        normalized = [
            (str(role), str(text), bool(streaming), str(time or ""))
            for role, text, streaming, time in messages
        ]
        key = tuple(normalized)
        if key == self._message_key:
            return
        old_messages = self._messages
        old_structure = tuple(role for role, _text, _streaming, _time in old_messages)
        new_structure = tuple(role for role, _text, _streaming, _time in normalized)
        can_update = (
            bool(self._message_key is not None)
            and len(normalized) >= len(old_messages)
            and new_structure[: len(old_structure)] == old_structure
        )
        if not can_update:
            self._message_key = key
            self._messages = list(normalized)
            self._sync_rows()
            return

        scrollbar = self.verticalScrollBar()
        was_at_bottom = scrollbar.maximum() == 0 or scrollbar.value() >= scrollbar.maximum() - 4
        self.setUpdatesEnabled(False)
        try:
            for index, message in enumerate(normalized[: len(old_messages)]):
                if message != old_messages[index]:
                    self._update_message(index, *message)
            if len(normalized) > len(old_messages):
                trailing = self._layout.takeAt(self._layout.count() - 1)
                if trailing is not None and trailing.widget() is not None:
                    self._layout.addItem(trailing)
                for message in normalized[len(old_messages) :]:
                    self._add_message(*message)
                self._layout.addStretch(1)
            self._messages = list(normalized)
            self._message_key = key
        finally:
            self.setUpdatesEnabled(True)
        if was_at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def refresh(self) -> None:
        colors = get_workbench_colors()
        self._code_bg = colors.surface_hover
        self._code_fg = colors.text
        if self._message_key is not None:
            self._sync_rows()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        width = self.viewport().width()
        self._bubble_max_width = max(scale_px(260, min_abs=220), int(width * 0.78))
        for bubble, natural in zip(self._bubbles, self._bubble_naturals):
            bubble.setMaximumWidth(self._bubble_max_width)
            bubble.setMinimumWidth(
                max(scale_px(48, min_abs=40), min(natural, self._bubble_max_width))
            )

    def _sync_rows(self) -> None:
        scrollbar = self.verticalScrollBar()
        was_at_bottom = (
            scrollbar.maximum() == 0 or scrollbar.value() >= scrollbar.maximum() - 4
        )
        self.setUpdatesEnabled(False)
        try:
            while self._layout.count():
                item = self._layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()
            self._rows = []
            self._bubbles = []
            self._bubble_naturals = []
            self._message_text_labels = []
            self._message_headers = []
            self._message_bubble_refs = []
            for message in self._messages:
                self._add_message(*message)
            self._layout.addStretch(1)
        finally:
            self.setUpdatesEnabled(True)
        if was_at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def _md(self, text: str) -> str:
        return _md_to_rich(text, self._code_bg, self._code_fg)

    def _add_message(
        self,
        role: str,
        text: str,
        is_streaming: bool,
        timestamp: str,
    ) -> None:
        if role == "system":
            label = QLabel(self._container)
            label.setObjectName("OfficeChatSystem")
            label.setTextFormat(Qt.RichText)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            label.setText(self._md(text))
            label.setAlignment(Qt.AlignHCenter)
            self._layout.addWidget(label)
            self._rows.append(label)
            self._message_text_labels.append(label)
            self._message_headers.append(None)
            self._message_bubble_refs.append(None)
            return

        row = QWidget(self._container)
        row.setObjectName("OfficeChatRow")
        hbox = QHBoxLayout(row)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(0)

        column = QWidget(row)
        column_layout = QVBoxLayout(column)
        column_layout.setContentsMargins(0, 0, 0, 0)
        column_layout.setSpacing(scale_px(2, min_abs=2))

        sender = _SENDER_LABELS.get(role, role)
        if is_streaming:
            sender = f"{sender}（生成中）"
        header = QLabel(sender, column)
        header.setObjectName("OfficeChatSender")
        header.setAlignment(Qt.AlignLeft if role == "assistant" else Qt.AlignRight)
        column_layout.addWidget(header)

        bubble = QFrame(column)
        bubble.setObjectName("OfficeChatBubble")
        bubble.setProperty("side", role)
        bubble.setMaximumWidth(self._bubble_max_width)
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(0, 0, 0, 0)
        bubble_layout.setSpacing(0)

        text_label = QLabel(bubble)
        text_label.setObjectName("OfficeChatBubbleText")
        text_label.setFont(get_ui_font(size=scale_px(12, min_abs=11)))
        text_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        text_label.setTextFormat(Qt.RichText)
        text_label.setWordWrap(True)
        text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        text_label.setText(self._md(text))
        bubble_layout.addWidget(text_label)
        column_layout.addWidget(bubble)

        natural = self._natural_width(text, text_label) + _BUBBLE_PAD_H_TOTAL + _RICH_TEXT_SLACK
        bubble.setMaximumWidth(self._bubble_max_width)
        bubble.setMinimumWidth(
            max(scale_px(48, min_abs=40), min(natural, self._bubble_max_width))
        )
        self._bubble_naturals.append(natural)

        if role == "assistant":
            hbox.addWidget(column)
            hbox.addStretch(1)
        else:
            hbox.addStretch(1)
            hbox.addWidget(column)

        self._layout.addWidget(row)
        self._rows.append(row)
        self._bubbles.append(bubble)
        self._message_text_labels.append(text_label)
        self._message_headers.append(header)
        self._message_bubble_refs.append(bubble)

    def _update_message(
        self,
        index: int,
        role: str,
        text: str,
        is_streaming: bool,
        timestamp: str,
    ) -> None:
        del timestamp
        label = self._message_text_labels[index]
        label.setText(self._md(text))
        header = self._message_headers[index]
        bubble = self._message_bubble_refs[index]
        if header is None or bubble is None:
            return
        sender = _SENDER_LABELS.get(role, role)
        if is_streaming:
            sender = f"{sender}（生成中）"
        header.setText(sender)
        natural = self._natural_width(text, label) + _BUBBLE_PAD_H_TOTAL + _RICH_TEXT_SLACK
        bubble.setMaximumWidth(self._bubble_max_width)
        bubble.setMinimumWidth(
            max(scale_px(48, min_abs=40), min(natural, self._bubble_max_width))
        )
        bubble_index = sum(ref is not None for ref in self._message_bubble_refs[: index + 1]) - 1
        if 0 <= bubble_index < len(self._bubble_naturals):
            self._bubble_naturals[bubble_index] = natural

    @staticmethod
    def _natural_width(text: str, label: QLabel) -> int:
        metrics = label.fontMetrics()
        longest = 0
        for line in str(text or "").split("\n"):
            longest = max(longest, metrics.horizontalAdvance(line))
        return longest


__all__ = ["OfficeConversationView"]
