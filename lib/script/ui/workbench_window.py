"""Unified workbench window for settings, extensions, and maintenance tools."""

from __future__ import annotations

from collections.abc import Callable

from PyQt5.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, QSettings, Qt
from PyQt5.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config.config import UI
from config.font_config import apply_ui_font_tree, get_ui_font
from config.scale import scale_px
from lib.core.anchor_utils import apply_ui_opacity
from lib.core.event.center import Event, EventType, get_event_center
from lib.script.workbench.components import (
    WorkbenchOverviewPage,
    WorkbenchPetAboutButton,
    create_window_button,
)
from lib.script.workbench.page_registry import (
    WorkbenchPageRegistry,
    WorkbenchPageSpec,
    default_page_spec,
)
from lib.script.workbench.theme import workbench_stylesheet


_GROUP_ORDER = (
    "工作台",
    "智能交互",
    "桌宠与场景",
    "声音与媒体",
    "扩展与游戏",
    "系统与维护",
    "其他",
)
_NAV_FONT_SCALE = 1.25


class WorkbenchWindow(QWidget):
    def __init__(
        self,
        control_panel_factory,
        control_panel_page_specs: list[tuple[str, str]] | None = None,
        extra_page_specs: list[tuple[str, str, Callable[[], QWidget]] | WorkbenchPageSpec] | None = None,
    ) -> None:
        super().__init__()
        if callable(control_panel_factory):
            self._control_panel_factory = control_panel_factory
            self._control_panel = None
        else:
            self._control_panel_factory = lambda: control_panel_factory
            self._control_panel = control_panel_factory
        self._control_panel_page_specs = tuple(
            control_panel_page_specs
            or self._control_panel_page_metadata()
        )
        self._extra_page_specs = tuple(extra_page_specs or ())
        self._registry = WorkbenchPageRegistry()
        self._page_hosts: dict[str, QFrame] = {}
        self._page_buttons_by_id: dict[str, QPushButton] = {}
        self._group_labels: dict[str, QLabel] = {}
        self._external_page_slots: dict[str, tuple[QFrame, QVBoxLayout, Callable[[], object]]] = {}
        self._external_pages: dict[str, QWidget] = {}
        self._page_buttons: list[QPushButton] = []
        self._drag_targets: set[QWidget] = set()
        self._dragging = False
        self._drag_offset = QPoint()
        self._fading_out = False
        self._allow_hide_once = False
        self._geometry_restored = False
        self._settings = QSettings("FlyingSnow", "UnifiedWorkbench")
        self._always_on_top = self._settings.value("always_on_top", False, type=bool)
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.CONFIG_UPDATED, self._on_config_updated)

        self.setObjectName("WorkbenchWindow")
        self.setWindowTitle("飞行雪绒工作台")
        window_flags = Qt.Window | Qt.FramelessWindowHint
        if self._always_on_top:
            window_flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(window_flags)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumSize(scale_px(1060, min_abs=980), scale_px(680, min_abs=620))
        self.resize(scale_px(1280, min_abs=1120), scale_px(820, min_abs=720))
        self.setFont(get_ui_font(size=scale_px(12, min_abs=10)))

        self._opacity_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._opacity_anim.setDuration(UI.get("ui_fade_duration", 180))
        self._opacity_anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._opacity_anim.finished.connect(self._on_opacity_anim_finished)

        self._build_ui()
        self._attach_pages()
        self._build_navigation()
        apply_ui_font_tree(self)
        self._populate_about_menu()
        self._restore_window_state()
        self._set_current_page("overview")
        self.setWindowOpacity(0.0)
        super().hide()

    @property
    def page_registry(self) -> WorkbenchPageRegistry:
        return self._registry

    def _build_ui(self) -> None:
        self.setStyleSheet(workbench_stylesheet())

        shell = QFrame(self)
        shell.setObjectName("WorkbenchShell")
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        self._header = QFrame(shell)
        self._header.setObjectName("WorkbenchHeader")
        self._header.setFixedHeight(scale_px(68, min_abs=60))
        self._header.setCursor(Qt.OpenHandCursor)
        self._install_drag_target(self._header)
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(
            scale_px(18, min_abs=14),
            scale_px(10, min_abs=8),
            scale_px(10, min_abs=8),
            scale_px(10, min_abs=8),
        )
        header_layout.setSpacing(scale_px(10, min_abs=8))

        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(scale_px(1, min_abs=1))
        self._page_title_label = QLabel("总览", self._header)
        self._page_title_label.setObjectName("WorkbenchHeaderTitle")
        title_font = get_ui_font(size=scale_px(18, min_abs=16))
        title_font.setBold(True)
        self._page_title_label.setFont(title_font)
        self._page_group_label = QLabel("总览", self._header)
        self._page_group_label.setObjectName("WorkbenchHeaderGroup")
        self._page_group_label.setFont(get_ui_font(size=scale_px(10, min_abs=9)))
        self._install_drag_target(self._page_title_label)
        self._install_drag_target(self._page_group_label)
        title_box.addWidget(self._page_title_label)
        title_box.addWidget(self._page_group_label)
        header_layout.addLayout(title_box, 1)

        self._search = QLineEdit(self._header)
        self._search.setObjectName("WorkbenchSearch")
        self._search.setPlaceholderText("搜索设置或工具")
        self._search.setClearButtonEnabled(True)
        self._search.setMinimumWidth(scale_px(230, min_abs=210))
        self._search.setMaximumWidth(scale_px(340, min_abs=300))
        self._search.setFont(get_ui_font(size=scale_px(11, min_abs=10)))
        self._search.textChanged.connect(self._on_search_text_changed)
        self._search.returnPressed.connect(self._activate_search_result)
        header_layout.addWidget(self._search)

        self._pin_toggle = QCheckBox("置顶", self._header)
        self._pin_toggle.setObjectName("WorkbenchPinToggle")
        self._pin_toggle.setChecked(self._always_on_top)
        self._pin_toggle.setToolTip("让工作台保持在其他窗口上方")
        self._pin_toggle.toggled.connect(self._toggle_always_on_top)
        header_layout.addWidget(self._pin_toggle)

        self._about_menu = QMenu(self)
        self._about_menu.setObjectName("WorkbenchAboutMenu")
        self._about_button = WorkbenchPetAboutButton(self._header)
        self._about_button.setToolTip("关于")
        self._about_button.setMenu(self._about_menu)
        self._about_button.setPopupMode(QToolButton.InstantPopup)
        header_layout.addWidget(self._about_button)

        self._minimize_button = create_window_button(
            self._header, QStyle.SP_TitleBarMinButton, "最小化", self.showMinimized
        )
        self._maximize_button = create_window_button(
            self._header, QStyle.SP_TitleBarMaxButton, "最大化", self._toggle_maximized
        )
        self._close_button = create_window_button(
            self._header, QStyle.SP_TitleBarCloseButton, "关闭", self.fade_out, danger=True
        )
        header_layout.addWidget(self._minimize_button)
        header_layout.addWidget(self._maximize_button)
        header_layout.addWidget(self._close_button)
        shell_layout.addWidget(self._header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        navigation = QFrame(shell)
        navigation.setObjectName("WorkbenchNavigation")
        navigation.setFixedWidth(scale_px(224, min_abs=204))
        navigation_layout = QVBoxLayout(navigation)
        navigation_layout.setContentsMargins(0, 0, 0, scale_px(10, min_abs=8))
        navigation_layout.setSpacing(0)

        brand_box = QWidget(navigation)
        brand_layout = QVBoxLayout(brand_box)
        brand_layout.setContentsMargins(
            scale_px(14, min_abs=11),
            scale_px(15, min_abs=12),
            scale_px(14, min_abs=11),
            scale_px(9, min_abs=7),
        )
        brand_layout.setSpacing(0)
        brand = QLabel("飞行雪绒", brand_box)
        brand.setObjectName("WorkbenchBrand")
        brand_font = get_ui_font(size=scale_px(17, min_abs=15))
        brand_font.setBold(True)
        brand.setFont(brand_font)
        brand_meta = QLabel("工作台", brand_box)
        brand_meta.setObjectName("WorkbenchBrandMeta")
        brand_meta.setFont(get_ui_font(size=scale_px(10, min_abs=9)))
        brand_layout.addWidget(brand)
        brand_layout.addWidget(brand_meta)
        navigation_layout.addWidget(brand_box)

        navigation_scroll = QScrollArea(navigation)
        navigation_scroll.setObjectName("WorkbenchNavigationScroll")
        navigation_scroll.setWidgetResizable(True)
        navigation_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        navigation_scroll.setFrameShape(QFrame.NoFrame)
        navigation_viewport = QWidget(navigation_scroll)
        self._navigation_layout = QVBoxLayout(navigation_viewport)
        self._navigation_layout.setContentsMargins(0, 0, 0, 0)
        self._navigation_layout.setSpacing(scale_px(1, min_abs=1))
        navigation_scroll.setWidget(navigation_viewport)
        navigation_layout.addWidget(navigation_scroll, 1)
        body.addWidget(navigation)

        content = QFrame(shell)
        content.setObjectName("WorkbenchContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(
            scale_px(14, min_abs=11),
            scale_px(14, min_abs=11),
            scale_px(14, min_abs=11),
            scale_px(14, min_abs=11),
        )
        self._stack = QStackedWidget(content)
        self._stack.setObjectName("WorkbenchStack")
        content_layout.addWidget(self._stack)
        body.addWidget(content, 1)
        shell_layout.addLayout(body, 1)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(shell)

        self._button_group = QButtonGroup(navigation)
        self._button_group.setExclusive(True)
        self._size_grip = QSizeGrip(self)
        self._size_grip.setFixedSize(scale_px(18, min_abs=15), scale_px(18, min_abs=15))
        self._size_grip.raise_()

    def _on_config_updated(self, event: Event) -> None:
        values = (event.data or {}).get("values") or {}
        ui_values = values.get("UI")
        if not isinstance(ui_values, dict) or "workbench_light_theme" not in ui_values:
            return
        self.setStyleSheet(workbench_stylesheet())
        control_panel = self._control_panel
        if control_panel is not None:
            refresh_theme = getattr(control_panel, "refresh_workbench_theme", None)
            if callable(refresh_theme):
                refresh_theme()
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def _attach_pages(self) -> None:
        overview = WorkbenchOverviewPage(
            self._set_current_page,
            (
                ("常用设置", (("ai", "AI 与对话"), ("ui_anim", "界面与动画"), ("audio_music", "声音与媒体"))),
                (
                    "运行与维护",
                    (
                        ("game_manager", "游戏包"),
                        ("desktop_pet_update", "桌宠更新"),
                        ("bug_tracker", "故障跟踪"),
                    ),
                ),
            ),
            self._stack,
        )
        self._add_page(default_page_spec("overview"), overview)

        for page_id, fallback_title in self._control_panel_page_specs:
            self._add_page(
                default_page_spec(
                    page_id,
                    fallback_title,
                    factory=lambda target=page_id: self._create_control_panel_page(target),
                ),
                None,
            )

        for item in self._extra_page_specs:
            self._add_page(self._normalize_extra_page_spec(item), None)

    @staticmethod
    def _normalize_extra_page_spec(item) -> WorkbenchPageSpec:
        if isinstance(item, WorkbenchPageSpec):
            return item
        page_id, fallback_title, factory = item
        return default_page_spec(page_id, fallback_title, factory=factory)

    @staticmethod
    def _control_panel_page_metadata() -> list[tuple[str, str]]:
        from lib.script.workbench.settings import GENERAL_CONFIG_CATEGORIES

        return [('ai', 'AI 设置')] + [
            (spec.page_id, spec.tab_title)
            for spec in GENERAL_CONFIG_CATEGORIES
        ]

    def _ensure_control_panel(self):
        if self._control_panel is None:
            self._control_panel = self._control_panel_factory()
            self._control_panel.set_external_close_callback(self.fade_out)
        return self._control_panel

    def _create_control_panel_page(self, page_id: str) -> QWidget:
        panel = self._ensure_control_panel()
        page = panel.create_workbench_page(page_id)
        if not isinstance(page, QWidget):
            raise TypeError('control panel page factory must return QWidget')
        return page

    def _add_page(self, spec: WorkbenchPageSpec, page: QWidget | None) -> None:
        self._registry.register(spec)
        host = QFrame(self._stack)
        host.setObjectName("WorkbenchPageHost")
        host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(0)

        if page is None:
            placeholder = QLabel("正在准备页面...", host)
            placeholder.setObjectName("WorkbenchPlaceholder")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setFont(get_ui_font(size=scale_px(12, min_abs=10)))
            host_layout.addWidget(placeholder, 1)
            if spec.factory is not None:
                self._external_page_slots[spec.page_id] = (host, host_layout, spec.factory)
        else:
            self._embed_page(host, host_layout, page)

        self._stack.addWidget(host)
        self._page_hosts[spec.page_id] = host

    def _embed_page(
        self,
        host: QFrame,
        host_layout: QVBoxLayout,
        page: QWidget,
    ) -> None:
        if hasattr(page, "set_external_close_callback"):
            page.set_external_close_callback(self.fade_out)
        if hasattr(page, "set_embedded_mode"):
            page.set_embedded_mode(True)
        page.setParent(host)
        page.setWindowFlags(Qt.Widget)
        page.setWindowOpacity(1.0)
        page.setObjectName(page.objectName() or "WorkbenchPage")
        page.setMinimumSize(0, 0)
        page.setMaximumSize(16777215, 16777215)
        page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        host_layout.addWidget(page, 1)
        page.show()

    def _build_navigation(self) -> None:
        grouped: dict[str, list[WorkbenchPageSpec]] = {}
        for spec in self._ordered_navigation_specs():
            grouped.setdefault(spec.group, []).append(spec)

        for group, pages in grouped.items():
            group_label = QLabel(group, self._button_group.parent())
            group_label.setObjectName("WorkbenchNavGroup")
            group_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            group_label.setMinimumHeight(scale_px(34, min_abs=30))
            group_label.setFont(get_ui_font(size=scale_px(9 * _NAV_FONT_SCALE, min_abs=10)))
            self._navigation_layout.addWidget(group_label)
            self._group_labels[group] = group_label

            for spec in pages:
                button = QPushButton(spec.title, self._button_group.parent())
                button.setObjectName("WorkbenchNavButton")
                button.setCheckable(True)
                button.setMinimumHeight(scale_px(42, min_abs=38))
                button.setFont(get_ui_font(size=scale_px(11 * _NAV_FONT_SCALE, min_abs=12)))
                button.setToolTip(spec.description)
                button.clicked.connect(
                    lambda _checked=False, target=spec.page_id: self._set_current_page(target)
                )
                self._button_group.addButton(button)
                self._navigation_layout.addWidget(button)
                self._page_buttons_by_id[spec.page_id] = button
                self._page_buttons.append(button)

        self._navigation_layout.addStretch(1)

    def _ordered_navigation_specs(self) -> tuple[WorkbenchPageSpec, ...]:
        specs = self._registry.navigation_pages()
        group_rank = {group: index for index, group in enumerate(_GROUP_ORDER)}
        original_rank = {spec.page_id: index for index, spec in enumerate(specs)}
        return tuple(
            sorted(
                specs,
                key=lambda spec: (
                    group_rank.get(spec.group, len(group_rank)),
                    original_rank[spec.page_id],
                ),
            )
        )

    def _populate_about_menu(self) -> None:
        self._about_menu.clear()
        about_pages = tuple(spec for spec in self._registry.all() if spec.group == "关于")
        for spec in about_pages:
            action = self._about_menu.addAction(spec.title)
            action.triggered.connect(
                lambda _checked=False, target=spec.page_id: self._set_current_page(target)
            )
        self._about_button.setEnabled(bool(about_pages))

    def _ensure_external_page(self, page_id: str) -> None:
        slot = self._external_page_slots.get(page_id)
        if slot is None or page_id in self._external_pages:
            return

        host, host_layout, factory = slot
        while host_layout.count():
            item = host_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        try:
            page = factory()
            if not isinstance(page, QWidget):
                raise TypeError("external page factory must return QWidget")
        except Exception as exc:
            page = QLabel(f"页面加载失败\n{exc}", host)
            page.setObjectName("WorkbenchPlaceholder")
            page.setAlignment(Qt.AlignCenter)
            host_layout.addWidget(page, 1)
            self._external_pages[page_id] = page
            return

        self._embed_page(host, host_layout, page)
        apply_ui_font_tree(page)
        self._external_pages[page_id] = page
        if hasattr(page, "refresh_games"):
            page.refresh_games()
        elif hasattr(page, "_refresh_now"):
            page._refresh_now()

    def _set_current_page(self, page_id: str) -> None:
        spec = self._registry.get(page_id) or self._registry.get("overview")
        if spec is None:
            return

        self._ensure_external_page(spec.page_id)
        self._stack.setCurrentWidget(self._page_hosts[spec.page_id])
        self._page_title_label.setText(spec.title)
        self._page_group_label.setText(spec.group)
        self._about_button.setProperty("active", spec.group == "关于")
        self._about_button.update()

        button = self._page_buttons_by_id.get(spec.page_id)
        if button is not None:
            button.setChecked(True)
        else:
            self._clear_navigation_selection()

    def _clear_navigation_selection(self) -> None:
        self._button_group.setExclusive(False)
        for button in self._page_buttons:
            button.setChecked(False)
        self._button_group.setExclusive(True)

    def _on_search_text_changed(self, query: str) -> None:
        matched_ids = {
            spec.page_id for spec in self._registry.search(query, navigation_only=True)
        }
        for page_id, button in self._page_buttons_by_id.items():
            button.setVisible(page_id in matched_ids)
        for group, label in self._group_labels.items():
            label.setVisible(
                any(
                    not button.isHidden()
                    for page_id, button in self._page_buttons_by_id.items()
                    if self._registry.require(page_id).group == group
                )
            )

    def _activate_search_result(self) -> None:
        matches = self._registry.search(self._search.text(), navigation_only=True)
        if not matches:
            return
        self._set_current_page(matches[0].page_id)
        self._search.clear()

    def show_page(self, page_id: str = "overview") -> None:
        if self._control_panel is not None:
            try:
                self._control_panel.load_values()
            except RuntimeError:
                pass
        self._set_current_page(page_id)
        self._ensure_window_on_screen()
        if self.isMinimized():
            self.showNormal()
        if self.isVisible():
            self.raise_()
            self.activateWindow()
            return
        self.fade_in()

    def _restore_window_state(self) -> None:
        geometry = self._settings.value("geometry")
        if geometry is not None:
            try:
                self._geometry_restored = bool(self.restoreGeometry(geometry))
            except (RuntimeError, TypeError):
                self._geometry_restored = False

    def _save_window_state(self) -> None:
        self._settings.setValue("geometry", self.saveGeometry())
        self._settings.setValue("always_on_top", self._always_on_top)

    def _ensure_window_on_screen(self) -> None:
        screens = QApplication.screens()
        if self._geometry_restored and any(
            screen.availableGeometry().intersects(self.frameGeometry()) for screen in screens
        ):
            return
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        self.move(
            geometry.x() + (geometry.width() - self.width()) // 2,
            geometry.y() + (geometry.height() - self.height()) // 2,
        )
        self._geometry_restored = True

    def _toggle_always_on_top(self, checked: bool) -> None:
        checked = bool(checked)
        if checked == self._always_on_top:
            return
        was_visible = self.isVisible()
        geometry = self.geometry()
        self._always_on_top = checked
        self.setWindowFlag(Qt.WindowStaysOnTopHint, checked)
        self.setGeometry(geometry)
        if was_visible:
            self.show()
            self.raise_()
            self.activateWindow()
        self._settings.setValue("always_on_top", checked)

    def _toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._refresh_window_state_controls()

    def _refresh_window_state_controls(self) -> None:
        maximized = self.isMaximized()
        icon = QStyle.SP_TitleBarNormalButton if maximized else QStyle.SP_TitleBarMaxButton
        self._maximize_button.setIcon(self.style().standardIcon(icon))
        self._maximize_button.setToolTip("还原" if maximized else "最大化")
        self._size_grip.setVisible(not maximized)

    def fade_in(self) -> None:
        self._opacity_anim.stop()
        self._fading_out = False
        self._allow_hide_once = False
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        self._opacity_anim.setStartValue(0.0)
        self._opacity_anim.setEndValue(apply_ui_opacity(1.0))
        self._opacity_anim.start()

    def fade_out(self) -> None:
        if self._fading_out or not self.isVisible():
            return
        self._save_window_state()
        self._fading_out = True
        self._opacity_anim.stop()
        self._opacity_anim.setStartValue(
            max(0.0, min(1.0, float(self.windowOpacity())))
        )
        self._opacity_anim.setEndValue(0.0)
        self._opacity_anim.start()

    def hide(self) -> None:
        if self._allow_hide_once or self._fading_out or not self.isVisible():
            super().hide()
            return
        self.fade_out()

    def hide_immediately(self) -> None:
        self._save_window_state()
        self._opacity_anim.stop()
        self._fading_out = False
        self._allow_hide_once = True
        try:
            super().hide()
        finally:
            self._allow_hide_once = False

    def _on_opacity_anim_finished(self) -> None:
        if not self._fading_out:
            return
        self._fading_out = False
        self._allow_hide_once = True
        try:
            super().hide()
        finally:
            self._allow_hide_once = False
            self.setWindowOpacity(apply_ui_opacity(1.0))

    def _install_drag_target(self, widget: QWidget) -> None:
        widget.installEventFilter(self)
        self._drag_targets.add(widget)

    def eventFilter(self, watched, event) -> bool:
        if watched in self._drag_targets:
            if (
                event.type() == QEvent.MouseButtonDblClick
                and event.button() == Qt.LeftButton
            ):
                self._toggle_maximized()
                event.accept()
                return True
            if (
                event.type() == QEvent.MouseButtonPress
                and event.button() == Qt.LeftButton
            ):
                if self.isMaximized():
                    return True
                self._dragging = True
                self._drag_offset = (
                    event.globalPos() - self.frameGeometry().topLeft()
                )
                self._header.setCursor(Qt.ClosedHandCursor)
                event.accept()
                return True
            if (
                event.type() == QEvent.MouseMove
                and self._dragging
                and (event.buttons() & Qt.LeftButton)
            ):
                self.move(event.globalPos() - self._drag_offset)
                event.accept()
                return True
            if (
                event.type() == QEvent.MouseButtonRelease
                and event.button() == Qt.LeftButton
            ):
                self._dragging = False
                self._header.setCursor(Qt.OpenHandCursor)
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if (
            event.type() == QEvent.WindowStateChange
            and hasattr(self, "_maximize_button")
        ):
            self._refresh_window_state_controls()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_size_grip"):
            self._size_grip.move(
                self.width() - self._size_grip.width(),
                self.height() - self._size_grip.height(),
            )

    def closeEvent(self, event) -> None:
        event.ignore()
        self.fade_out()

    def deleteLater(self) -> None:
        try:
            self._event_center.unsubscribe(EventType.CONFIG_UPDATED, self._on_config_updated)
        except (AttributeError, RuntimeError):
            pass
        super().deleteLater()
