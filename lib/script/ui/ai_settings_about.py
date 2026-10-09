"""AI 设置面板的「支持作者 / 贡献者」两页（视图 + 控制器 + 描述）。

本模块是批次 3（C 类）首个切分单元：把 `ai_settings_panel.py` 里内聚且自洽的一族
——贡献卡片控件、两个只读页面的装配、外链/图片两个控制器、以及两页专属的 QSS——
整体搬到这里。切分口径与既有批次一致：

- **视图**：`build_sponsor_author_panel` / `build_contribution_list_panel` 用
  `SettingsPageScaffold` 装配真实控件树，控件顺序、对象名、间距与搬出前逐行等价。
- **控制器**：`open_sponsor_author_link` / `open_contribution_link` /
  `set_sponsor_author_image` 只做「开链接 / 贴图」两件事，不读面板状态；提示文案经
  `show_info` 回调注入，因此本模块不依赖面板类型，也不 import 面板。
- **描述**：`sponsor_author_stylesheet` / `contribution_list_stylesheet` 是两页专属的
  QSS 片段。批次 3 第三轮把这两段 QSS 的**正文**上移到
  `lib/core/render/visuals/ai_settings_panel_visuals.py`（与整段面板样式表同一事实源），
  这里改为从该模块导入并按原名转发，串仍然是逐字符相同的那两段。

本模块仍在 `lib/script/ui/` 下、仍 import `PyQt5`（两页都是 QWidget 树），因此
`tests/test_qt_dependency_boundaries.py` 的 `frozen_ui_qt_importers` 必须同时登记
本文件与面板文件；面板按原名保留 `_build_*_panel` / `_open_*` / `_set_*` 转发，
调用点与既有测试的导入路径不变。

本模块不 import 任何产品包（`chat` / `office` / `music` / `gsvmove`），
贡献名单的解析与路径仍由后端中立的 `ai_settings_contributions.py` 负责。
"""

from __future__ import annotations

import webbrowser
from collections.abc import Callable
from pathlib import Path

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from config.scale import scale_px
from lib.script.ui.ai_settings_contributions import (
    contribution_list_path,
    load_contribution_records,
    sponsor_author_image_path,
)
from lib.script.ui.render_bridge import ui_font as get_ui_font
from lib.script.ui.workbench_settings_layout import SettingsPageScaffold
from lib.core.render.visuals.ai_settings_panel_visuals import (
    contribution_list_fragment,
    sponsor_author_fragment,
)
from lib.script.workbench.theme import get_workbench_colors

#: 与面板同一像素档：卡片与行内字号都由它派生，取值必须与搬出前一致。
_CONFIG_FONT_SIZE = scale_px(17, min_abs=12)

SPONSOR_AUTHOR_URL = "https://afdian.com/a/fxxrdeskpet"


class _ContributionCardButton(QPushButton):
    """Contribution entry with a compact action hint."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._watermark_label: QLabel | None = None
        self._watermark_font = get_ui_font(size=max(scale_px(11, min_abs=9), _CONFIG_FONT_SIZE - scale_px(1, min_abs=1)))
        self._watermark_font.setBold(True)

    def bind_watermark_label(self, label: QLabel) -> None:
        self._watermark_label = label
        label.setProperty("preserveCustomFont", True)
        self._apply_watermark(False)

    def _layout_aware_size_hint(self, hint: QSize) -> QSize:
        card_layout = self.layout()
        if card_layout is not None:
            hint.setHeight(max(hint.height(), card_layout.minimumSize().height()))
        return hint

    def sizeHint(self) -> QSize:
        return self._layout_aware_size_hint(super().sizeHint())

    def minimumSizeHint(self) -> QSize:
        return self._layout_aware_size_hint(super().minimumSizeHint())

    def _apply_watermark(self, hovered: bool) -> None:
        if self._watermark_label is None:
            return
        colors = get_workbench_colors()
        self._watermark_label.setFont(self._watermark_font)
        self._watermark_label.setText("打开" if hovered else "主页")
        self._watermark_label.setStyleSheet(
            f"color: {colors.cyan if hovered else colors.text_dim};"
        )

    def enterEvent(self, event) -> None:
        self._apply_watermark(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._apply_watermark(False)
        super().leaveEvent(event)


def build_sponsor_author_panel(
panel: QWidget,
    scaffold: SettingsPageScaffold,
    category_title: str,
    title_label: QLabel,
hint_label: QLabel,
tab_meta: dict[str, dict],
root_dir: Path,
show_info: Callable[[str], None],
) -> QWidget:
    section = scaffold.add_help_section(
        "支持作者",
        "扫描赞助码，或通过下方按钮前往爱发电。",
        help_text=(
            "桌宠是免费的，也不会在里面塞广告。\n\n"
            "如果它帮到了你，可以扫赞助码或去爱发电支持一下维护工作；"
            "赞助完全自愿，不影响任何功能的使用。"
        ),
    )

    card = QWidget(section)
    card.setObjectName("sponsorAuthorCard")
    card_layout = QVBoxLayout(card)
    card_layout.setContentsMargins(0, 0, 0, 0)
    card_layout.setSpacing(scale_px(10, min_abs=8))

    image_frame = QWidget(card)
    image_frame.setObjectName("sponsorAuthorImageFrame")
    image_frame_layout = QVBoxLayout(image_frame)
    image_frame_layout.setContentsMargins(
        scale_px(10, min_abs=8),
        scale_px(10, min_abs=8),
        scale_px(10, min_abs=8),
        scale_px(10, min_abs=8),
    )
    image_frame_layout.setSpacing(0)
    image_frame.setMaximumWidth(scale_px(380, min_abs=320))

    image_label = QLabel(image_frame)
    image_label.setObjectName("sponsorAuthorImage")
    image_label.setAlignment(Qt.AlignCenter)
    image_label.setWordWrap(True)
    image_frame_layout.addWidget(image_label, 0, Qt.AlignCenter)
    set_sponsor_author_image(image_label, root_dir)

    card_layout.addWidget(image_frame, 0, Qt.AlignHCenter)

    sponsor_button = QPushButton("前往爱发电给作者买鸡腿饭", card)
    sponsor_button.setObjectName("sponsorAuthorButton")
    sponsor_button.setCursor(Qt.PointingHandCursor)
    sponsor_button.setFixedHeight(scale_px(36, min_abs=32))
    sponsor_button.setMaximumWidth(scale_px(380, min_abs=320))
    sponsor_button.clicked.connect(lambda: open_sponsor_author_link(show_info))
    card_layout.addWidget(sponsor_button, 0, Qt.AlignHCenter)

    section.body_layout.addWidget(card)
    scaffold.finish()

    tab_meta["sponsor_author"] = {
        "panel": panel,
        "fields": [],
        "defaults": {},
        "title": category_title,
        "title_label": title_label,
        "hint_label": hint_label,
        "section_title_labels": [section.title_label],
        "section_hint_labels": [section.description_label],
        "buttons": [sponsor_button],
    }

    return panel


def build_contribution_list_panel(
panel: QWidget,
    scaffold: SettingsPageScaffold,
    category_title: str,
    title_label: QLabel,
hint_label: QLabel,
tab_meta: dict[str, dict],
root_dir: Path,
show_info: Callable[[str], None],
) -> QWidget:
    records = [
        record
        for record in load_contribution_records(root_dir)
        if str(record.get("url") or "").strip()
    ]
    total_count = len(records)
    section = scaffold.add_help_section(
        f"贡献者 ({total_count})",
        "选择条目可打开对应开发者主页。",
        help_text=(
            "这份名单来自仓库里的贡献者记录，按角色整理。\n\n"
            "点条目会在浏览器里打开对应主页。名单只列已登记的贡献者，"
            "漏掉的话可以在仓库里提 issue 补上。"
        ),
    )

    buttons: list[QPushButton] = []
    if records:
        name_font = get_ui_font(size=max(scale_px(15, min_abs=12), _CONFIG_FONT_SIZE + scale_px(1, min_abs=1)))
        name_font.setBold(True)
        role_font = get_ui_font(size=max(scale_px(10, min_abs=9), _CONFIG_FONT_SIZE - scale_px(1, min_abs=1)))
        for record in records:
            name = str(record.get("name") or "未命名贡献者").strip()
            role = str(record.get("role") or "贡献者").strip()
            url = str(record.get("url") or "").strip()
            button = _ContributionCardButton(section)
            button.setObjectName("ContributionCardButton")
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(scale_px(74, min_abs=66))
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button_layout = QHBoxLayout(button)
            button_layout.setContentsMargins(
                scale_px(14, min_abs=12),
                scale_px(12, min_abs=10),
                scale_px(14, min_abs=12),
                scale_px(12, min_abs=10),
            )
            button_layout.setSpacing(scale_px(12, min_abs=10))

            accent = QWidget(button)
            accent.setObjectName("ContributionCardAccent")
            accent.setFixedWidth(scale_px(3, min_abs=2))
            accent.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            button_layout.addWidget(accent, 0)

            text_wrap = QWidget(button)
            text_layout = QVBoxLayout(text_wrap)
            text_layout.setContentsMargins(0, 0, 0, 0)
            text_layout.setSpacing(scale_px(4, min_abs=2))
            text_wrap.setAttribute(Qt.WA_TransparentForMouseEvents, True)

            name_label = QLabel(name, text_wrap)
            name_label.setFont(name_font)
            name_label.setObjectName("ContributionCardName")
            name_label.setProperty("preserveCustomFont", True)
            name_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            name_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            text_layout.addWidget(name_label, 0, Qt.AlignLeft)

            role_label = QLabel(role, text_wrap)
            role_label.setFont(role_font)
            role_label.setObjectName("ContributionCardRole")
            role_label.setProperty("preserveCustomFont", True)
            role_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            role_label.setWordWrap(True)
            role_label.setMinimumHeight(role_label.sizeHint().height())
            role_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            text_layout.addWidget(role_label, 0, Qt.AlignLeft)

            button_layout.addWidget(text_wrap, 1)

            watermark_wrap = QWidget(button)
            watermark_wrap.setFixedWidth(scale_px(64, min_abs=56))
            watermark_wrap.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            watermark_layout = QVBoxLayout(watermark_wrap)
            watermark_layout.setContentsMargins(0, 0, 0, 0)
            watermark_layout.setSpacing(0)
            watermark_layout.addStretch(1)

            watermark_label = QLabel("主页", watermark_wrap)
            watermark_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            watermark_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            button.bind_watermark_label(watermark_label)
            watermark_layout.addWidget(watermark_label, 0, Qt.AlignRight | Qt.AlignVCenter)
            button_layout.addWidget(watermark_wrap, 0)

            section.body_layout.addWidget(button)
            buttons.append(button)
            button.clicked.connect(lambda _checked=False, entry_name=name, entry_url=url: open_contribution_link(show_info, entry_name, entry_url))
    else:
        empty_label = QLabel(f"未读取到贡献名单，请检查文件是否存在：{contribution_list_path(root_dir)}", section)
        empty_label.setWordWrap(True)
        section.body_layout.addWidget(empty_label)

    scaffold.finish()

    tab_meta["contribution_list"] = {
        "panel": panel,
        "fields": [],
        "defaults": {},
        "title": category_title,
        "title_label": title_label,
        "hint_label": hint_label,
        "section_title_labels": [section.title_label],
        "section_hint_labels": [section.description_label],
        "buttons": buttons,
    }

    return panel


def set_sponsor_author_image(label: QLabel, root_dir: Path) -> None:
    image_path = sponsor_author_image_path(root_dir)
    if not image_path.exists():
        label.setText(f"未找到赞助图片：\n{image_path}")
        label.setPixmap(QPixmap())
        return

    pixmap = QPixmap(str(image_path))
    if pixmap.isNull():
        label.setText(f"赞助图片加载失败：\n{image_path.name}")
        label.setPixmap(QPixmap())
        return

    max_width = scale_px(340, min_abs=280)
    scaled = pixmap.scaledToWidth(max_width, Qt.SmoothTransformation)
    label.setPixmap(scaled)
    label.setText("")


def open_sponsor_author_link(show_info: Callable[[str], None]) -> None:
    try:
        opened = webbrowser.open(SPONSOR_AUTHOR_URL)
    except Exception as exc:
        show_info(f"打开爱发电链接失败：{exc}")
        return
    if not opened:
        show_info(f"未能自动打开链接，请手动访问：{SPONSOR_AUTHOR_URL}")


def open_contribution_link(show_info: Callable[[str], None], name: str, url: str) -> None:
    try:
        opened = webbrowser.open(url)
    except Exception as exc:
        show_info(f"打开 {name} 的主页失败：{exc}")
        return
    if not opened:
        show_info(f"未能自动打开 {name} 的主页，请手动访问：{url}")


def sponsor_author_stylesheet() -> str:
    """「支持作者」页专属 QSS 片段；整段样式表的事实源已上移到 `visuals/`。"""
    return sponsor_author_fragment(get_workbench_colors())


def contribution_list_stylesheet() -> str:
    """「贡献者」页专属 QSS 片段；整段样式表的事实源已上移到 `visuals/`。"""
    return contribution_list_fragment(get_workbench_colors())
