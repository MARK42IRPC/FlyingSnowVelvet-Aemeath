import ast
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from lib.core.event.center import EventType


class QtDependencyBoundaryTests(unittest.TestCase):
    @staticmethod
    def _qt_import_violations(repo_root: Path, paths) -> list[str]:
        violations = []
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                if any(
                    name == "PyQt5"
                    or name.startswith("PyQt5.")
                    or name == "lib.core.render.backends.qt"
                    or name.startswith("lib.core.render.backends.qt.")
                    for name in names
                ):
                    violations.append(str(path.relative_to(repo_root)))
                    break
        return violations

    def test_config_and_core_do_not_import_qt_or_qt_bridge(self):
        repo_root = Path(__file__).resolve().parents[1]
        roots = (repo_root / "config", repo_root / "lib" / "core")
        excluded = repo_root / "lib" / "core" / "render" / "backends" / "qt"

        violations = []
        for root in roots:
            for path in root.rglob("*.py"):
                if excluded in path.parents:
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        names = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        names = [node.module or ""]
                    else:
                        continue
                    if any(
                        name == "PyQt5"
                        or name.startswith("PyQt5.")
                        or name == "lib.core.render.backends.qt"
                        or name.startswith("lib.core.render.backends.qt.")
                        for name in names
                    ):
                        violations.append(str(path.relative_to(repo_root)))

        self.assertEqual(violations, [])

    def test_backend_neutral_core_does_not_import_qt_ui_implementations(self):
        repo_root = Path(__file__).resolve().parents[1]
        core_root = repo_root / "lib" / "core"
        excluded = core_root / "render" / "backends" / "qt"
        violations = []

        for path in core_root.rglob("*.py"):
            if excluded in path.parents:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                if any(
                    name == "lib.script.ui" or name.startswith("lib.script.ui.")
                    for name in names
                ):
                    violations.append(str(path.relative_to(repo_root)))
                    break

        self.assertEqual(violations, [])

    def test_repository_qt_imports_stay_in_explicit_toolkit_boundaries(self):
        repo_root = Path(__file__).resolve().parents[1]
        scan_roots = (
            repo_root / "config",
            repo_root / "lib",
            repo_root / "scripts",
        )
        paths = [
            path
            for root in scan_roots
            for path in root.rglob("*.py")
        ]
        qt_imports = {
            Path(path).as_posix()
            for path in self._qt_import_violations(repo_root, paths)
        }

        allowed_files = {
            # 打包前的发行包自检（``scripts/`` 不进 payload）要在离屏 Qt 里真的把工作台、
            # 办公窗口、论坛与粒子构造一遍，所以它是构建工具里唯一允许直接引用 PyQt5 的。
            "scripts/payload_selftest.py",
            "lib/script/bug_tracker/__main__.py",
            "lib/script/gemes/packages/official/lahai_tetris/code/lahai_tetris_pkg/render.py",
            "lib/script/gemes/packages/official/lahai_tetris/code/lahai_tetris_pkg/widget.py",
            "lib/script/app/qt_backend_bootstrap.py",
            "lib/script/app/qt_application_ui.py",
            "lib/script/app/workbench_helper_entry.py",
        }

        unexpected = sorted(
            path
            for path in qt_imports
            if not path.startswith("lib/core/render/backends/qt/")
            and not path.startswith("lib/script/ui/")
            and path not in allowed_files
        )
        self.assertEqual(unexpected, [])

    def test_backend_neutral_script_modules_do_not_import_qt_or_qt_bridge(self):
        repo_root = Path(__file__).resolve().parents[1]
        script_root = repo_root / "lib" / "script"
        directory_names = (
            "chat",
            "effects",
            "gsvmove",
            "mainpet",
            "microphone_stt",
            "music",
            "tool_dispatcher",
        )
        paths = []
        for name in directory_names:
            paths.extend((script_root / name).rglob("*.py"))
        paths.extend(
            script_root / "SEanima" / name
            for name in ("animation.py", "clip.py", "decoder.py", "effects.py")
        )
        paths.extend((script_root / "cloudmusic").rglob("*.py"))
        paths.extend((script_root / "workbench").rglob("*.py"))
        paths.extend((script_root / "gemes" / "MAIN").rglob("*.py"))
        paths.extend(
            path
            for path in (script_root / "bug_tracker").rglob("*.py")
            if path.name != "__main__.py"
        )
        lahai_root = (
            script_root
            / "gemes"
            / "packages"
            / "official"
            / "lahai_tetris"
            / "code"
            / "lahai_tetris_pkg"
        )
        paths.extend(lahai_root / name for name in ("constants.py", "model.py", "skills.py"))

        violations = self._qt_import_violations(repo_root, paths)

        self.assertEqual(violations, [])

    def test_backend_neutral_scripts_do_not_import_qt_ui_implementations(self):
        repo_root = Path(__file__).resolve().parents[1]
        script_root = repo_root / "lib" / "script"
        allowed_files = {
            script_root / "main.py",
            script_root / "app" / "qt_application_ui.py",
            script_root / "app" / "qt_backend_bootstrap.py",
            script_root / "app" / "workbench_helper_entry.py",
            script_root / "bug_tracker" / "__main__.py",
        }
        violations = []

        for path in script_root.rglob("*.py"):
            if script_root / "ui" in path.parents or path in allowed_files:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                if any(
                    name == "lib.script.ui" or name.startswith("lib.script.ui.")
                    for name in names
                ):
                    violations.append(str(path.relative_to(repo_root)))
                    break

        self.assertEqual(violations, [])

    def test_world_object_managers_only_use_backend_neutral_facades(self):
        repo_root = Path(__file__).resolve().parents[1]
        manager_paths = list((repo_root / "lib" / "script").glob("obj-*/manager.py"))
        violations = self._qt_import_violations(repo_root, manager_paths)
        self.assertEqual(violations, [])

        forbidden_tokens = (
            "QPoint",
            "QRect",
            "QImage",
            "QPixmap",
            "QWidget",
            "lib.core.render.backends.qt",
            ".geometry()",
            ".get_center()",
            ".width()",
            ".height()",
            ".x()",
            ".y()",
            "pixmap",
            "lib.script.ui",
            "PhysicsBody",
            "physics_body",
            "_fading",
            "_drag_offset",
            "_frozen",
            "_flipped",
            "list[object]",
        )
        for path in manager_paths:
            source = path.read_text(encoding="utf-8-sig")
            for token in forbidden_tokens:
                self.assertNotIn(token, source, f"{path.relative_to(repo_root)}: {token}")
            self.assertIn("load_image_resource", source)
            self.assertIn("WorldObjectInstance", source)
            self.assertIn("create_world_object", source)

    def test_world_object_contract_has_no_native_asset_or_instance_types(self):
        repo_root = Path(__file__).resolve().parents[1]
        path = repo_root / "lib" / "core" / "world_objects.py"
        source = path.read_text(encoding="utf-8-sig")
        for token in (
            "QImage",
            "QPixmap",
            "QWidget",
            "PhysicsBody",
            "WorldObjectImagePair",
            "flipped_image",
        ):
            self.assertNotIn(token, source, token)
        self.assertIn("class WorldObjectRequest", source)
        self.assertIn("class WorldObjectInstance", source)
        self.assertIn("class WorldObjectMotion", source)

    def test_core_event_protocol_has_no_toolkit_render_callback(self):
        self.assertFalse(hasattr(EventType, "DRAW_RENDER"))

    def test_gui_never_requests_open_gl_or_a_web_engine(self):
        # The payload drops Qt's software OpenGL rasterizer because nothing here
        # asks Qt for a GL context.  This is the other half of that decision: a
        # GL, Quick or WebEngine widget would need ``opengl32sw.dll`` shipped
        # (and the ANGLE pair) again.
        repo_root = Path(__file__).resolve().parents[1]
        forbidden_tokens = (
            "QtOpenGL",
            "QtQuick",
            "QtQml",
            "QtWebEngine",
            "QOpenGLWidget",
            "QOpenGLContext",
            "QOpenGLWindow",
            "QSurfaceFormat",
            "QQuickWidget",
            "AA_UseSoftwareOpenGL",
            "AA_UseDesktopOpenGL",
            "AA_UseOpenGLES",
        )
        for path in (repo_root / "lib").rglob("*.py"):
            source = path.read_text(encoding="utf-8-sig")
            for token in forbidden_tokens:
                self.assertNotIn(token, source, f"{path.relative_to(repo_root)}: {token}")

    def test_unimplemented_backends_stay_disabled_while_the_payload_omits_gl(self):
        from lib.core.render.router import get_backend_descriptors
        from scripts import build_offline_distribution as distribution

        unavailable = {
            descriptor.backend_id
            for descriptor in get_backend_descriptors()
            if not descriptor.available
        }
        # Enabling OpenGL (or adding a GL widget) means restoring the software
        # rasterizer in ``QT_BIN_FILES``; keep the two facts in sync.
        self.assertLessEqual({"opengl", "vulkan"}, unavailable)
        self.assertNotIn("opengl32sw.dll", distribution.QT_BIN_FILES)

    def test_core_graphics_contract_has_no_toolkit_images_or_painter_callbacks(self):
        repo_root = Path(__file__).resolve().parents[1]
        graphics_root = repo_root / "lib" / "core" / "render" / "visuals"
        contract_paths = (
            graphics_root / "backend.py",
            graphics_root / "commands.py",
            graphics_root / "resources.py",
            graphics_root / "scene.py",
        )
        forbidden_tokens = (
            "QImage",
            "QPixmap",
            "QPainter",
            "QRect",
            "PaintCallback",
            "RenderItem",
            "RenderRequest",
        )

        for path in contract_paths:
            source = path.read_text(encoding="utf-8-sig")
            for token in forbidden_tokens:
                self.assertNotIn(token, source, f"{path.relative_to(repo_root)}: {token}")

        self.assertFalse((repo_root / "lib" / "core" / "render_core.py").exists())
        self.assertFalse((repo_root / "lib" / "core" / "render_layer.py").exists())

    def test_layer_manager_only_uses_backend_neutral_window_hosts(self):
        repo_root = Path(__file__).resolve().parents[1]
        contract_paths = (
            repo_root / "lib" / "core" / "layer_manager.py",
            repo_root / "lib" / "core" / "window_host.py",
        )
        forbidden_tokens = (
            "QWidget",
            "SetWindowPos",
            "HWND_TOPMOST",
            ".isVisible()",
            ".raise_()",
            ".winId()",
        )

        for path in contract_paths:
            source = path.read_text(encoding="utf-8-sig")
            for token in forbidden_tokens:
                self.assertNotIn(token, source, f"{path.relative_to(repo_root)}: {token}")

        source = contract_paths[1].read_text(encoding="utf-8-sig")
        self.assertIn("class LayerWindowHost(Protocol)", source)

    def test_core_runtime_imports_when_pyqt_is_unavailable(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = textwrap.dedent(
            """
            import builtins

            original_import = builtins.__import__

            def blocked_import(name, *args, **kwargs):
                if name == "PyQt5" or name.startswith("PyQt5."):
                    raise ModuleNotFoundError("PyQt5 blocked by test")
                return original_import(name, *args, **kwargs)

            builtins.__import__ = blocked_import

            import config.config
            from lib.core.render.router import BackendRouter
            from lib.core.draw_core import DrawCore
            from lib.core.event.center import EventCenter
            from lib.core.render.visuals.commands import DrawRequest
            from lib.core.render.visuals.gif_loader import GifLoader
            from lib.core.render.visuals.resources import ImageResource, RasterFrame
            from lib.core.layer_manager import LayerManager
            from lib.core.pet_window import PetWindow
            from lib.core.physics import PhysicsWorld
            from lib.core.screen_utils import get_virtual_screen_rect

            draw_core = DrawCore()
            frame = RasterFrame(1, 1, bytes((255, 0, 0, 255)))
            draw_core.register_resource(ImageResource("pet", (frame,)))
            draw_core.add_draw_request(DrawRequest("pet"))
            assert draw_core.build_batch().commands[0].frame is frame
            assert draw_core._backend.__class__.__name__ == "_NullDrawBackend"
            assert GifLoader([]).load_all() == {}
            assert [item.backend_id for item in BackendRouter().descriptors()] == [
                "qt", "directx", "opengl", "vulkan"
            ]
            assert get_virtual_screen_rect().width > 0
            layer_manager = LayerManager()
            layer_window = object()
            layer_manager.register(layer_window, "PANEL", name="probe")
            layer_manager.enforce_now()
            assert layer_manager.snapshot()[0][3:] == ("probe", True)
            assert PetWindow.__name__ == "PetWindow"
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_workbench_helper_without_pyqt_reports_recoverable_error(self):
        """阶段 3 退出条件：无 PyQt5 时打开 Qt 工作台要给明确可恢复提示。"""
        repo_root = Path(__file__).resolve().parents[1]
        script = textwrap.dedent(
            """
            import builtins
            import ctypes
            import io
            import sys

            original_import = builtins.__import__

            def blocked_import(name, *args, **kwargs):
                if name == "PyQt5" or name.startswith("PyQt5."):
                    raise ModuleNotFoundError("PyQt5 blocked by test")
                return original_import(name, *args, **kwargs)

            builtins.__import__ = blocked_import
            sys.stderr = io.StringIO()

            boxes = []

            class _User32:
                def MessageBoxW(self, *args):
                    boxes.append(args)
                    return 1

            class _Windll:
                user32 = _User32()

            ctypes.windll = _Windll()

            from lib.script.app import workbench_helper_entry

            code = workbench_helper_entry.run_workbench_helper("office")
            stderr_text = sys.stderr.getvalue()
            sys.stderr = sys.__stderr__
            assert code == 1, code
            assert "PyQt5" in stderr_text, stderr_text
            assert len(boxes) == 1, boxes
            _hwnd, message, title, flags = boxes[0]
            assert "PyQt5" in message, message
            assert flags == 0x30, flags
            assert "\u63a7\u5236\u9762\u677f" in title, title
            assert not [name for name in sys.modules if name.startswith("PyQt5")]
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


    def test_backend_neutral_product_packages_import_without_pyqt(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = textwrap.dedent(
            """
            import builtins
            import sys

            original_import = builtins.__import__

            def blocked_import(name, *args, **kwargs):
                if name == "PyQt5" or name.startswith("PyQt5."):
                    raise AssertionError(f"backend-neutral package imported Qt: {name}")
                return original_import(name, *args, **kwargs)

            builtins.__import__ = blocked_import

            import lib.script.SEanima.animation
            import lib.script.bug_tracker.service
            import lib.script.cloudmusic.manager
            import lib.script.gemes.MAIN.runtime
            import lib.script.workbench.page_registry
            import lib.script.workbench.settings
            import lib.script.workbench.theme
            from lib.script.gemes.packages.official.lahai_tetris.code.lahai_tetris_pkg import constants

            assert constants.THEME["A"][0] == (255, 120, 126)
            assert not [name for name in sys.modules if name.startswith("PyQt5")]
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_dx_interaction_queries_and_ui_package_import_do_not_load_pyqt(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = textwrap.dedent(
            """
            import builtins
            import sys

            original_import = builtins.__import__

            def blocked_import(name, *args, **kwargs):
                if name == "PyQt5" or name.startswith("PyQt5."):
                    raise AssertionError(f"DX imported Qt: {name}")
                return original_import(name, *args, **kwargs)

            builtins.__import__ = blocked_import

            import lib.script.ui
            from lib.core.event.center import Event, EventType
            from lib.core.event.key_handler import KeyEventHandler
            from lib.core.game_obstacles import get_game_obstacle_rect
            from lib.core.render.visuals.types import Point, Rect
            from lib.core.input.types import Key
            from lib.script.mainpet.state import StateMachine

            class Entity:
                def get_core_geometry(self): return Rect(0, 0, 20, 20)
                def get_core_position(self): return Point(0, 0)
                def is_moving(self): return False
                def play_animation(self, *_args, **_kwargs): pass

            entity = Entity()
            state = StateMachine.__new__(StateMachine)
            state._entity = entity
            assert get_game_obstacle_rect() is None
            assert state._is_wander_target_blocked_by_lahai(Point(80, 40)) is False

            handler = KeyEventHandler.__new__(KeyEventHandler)
            handler._entity = entity
            handler._on_key_press(Event(EventType.KEY_PRESS, {"key": Key.LEFT}))
            assert not [name for name in sys.modules if name.startswith("PyQt5")]
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


    def test_drawing_tier_is_reachable_only_from_the_router_and_backend_windows(self):
        """档位 A：`backends/qt/drawing/` 的允许引用面。

        `drawing/` 是把声明式命令变成 QPainter 像素的地方。理想上只应被 `lib/core/render/router.py`
        与后端自带窗口的绘制回调引用，但当前 `lib/script/ui` 里仍有一批控件直接构造 `QtDrawBackend`
        并在自己的 `paintEvent` 里执行批次。这是既成事实，不是新许可：下面那份清单是**基线**，
        只允许缩短——把某个文件从直接绘制改成走共享 presenter 后，必须同步从清单里删掉它；
        任何新文件出现对 `drawing/` 的引用都会失败。
        """
        repo_root = Path(__file__).resolve().parents[1]
        drawing_prefix = "lib.core.render.backends.qt.drawing"

        #: 仍直接从 `drawing/` 构造绘制实现的 `lib/script/ui` 文件（基线，只减不增）。
        frozen_ui_draw_importers = {
            "lib/script/ui/ai_settings_panel.py",
            "lib/script/ui/ai_settings_tabs.py",
            "lib/script/ui/bubble.py",
            "lib/script/ui/clickthrough_button.py",
            "lib/script/ui/close_button.py",
            "lib/script/ui/cmd_window.py",
            "lib/script/ui/command_dialog.py",
            "lib/script/ui/command_hint_box.py",
            "lib/script/ui/forum_color_picker.py",
            "lib/script/ui/forum_sticker.py",
            "lib/script/ui/game_runtime.py",
            "lib/script/ui/mic_stt_indicator.py",
            "lib/script/ui/playlist_panel.py",
            "lib/script/ui/progress_panel.py",
            "lib/script/ui/qr_dialog_base.py",
            "lib/script/ui/rect_action_button_style.py",
            "lib/script/ui/restore_button.py",
            "lib/script/ui/speaker_band_slider.py",
            "lib/script/ui/speaker_control_buttons.py",
            "lib/script/ui/speaker_menu_style.py",
            "lib/script/ui/speaker_search_result_box.py",
            "lib/script/ui/speaker_volume_slider.py",
            "lib/script/ui/tooltip_panel.py",
            "lib/script/ui/tray_menu.py",
            "lib/script/ui/world_objects/clock.py",
            "lib/script/ui/world_objects/motor.py",
            "lib/script/ui/world_objects/snow_leopard.py",
            "lib/script/ui/world_objects/snow_pile.py",
            "lib/script/ui/world_objects/snowball.py",
            "lib/script/ui/world_objects/sofa.py",
            "lib/script/ui/world_objects/speaker.py",
        }
        #: 官方游戏包 v1 的控件沿用 Qt 页面约定，见 `doc/Qt边界契约.md` 第 2 节。
        frozen_other_importers = {
            "lib/script/gemes/packages/official/lahai_tetris/code/lahai_tetris_pkg/widget.py",
        }

        def _drawing_refs(path):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            found = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    if name == drawing_prefix or name.startswith(drawing_prefix + "."):
                        found.append(name)
            return found

        offenders = []
        for base in (repo_root / "lib", repo_root / "scripts"):
            for path in sorted(base.rglob("*.py")):
                relative = path.relative_to(repo_root).as_posix()
                #: `drawing/` 内部与同包 runtime 的装配连线不算越界。
                if "/render/backends/qt/drawing/" in relative:
                    continue
                if "/render/backends/qt/runtime/" in relative:
                    continue
                if not _drawing_refs(path):
                    continue
                if relative in frozen_ui_draw_importers or relative in frozen_other_importers:
                    continue
                offenders.append(relative)

        self.assertEqual(offenders, [], "新增了对 drawing/ 的直接引用；请改走共享 presenter 或扩展基线并说明理由")
        self.assertGreater(len(frozen_ui_draw_importers), 20)

    def test_qt_runtime_tier_is_not_named_by_business_or_ui_code(self):
        """档位 B：`backends/qt/runtime/` 的具体路径不得出现在业务脚本里。

        能力和绘制不同：字体度量、屏幕几何、文本排版必须能被业务层使用，只是不允许写死后端路径。
        当前它们以具体路径被 `lib/script/ui` 直接导入。清单同样是**冻结基线**，只减不增；
        迁移方向是 `lib/core/render/contract.py` 的协议 + 组合入口注入。
        """
        repo_root = Path(__file__).resolve().parents[1]
        runtime_prefix = "lib.core.render.backends.qt.runtime"

        frozen_ui_runtime_importers = {
            "lib/script/ui/ai_settings_panel.py",
            "lib/script/ui/announcement_dialog.py",
            "lib/script/ui/bubble.py",
            "lib/script/ui/bug_tracker_window.py",
            "lib/script/ui/chat_mode_button.py",
            "lib/script/ui/clickthrough_button.py",
            "lib/script/ui/close_button.py",
            "lib/script/ui/cmd_window.py",
            "lib/script/ui/command_dialog.py",
            "lib/script/ui/command_hint_box.py",
            "lib/script/ui/forum_account.py",
            "lib/script/ui/forum_board.py",
            "lib/script/ui/forum_color_control.py",
            "lib/script/ui/forum_window.py",
            "lib/script/ui/game_manager_window.py",
            "lib/script/ui/game_runtime.py",
            "lib/script/ui/help_window.py",
            "lib/script/ui/interaction_mode_button.py",
            "lib/script/ui/launch_wuwa_button.py",
            "lib/script/ui/mic_stt_indicator.py",
            "lib/script/ui/more_functions_button.py",
            "lib/script/ui/office_approval_dialog.py",
            "lib/script/ui/office_chat_view.py",
            "lib/script/ui/office_manager_card.py",
            "lib/script/ui/office_mode_page.py",
            "lib/script/ui/office_page.py",
            "lib/script/ui/office_style.py",
            "lib/script/ui/page_turn_buttons.py",
            "lib/script/ui/playlist_panel.py",
            "lib/script/ui/progress_panel.py",
            "lib/script/ui/qr_dialog_base.py",
            "lib/script/ui/rect_action_button_style.py",
            "lib/script/ui/restore_button.py",
            "lib/script/ui/scale_button.py",
            "lib/script/ui/speaker_control_buttons.py",
            "lib/script/ui/speaker_menu_style.py",
            "lib/script/ui/speaker_search_dialog.py",
            "lib/script/ui/speaker_search_result_box.py",
            "lib/script/ui/tooltip_panel.py",
            "lib/script/ui/tray_menu.py",
            "lib/script/ui/update_dialog.py",
            "lib/script/ui/voice_package_installer.py",
            "lib/script/ui/workbench_components.py",
            "lib/script/ui/workbench_settings_layout.py",
            "lib/script/ui/workbench_window.py",
            "lib/script/ui/world_objects/clock.py",
            "lib/script/ui/world_objects/motor.py",
            "lib/script/ui/world_objects/snow_pile.py",
            "lib/script/ui/world_objects/snowball.py",
            "lib/script/ui/world_objects/sofa.py",
            "lib/script/ui/world_objects/speaker.py",
        }

        offenders = []
        for path in sorted((repo_root / "lib" / "script").rglob("*.py")):
            relative = path.relative_to(repo_root).as_posix()
            if not relative.startswith("lib/script/ui/"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    if name == runtime_prefix or name.startswith(runtime_prefix + "."):
                        if relative not in frozen_ui_runtime_importers:
                            offenders.append(f"{relative}:{node.lineno}:{name}")

        self.assertEqual(offenders, [], "业务层新引入具体后端运行时路径；请经后端中立协议获取能力")
        self.assertGreater(len(frozen_ui_runtime_importers), 20)

    def test_qt_package_has_no_wildcard_reexport(self):
        """`backends/qt/__init__.py` 不得用通配符聚合并导出子模块。

        一旦它 `from .runtime import *`，按子包前缀判定的档位规则就会被聚合入口绕过，
        档位 A 的清单也失去意义。这里用 AST 判定真实的通配符导入，而不是匹配文本
        （说明性文字里出现 `from .runtime import *` 不应误报）。
        """
        repo_root = Path(__file__).resolve().parents[1]
        init_path = (
            repo_root / "lib" / "core" / "render" / "backends" / "qt" / "__init__.py"
        )
        tree = ast.parse(init_path.read_text(encoding="utf-8-sig"), filename=str(init_path))
        wildcards = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if any(alias.name == "*" for alias in node.names):
                    wildcards.append(node.module)
            elif isinstance(node, ast.Import):
                if any(alias.name == "*" for alias in node.names):
                    wildcards.append("<bare>")
        self.assertEqual(wildcards, [])
        names = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                 for alias in node.names}
        for submodule in ("runtime", "drawing"):
            self.assertNotIn(submodule, names, f"__init__ 不得聚合导出 {submodule}")


if __name__ == "__main__":
    unittest.main()
