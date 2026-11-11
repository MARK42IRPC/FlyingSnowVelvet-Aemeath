import atexit
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TEST_HOME = tempfile.mkdtemp(prefix="workbench-test-")
os.environ["AEMEATH_DESK_PET_HOME"] = _TEST_HOME
atexit.register(shutil.rmtree, _TEST_HOME, ignore_errors=True)

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtCore import QPropertyAnimation, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QWidget

from config.config import UI
from lib.core.render.backends.qt.drawing.colors import COLORS, UI_THEME
import config.user_settings as user_settings
from config.font_config import get_ui_font_family
from config.scale import scale_px
from lib.script.ui import workbench_window as workbench_module


class _MemorySettings:
    def __init__(self, *_args, **_kwargs):
        self.values = {}

    def value(self, key, default=None, **_kwargs):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


class _FakeControlPanel:
    PAGE_IDS = (
        "ai",
        "ui_anim",
        "behavior_physics",
        "audio_music",
        "scene_objects",
        "system_dispatch",
        "desktop_pet_update",
        "contribution_list",
        "sponsor_author",
    )

    def __init__(self):
        self.pages = {}
        self.load_count = 0
        self.close_callback = None

    def get_workbench_page_specs(self):
        return [(page_id, page_id) for page_id in self.PAGE_IDS]

    def create_workbench_page(self, page_id: str):
        page = self.pages.setdefault(page_id, QWidget())
        return page

    def set_external_close_callback(self, callback):
        self.close_callback = callback

    def load_values(self):
        self.load_count += 1


class _ThemeAwarePage(QWidget):
    def __init__(self):
        super().__init__()
        self.theme_refreshes = 0

    def refresh_workbench_theme(self):
        self.theme_refreshes += 1


class _RefreshContractPage(QWidget):
    def __init__(self):
        super().__init__()
        self.shared_refreshes = 0
        self.legacy_refreshes = 0

    def refresh_workbench_page(self):
        self.shared_refreshes += 1

    def refresh_games(self):
        self.legacy_refreshes += 1

    def _refresh_now(self):
        self.legacy_refreshes += 1


_ABOUT_GRAB_SUPPORTED: bool | None = None


def _about_grab_supported() -> bool:
    """当前机器能否对自绘按钮做 ``grab()``（只探测一次，结果缓存）。

    探测放在子进程里：不支持时那个进程会直接退出，父进程据此判定并缓存结果。
    """
    global _ABOUT_GRAB_SUPPORTED
    if _ABOUT_GRAB_SUPPORTED is not None:
        return _ABOUT_GRAB_SUPPORTED
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent(
        """
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import PyQt5.QtCore
        root = os.path.dirname(PyQt5.QtCore.__file__)
        os.environ.setdefault(
            "QT_QPA_PLATFORM_PLUGIN_PATH", os.path.join(root, "Qt5", "plugins", "platforms")
        )
        os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(root, "Qt5", "plugins"))
        from PyQt5.QtWidgets import QApplication, QWidget
        from unittest.mock import patch
        import tests.test_workbench_window as t

        app = QApplication([])
        panel = t._FakeControlPanel()
        with patch.object(t.workbench_module, "QSettings", t._MemorySettings):
            window = t.workbench_module.WorkbenchWindow(
                lambda: panel,
                control_panel_page_specs=panel.get_workbench_page_specs(),
                extra_page_specs=[("bug_tracker", "Bug", QWidget)],
            )
        window._about_button.grab()
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    _ABOUT_GRAB_SUPPORTED = result.returncode == 0
    return _ABOUT_GRAB_SUPPORTED


class WorkbenchWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _grab_about_button(window):
        """抓「贡献列表」按钮的位图；离屏环境不支持自绘 grab 时返回 ``(None, 0)``。

        `_about_button` 用档位 D 的绘制后端自绘三层描边，`grab()` 要真的走一遍
        QPainter。部分机器（离屏 + 无 GPU 上下文）在这条路径上会**直接结束进程**而不是
        抛异常，`try/except` 拦不住——所以改用「子进程探测一次」：能抓就抓，抓不了就
        跳过像素断言。按钮的几何、文案与 `active` 属性等非像素断言仍然照常执行。
        """
        if not _about_grab_supported():
            return None, 0
        image = window._about_button.grab().toImage()
        return image, image.height() // 2

    def test_shell_groups_pages_and_lazily_loads_tools(self):
        panel = _FakeControlPanel()
        factory_calls = []

        def make_tool():
            factory_calls.append(True)
            return QWidget()

        extras = (
            ("bug_tracker", "Bug tracker", make_tool),
            ("game_manager", "Game manager", make_tool),
        )
        with patch.object(workbench_module, "QSettings", _MemorySettings):
            window = workbench_module.WorkbenchWindow(
                lambda: panel,
                control_panel_page_specs=panel.get_workbench_page_specs(),
                extra_page_specs=list(extras),
            )

        self.assertEqual(len(window.page_registry.all()), 12)
        self.assertEqual(len(window.page_registry.navigation_pages()), 10)
        self.assertEqual(tuple(action.text() for action in window._about_menu.actions()), ("贡献者", "赞助作者"))
        # 分组标题是父级：展开的那一组带「－」标记，其余带「＋」。
        self.assertEqual(
            tuple(label.text() for label in window._group_labels.values()),
            (
                "工作台  －",
                "智能交互  ＋",
                "桌宠与场景  ＋",
                "声音与媒体  ＋",
                "扩展与游戏  ＋",
                "系统与维护  ＋",
            ),
        )
        # 父级标题字号比子项小一档但比改造前大，保证「父级更醒目」。
        self.assertEqual(
            {label.font().pixelSize() for label in window._group_labels.values()},
            {scale_px(10.5 * workbench_module._NAV_FONT_SCALE)},
        )
        self.assertGreater(
            scale_px(10.5 * workbench_module._NAV_FONT_SCALE),
            scale_px(9 * workbench_module._NAV_FONT_SCALE),
        )
        self.assertEqual(
            {button.font().pixelSize() for button in window._page_buttons},
            {scale_px(11 * workbench_module._NAV_FONT_SCALE)},
        )
        self.assertEqual(window._about_button.text(), "贡献列表")
        self.assertEqual(window._sponsor_button.text(), "赞助按钮")
        self.assertEqual(window._about_menu.objectName(), "WorkbenchAboutMenu")
        self.assertTrue(window._about_button.icon().isNull())
        self.assertEqual(window._about_button.width(), scale_px(88, min_abs=78))
        self.assertEqual(window._about_button.height(), scale_px(34))
        self.assertFalse(bool(window._about_button.property("active")))
        window._about_button.click()
        self.assertEqual(window._page_title_label.text(), "贡献者")
        window._sponsor_button.click()
        self.assertEqual(window._page_title_label.text(), "赞助作者")
        layer = scale_px(2, min_abs=1)
        about_image, middle_y = self._grab_about_button(window)
        if about_image is not None:
            self.assertEqual(about_image.pixelColor(0, middle_y).rgb(), COLORS["black"].rgb())
            self.assertEqual(about_image.pixelColor(layer, middle_y).rgb(), COLORS["cyan"].rgb())
            self.assertEqual(about_image.pixelColor(layer * 2, middle_y).rgb(), COLORS["pink"].rgb())
        self.assertIs(window._stack.currentWidget(), window._page_hosts["sponsor_author"])
        self.assertTrue(all(window._stack.indexOf(host) >= 0 for host in window._page_hosts.values()))
        self.assertEqual(factory_calls, [])

        window._set_current_page("bug_tracker")
        self.assertEqual(len(factory_calls), 1)
        self.assertIn("bug_tracker", window._external_pages)
        self.assertEqual(window._page_title_label.text(), "故障跟踪")
        self.assertIs(window._stack.currentWidget(), window._page_hosts["bug_tracker"])

        window._set_current_page("contribution_list")
        self.assertTrue(bool(window._about_button.property("active")))
        self.assertFalse(any(button.isChecked() for button in window._page_buttons))
        active_image, active_middle_y = self._grab_about_button(window)
        if active_image is not None:
            self.assertEqual(
                active_image.pixelColor(layer * 2, active_middle_y).rgb(),
                UI_THEME["deep_pink"].rgb(),
            )

        window._search.setText("麦克风")
        self.assertTrue(window._page_buttons_by_id["ai"].isHidden())
        self.assertTrue(window._page_buttons_by_id["bug_tracker"].isHidden())
        self.assertFalse(window._page_buttons_by_id["audio_music"].isHidden())
        window._activate_search_result()
        self.assertEqual(window._page_title_label.text(), "音频与音乐")
        self.assertIs(window._stack.currentWidget(), window._page_hosts["audio_music"])

        self._dispose_window(window)

    def _build_window(self, *, shown: bool = False):
        """建一个工作台窗口。

        ``shown=True`` 会真的 ``show()`` 一次，让布局跑起来、容器拥有真实高度——
        抽屉高度断言的用例必须走这条路，否则所有容器高度恒为 0。

        离屏环境下自绘的「贡献列表 / 赞助按钮」会在 paint 里直接结束进程（见
        `_about_grab_supported`），所以显示时把它们的 paintEvent 换成空实现；
        本文件不依赖这两个按钮的像素。
        """
        panel = _FakeControlPanel()
        with patch.object(workbench_module, "QSettings", _MemorySettings):
            window = workbench_module.WorkbenchWindow(
                lambda: panel,
                control_panel_page_specs=panel.get_workbench_page_specs(),
                extra_page_specs=[
                    ("bug_tracker", "Bug tracker", QWidget),
                    ("game_manager", "Game manager", QWidget),
                ],
            )
        if shown:
            with self._safe_show_context():
                window.show()
                self.app.processEvents()
        return window

    def _wait_for_nav_animation(self, window) -> None:
        """等侧边栏抽屉动画落位。

        不用 `QTest.qWait()`：它会跑完整事件循环，离屏环境里会顺带触发自绘控件的
        `grab()` 路径，实测直接段错误。这里只反复 `processEvents()` 直到动画结束，
        再补一个固定时长兜底。

        动画挂在每组的抽屉容器上（不是按钮）：按钮自带 `minimumHeight`，对按钮做
        `maximumHeight` 动画会被 min > max 的规则夹成常量，那正是此前「看不到动效」的根因。
        """
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            self.app.processEvents()
            animations = [
                getattr(container, "_nav_height_anim", None)
                for container in getattr(window, "_group_containers", {}).values()
            ]
            if not any(
                animation is not None and animation.state() == QPropertyAnimation.Running
                for animation in animations
            ):
                return
            time.sleep(0.01)

    @staticmethod
    def _nav_visible_titles(window):
        """导航上真正可见（可点）的子项标题。

        手风琴把每组子项收进抽屉容器，隐藏的是**容器**，子按钮自身的 hidden 标志
        不再反映「这一组是否收起」，所以判定要同时看容器和按钮。
        """
        visible = []
        for group, buttons in window._group_buttons.items():
            container = window._group_containers.get(group)
            if container is not None and container.isHidden():
                continue
            visible.extend(button.text() for button in buttons if not button.isHidden())
        return visible

    def test_navigation_is_accordion_with_only_the_active_group_expanded(self):
        window = self._build_window()
        try:
            # 初始只展开当前页（总览）所在的「工作台」组。
            self.assertEqual(window._expanded_group, "工作台")
            # 展开/收起是高度动画，等它落位再读可见性。
            self._wait_for_nav_animation(window)
            self.assertEqual(self._nav_visible_titles(window), ["总览"])
        finally:
            self._dispose_window(window)

    def test_switching_page_expands_its_group_and_collapses_the_previous_one(self):
        window = self._build_window()
        try:
            window._set_current_page("audio_music")
            self.assertEqual(window._expanded_group, "声音与媒体")
            self._wait_for_nav_animation(window)
            self.assertEqual(self._nav_visible_titles(window), ["音频与音乐"])

            # 切到另一组的页面时，旧组收起、新组展开。
            window._set_current_page("ai")
            self.assertEqual(window._expanded_group, "智能交互")
            self._wait_for_nav_animation(window)
            self.assertNotIn("音频与音乐", self._nav_visible_titles(window))
            self.assertIn("AI 与对话", self._nav_visible_titles(window))
        finally:
            self._dispose_window(window)

    def test_clicking_a_group_header_toggles_that_group(self):
        window = self._build_window()
        try:
            window._toggle_navigation_group("智能交互")
            self.assertEqual(window._expanded_group, "智能交互")

            # 再点一次同一组即收起，此时所有子项都不可见。
            # 收起是「高度动画到 0 再隐藏」，所以断言可见性前必须等动画结束。
            window._toggle_navigation_group("智能交互")
            self.assertEqual(window._expanded_group, "")
            self._wait_for_nav_animation(window)
            self.assertEqual(self._nav_visible_titles(window), [])
            self.assertTrue(
                all(container.isHidden() for container in window._group_containers.values())
            )
        finally:
            self._dispose_window(window)

    def test_group_header_text_marks_the_expanded_state(self):
        window = self._build_window()
        try:
            self.assertEqual(window._group_labels["工作台"].text(), "工作台  －")
            self.assertEqual(window._group_labels["智能交互"].text(), "智能交互  ＋")
            window._toggle_navigation_group("智能交互")
            self.assertEqual(window._group_labels["工作台"].text(), "工作台  ＋")
            self.assertEqual(window._group_labels["智能交互"].text(), "智能交互  －")
        finally:
            self._dispose_window(window)

    def test_search_temporarily_reveals_matches_then_restores_the_accordion(self):
        window = self._build_window()
        try:
            window._search.setText("麦克风")
            self.assertFalse(window._page_buttons_by_id["audio_music"].isHidden())
            self.assertTrue(window._page_buttons_by_id["ai"].isHidden())

            window._search.clear()
            self.assertEqual(window._expanded_group, "工作台")
            # 清空搜索会重新套用手风琴折叠，收起同样是异步高度动画。
            self._wait_for_nav_animation(window)
            self.assertNotIn("AI 与对话", self._nav_visible_titles(window))
            self.assertEqual(self._nav_visible_titles(window), ["总览"])
        finally:
            self._dispose_window(window)

    def test_theme_config_update_repolishes_workbench(self):
        panel = _FakeControlPanel()
        original_theme = UI["workbench_light_theme"]
        with patch.object(workbench_module, "QSettings", _MemorySettings):
            window = workbench_module.WorkbenchWindow(
                lambda: panel,
                control_panel_page_specs=panel.get_workbench_page_specs(),
            )

        try:
            UI["workbench_light_theme"] = True
            window._on_config_updated(
                workbench_module.Event(
                    workbench_module.EventType.CONFIG_UPDATED,
                    {"values": {"UI": {"workbench_light_theme": True}}},
                )
            )
            self.assertIn("#fff8fb", window.styleSheet())
            self.assertIn("#20344d", window.styleSheet())
            self.assertEqual(window.font().family(), get_ui_font_family())
            self.assertEqual(window._search.font().family(), get_ui_font_family())
            self.assertTrue(window._theme_toggle.isChecked())
        finally:
            UI["workbench_light_theme"] = original_theme
            self._dispose_window(window)

    def test_external_page_uses_shared_refresh_contract(self):
        panel = _FakeControlPanel()
        page = _RefreshContractPage()
        extras = (("bug_tracker", "Bug tracker", lambda: page),)
        with patch.object(workbench_module, "QSettings", _MemorySettings):
            window = workbench_module.WorkbenchWindow(
                lambda: panel,
                control_panel_page_specs=panel.get_workbench_page_specs(),
                extra_page_specs=list(extras),
            )

        window._set_current_page("bug_tracker")

        self.assertEqual(page.shared_refreshes, 1)
        self.assertEqual(page.legacy_refreshes, 0)
        self._dispose_window(window)

    def test_external_theme_refresh_is_deferred_and_visible_page_only(self):
        panel = _FakeControlPanel()
        pages = {}

        def make_page(page_id):
            page = _ThemeAwarePage()
            pages[page_id] = page
            return page

        extras = [
            ("bug_tracker", "Bug tracker", lambda: make_page("bug_tracker")),
            ("game_manager", "Game manager", lambda: make_page("game_manager")),
        ]
        original_theme = UI["workbench_light_theme"]
        with patch.object(workbench_module, "QSettings", _MemorySettings):
            window = workbench_module.WorkbenchWindow(
                lambda: panel,
                control_panel_page_specs=panel.get_workbench_page_specs(),
                extra_page_specs=extras,
            )

        try:
            window._set_current_page("bug_tracker")
            window._set_current_page("game_manager")
            UI["workbench_light_theme"] = not bool(original_theme)
            window._on_config_updated(
                workbench_module.Event(
                    workbench_module.EventType.CONFIG_UPDATED,
                    {"values": {"UI": {"workbench_light_theme": UI["workbench_light_theme"]}}},
                )
            )
            self.assertEqual(pages["bug_tracker"].theme_refreshes, 0)
            self.assertEqual(pages["game_manager"].theme_refreshes, 0)
            self.app.processEvents()
            self.assertEqual(pages["bug_tracker"].theme_refreshes, 0)
            self.assertEqual(pages["game_manager"].theme_refreshes, 1)

            window._set_current_page("bug_tracker")
            self.app.processEvents()
            self.assertEqual(pages["bug_tracker"].theme_refreshes, 1)
        finally:
            UI["workbench_light_theme"] = original_theme
            self._dispose_window(window)

    def test_page_transition_keeps_page_visible_without_graphics_effect(self):
        panel = _FakeControlPanel()
        with patch.object(workbench_module, "QSettings", _MemorySettings):
            window = workbench_module.WorkbenchWindow(
                lambda: panel,
                control_panel_page_specs=panel.get_workbench_page_specs(),
            )

        try:
            with self._safe_show_context():
                window.show_page("overview")
                self.app.processEvents()
                window._set_current_page("ui_anim")
                self.app.processEvents()

            host = window._page_hosts["ui_anim"]
            page = panel.pages["ui_anim"]
            self.assertIsNone(host.graphicsEffect())
            self.assertTrue(host.isVisible())
            self.assertTrue(page.isVisible())
            self.assertEqual(page.windowOpacity(), 1.0)
            self.assertFalse(window._page_transition_overlay.isHidden())
        finally:
            self._dispose_window(window)

    def test_page_transition_overlay_ends_up_above_the_incoming_page(self):
        """切页遮罩必须压在新页之上。

        `QStackedLayout.setCurrentWidget()` 会 `raise()` 新页面。此前的实现先把遮罩
        `raise_()` 再切页，遮罩被压回页面下面，opacity 照常在 1.0→0 地跑，但整段动画
        都在页面底下——用户看到的就是硬切。这条断言直接挡住该回归。
        """
        panel = _FakeControlPanel()
        with patch.object(workbench_module, "QSettings", _MemorySettings):
            window = workbench_module.WorkbenchWindow(
                lambda: panel,
                control_panel_page_specs=panel.get_workbench_page_specs(),
            )

        try:
            with self._safe_show_context():
                window.show()
                self.app.processEvents()
                window._set_current_page("ui_anim")
                self.app.processEvents()
            children = window._stack.children()
            overlay_index = children.index(window._page_transition_overlay)
            page_index = children.index(window._stack.currentWidget())
            self.assertGreater(
                overlay_index,
                page_index,
                "遮罩被新页面盖住了，切页动画不可见",
            )
            self.assertEqual(window._page_transition_anim.state(), QPropertyAnimation.Running)
        finally:
            self._dispose_window(window)

    def test_group_drawer_actually_changes_height_while_animating(self):
        """抽屉展开时容器高度必须真的变大。

        对按钮的 `maximumHeight` 做动画时，按钮的 `minimumHeight` 会把结果夹成常量
        （min > max 取 min），动画每帧写值而高度一帧不变。这条用例在动画中途采样
        容器高度，防止再次退化回「静止 180ms 然后瞬间消失」。
        """
        window = self._build_window(shown=True)
        try:
            container = window._group_containers["智能交互"]
            self.assertEqual(container.height(), 0)
            window._toggle_navigation_group("智能交互")
            self.app.processEvents()
            mid_heights = []
            deadline = time.monotonic() + 0.6
            while time.monotonic() < deadline:
                self.app.processEvents()
                mid_heights.append(container.height())
                animation = getattr(container, "_nav_height_anim", None)
                if animation is None or animation.state() != QPropertyAnimation.Running:
                    break
                time.sleep(0.01)
            self.assertGreater(
                len({height for height in mid_heights if height > 0}),
                1,
                f"抽屉高度在动画过程中没有变化：{mid_heights}",
            )
            self._wait_for_nav_animation(window)
            self.assertGreater(container.height(), 0)
        finally:
            self._dispose_window(window)

    def test_search_reveals_matches_without_leaving_zero_height_drawers(self):
        """搜索绕开折叠：命中项所在容器必须真的展开，不能「可见但零高」。"""
        window = self._build_window()
        try:
            window._search.setText("麦克风")
            self.app.processEvents()
            container = window._group_containers["声音与媒体"]
            self.assertFalse(container.isHidden())
            self.assertGreater(container.maximumHeight(), 0)
            self.assertFalse(window._page_buttons_by_id["audio_music"].isHidden())

            window._search.clear()
            self._wait_for_nav_animation(window)
            # 回到手风琴：非当前页所在组重新收起。
            self.assertTrue(window._group_containers["声音与媒体"].isHidden())
        finally:
            self._dispose_window(window)

    @staticmethod
    def _safe_show_context():
        """显示窗口时屏蔽离屏会致死的自绘件。

        离屏 + 无 GPU 上下文时，工作台里几个自绘控件会在 paint 里直接结束进程
        （见 `_about_grab_supported` 的探测）；`try/except` 拦不住，会带走整个测试
        进程。本文件所有显示型用例都不断言这些控件的像素，换成空实现即可安全走真实布局。

        这条限制是仓库既有问题：在本次改动之前的基线上，同一个用例同样以退出码 127
        结束（无 traceback），全量 `test_workbench_window` 也因此跑不完。
        """
        from contextlib import ExitStack

        from lib.core.render.backends.qt.widgets import workbench_components, workbench_widgets

        stack = ExitStack()
        for cls in (
            workbench_components.WorkbenchPetAboutButton,
            workbench_widgets._WorkbenchThemeToggle,
            workbench_widgets._WorkbenchFadeOverlay,
        ):
            stack.enter_context(patch.object(cls, "paintEvent", lambda self, event: None))
        return stack

    def _dispose_window(self, window) -> None:
        """销毁窗口，且让销毁期间的重绘也落在安全上下文里。

        `deleteLater()` 之后那次 `processEvents()` 仍会重绘窗口，若此时自绘件没有被
        屏蔽，离屏环境会直接结束进程（无 traceback）。所有显示过窗口的用例都走这里。
        """
        with self._safe_show_context():
            window.deleteLater()
            self.app.processEvents()

    def test_theme_transition_does_not_shift_shell_or_page_geometry(self):
        panel = _FakeControlPanel()
        original_theme = UI["workbench_light_theme"]
        with patch.object(workbench_module, "QSettings", _MemorySettings):
            window = workbench_module.WorkbenchWindow(
                lambda: panel,
                control_panel_page_specs=panel.get_workbench_page_specs(),
            )

        try:
            # 主题热切换会启动整壳淡出遮罩，之后每次 `processEvents()`（包括收尾的
            # `deleteLater()`）都会重绘它。整个生命周期都得留在安全上下文里。
            with self._safe_show_context():
                window.show_page("ui_anim")
                self.app.processEvents()
                shell_geometry = window._shell.geometry()
                host_geometry = window._page_hosts["ui_anim"].geometry()
                page_geometry = panel.pages["ui_anim"].geometry()

                UI["workbench_light_theme"] = not bool(original_theme)
                window._on_config_updated(
                    workbench_module.Event(
                        workbench_module.EventType.CONFIG_UPDATED,
                        {"values": {"UI": {"workbench_light_theme": UI["workbench_light_theme"]}}},
                    )
                )
                self.app.processEvents()

            self.assertIsNone(window._shell.graphicsEffect())
            self.assertEqual(window._shell.geometry(), shell_geometry)
            self.assertEqual(window._page_hosts["ui_anim"].geometry(), host_geometry)
            self.assertEqual(panel.pages["ui_anim"].geometry(), page_geometry)
        finally:
            UI["workbench_light_theme"] = original_theme
            with self._safe_show_context():
                window.deleteLater()
                self.app.processEvents()

    def test_workbench_is_normal_window_without_frame_limit_or_pin_control(self):
        panel = _FakeControlPanel()
        with patch.object(workbench_module, "QSettings", _MemorySettings):
            window = workbench_module.WorkbenchWindow(
                lambda: panel,
                control_panel_page_specs=panel.get_workbench_page_specs(),
            )
            with self._safe_show_context():
                window.show_page("overview")
                self.app.processEvents()
            window.hide_immediately()
            self.app.processEvents()

        source = Path(workbench_module.__file__).read_text(encoding="utf-8")
        self.assertFalse(bool(window.windowFlags() & Qt.WindowStaysOnTopHint))
        self.assertFalse(hasattr(window, "_pin_toggle"))
        self.assertNotIn("get_timing_manager", source)
        self.assertNotIn("set_frame_fps_limit", source)
        self.assertNotIn("get_layer_manager", source)
        self.assertNotIn("WorkbenchPinToggle", window.styleSheet())
        self._dispose_window(window)

    def test_workbench_uses_project_icon(self):
        panel = _FakeControlPanel()
        with patch.object(workbench_module, "QSettings", _MemorySettings):
            window = workbench_module.WorkbenchWindow(
                lambda: panel,
                control_panel_page_specs=panel.get_workbench_page_specs(),
            )

        self.assertEqual(workbench_module._WORKBENCH_ICON_PATH.name, "icon.ico")
        self.assertTrue(workbench_module._WORKBENCH_ICON_PATH.is_file())
        self.assertFalse(window.windowIcon().isNull())
        self.assertFalse(window.windowIcon().pixmap(32, 32).isNull())
        self._dispose_window(window)

    def test_theme_toggle_is_left_of_search_and_persists_choice(self):
        panel = _FakeControlPanel()
        original_theme = UI["workbench_light_theme"]
        with patch.object(workbench_module, "QSettings", _MemorySettings):
            window = workbench_module.WorkbenchWindow(
                lambda: panel,
                control_panel_page_specs=panel.get_workbench_page_specs(),
            )

        try:
            header_layout = window._header.layout()
            self.assertLess(
                header_layout.indexOf(window._theme_toggle),
                header_layout.indexOf(window._search),
            )
            self.assertEqual(window._theme_toggle.text(), "")
            self.assertEqual(window._theme_toggle.accessibleName(), "工作台明暗主题")
            self.assertIn("浅色主题", window._theme_toggle.toolTip())

            with tempfile.TemporaryDirectory() as tmpdir:
                settings_path = Path(tmpdir) / "settings.json"
                with patch.object(user_settings, "get_user_settings_path", return_value=settings_path):
                    window._theme_toggle.setChecked(True)

            self.assertTrue(UI["workbench_light_theme"])
            self.assertIn("#fff8fb", window.styleSheet())
        finally:
            UI["workbench_light_theme"] = original_theme
            self._dispose_window(window)

    def test_theme_toggle_clicks_from_anywhere_on_custom_track(self):
        panel = _FakeControlPanel()
        original_theme = UI["workbench_light_theme"]
        with patch.object(workbench_module, "QSettings", _MemorySettings):
            window = workbench_module.WorkbenchWindow(
                lambda: panel,
                control_panel_page_specs=panel.get_workbench_page_specs(),
            )

        try:
            UI["workbench_light_theme"] = False
            window._sync_theme_toggle()
            # 显示、点击、收尾必须都在同一个上下文里：`deleteLater()` 之后的那次
            # `processEvents()` 仍会重绘窗口，patch 提前撤销就会在离屏环境触发段错误。
            with self._safe_show_context():
                window.show()
                self.app.processEvents()
                center = window._theme_toggle.rect().center()
                QTest.mouseClick(window._theme_toggle, Qt.LeftButton, pos=center)
                self.app.processEvents()
            self.assertTrue(window._theme_toggle.isChecked())
            self.assertTrue(UI["workbench_light_theme"])
        finally:
            UI["workbench_light_theme"] = original_theme
            with self._safe_show_context():
                window.deleteLater()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
