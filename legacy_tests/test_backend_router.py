from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from lib.core.render.router import (
    BackendConfigurationError,
    BackendDescriptor,
    BackendRouter,
)


class BackendRouterTests(unittest.TestCase):
    def test_catalog_exposes_stable_backend_ids(self):
        router = BackendRouter()

        self.assertEqual(
            [descriptor.backend_id for descriptor in router.descriptors()],
            ["qt", "directx", "opengl", "vulkan"],
        )
        self.assertTrue(router.descriptors()[0].available)
        directx = next(item for item in router.descriptors() if item.backend_id == "directx")
        # DX 保留实现与测试，但按未启用后端处理：与 opengl/vulkan 同档。
        self.assertFalse(directx.available)
        self.assertTrue(directx.experimental)
        self.assertFalse(next(item for item in router.descriptors() if item.backend_id == "opengl").available)
        self.assertTrue(all(item.requires_restart for item in router.descriptors()))

    def test_registered_backend_is_selected_without_fallback(self):
        calls: list[str] = []
        router = BackendRouter()
        router.register_backend("qt", lambda: calls.append("qt"))

        selection = router.configure_selected_backend("qt")

        self.assertEqual(calls, ["qt"])
        self.assertEqual(selection.requested_backend, "qt")
        self.assertEqual(selection.active_backend, "qt")
        self.assertFalse(selection.fallback_used)
        self.assertIsNone(selection.reason)
        self.assertIs(router.get_active_selection(), selection)

    def test_unimplemented_backend_falls_back_to_qt_with_reason(self):
        calls: list[str] = []
        router = BackendRouter()
        router.register_backend("qt", lambda: calls.append("qt"))
        router.register_backend("opengl", lambda: calls.append("opengl"))

        selection = router.configure_selected_backend("opengl")

        self.assertEqual(calls, ["qt"])
        self.assertEqual(selection.requested_backend, "opengl")
        self.assertEqual(selection.active_backend, "qt")
        self.assertTrue(selection.fallback_used)
        self.assertIn("not implemented", selection.reason or "")
        self.assertFalse(selection.experimental)

    def test_dx_alias_routes_to_registered_directx_backend(self):
        calls: list[str] = []
        router = BackendRouter(
            (
                BackendDescriptor("qt", "Qt", True),
                BackendDescriptor("directx", "DirectX", True, experimental=True),
            )
        )
        router.register_backend("qt", lambda: calls.append("qt"))
        router.register_backend("directx", lambda: calls.append("directx"))

        selection = router.configure_selected_backend("DX")

        self.assertEqual(calls, ["directx"])
        self.assertEqual(selection.active_backend, "directx")
        self.assertFalse(selection.fallback_used)
        self.assertTrue(selection.experimental)

    def test_experimental_directx_failure_falls_back_to_qt(self):
        calls: list[str] = []
        router = BackendRouter(
            [
                BackendDescriptor("qt", "Qt", True),
                BackendDescriptor("directx", "DirectX", True, experimental=True),
            ]
        )

        def fail_directx() -> None:
            calls.append("directx")
            raise RuntimeError("device unavailable")

        router.register_backend("qt", lambda: calls.append("qt"))
        router.register_backend("directx", fail_directx)

        selection = router.configure_selected_backend("directx")

        self.assertEqual(calls, ["directx", "qt"])
        self.assertTrue(selection.fallback_used)
        self.assertEqual(selection.active_backend, "qt")
        self.assertIn("device unavailable", selection.reason or "")
        self.assertFalse(selection.experimental)

    def test_backend_initialization_error_falls_back_to_qt(self):
        calls: list[str] = []
        router = BackendRouter(
            (
                BackendDescriptor("qt", "Qt", True),
                BackendDescriptor("opengl", "OpenGL", True),
            )
        )

        def fail_opengl() -> None:
            calls.append("opengl")
            raise RuntimeError("adapter failed")

        router.register_backend("qt", lambda: calls.append("qt"))
        router.register_backend("opengl", fail_opengl)

        selection = router.configure_selected_backend("opengl")

        self.assertEqual(calls, ["opengl", "qt"])
        self.assertTrue(selection.fallback_used)
        self.assertIn("RuntimeError: adapter failed", selection.reason or "")

    def test_qt_initialization_error_is_not_retried(self):
        calls: list[str] = []
        router = BackendRouter()

        def fail_qt() -> None:
            calls.append("qt")
            raise RuntimeError("qt failed")

        router.register_backend("qt", fail_qt)

        with self.assertRaisesRegex(BackendConfigurationError, "qt failed"):
            router.configure_selected_backend("qt")
        self.assertEqual(calls, ["qt"])

    def test_duplicate_registration_rejects_a_different_configurer(self):
        router = BackendRouter()

        def configure() -> None:
            pass

        router.register_backend("qt", configure)
        router.register_backend("qt", configure)
        with self.assertRaisesRegex(ValueError, "already registered"):
            router.register_backend("qt", lambda: None)

    def test_qt_configurer_installs_complete_core_services(self):
        """Run in a subprocess: installing a backend is once-per-process."""
        repo_root = Path(__file__).resolve().parents[1]
        script = textwrap.dedent(
            """
            from lib.core.render.router import BackendRouter
            from lib.core.render.registry import (
                get_application_runtime_factory,
                get_application_ui_host_factory,
                get_deferred_call,
                get_desktop_backend_bundle,
                get_draw_backend_factory,
                get_effect_overlay_factory,
                get_event_pump_factory,
                get_layer_window_host_factory,
                get_particle_overlay_factory,
                get_pet_window_factory,
                get_scheduler_factory,
                get_screen_capture_provider,
                get_screen_capture_factory,
                get_tray_host_factory,
                get_screen_for_point_provider,
                get_virtual_screen_provider,
                get_window_host_factory,
            )
            from lib.core.world_objects import get_world_object_backend
            from lib.script.app.qt_backend_bootstrap import _configure_qt_backend

            router = BackendRouter()
            router.register_backend("qt", _configure_qt_backend)

            selection = router.configure_selected_backend("qt")
            assert selection.active_backend == "qt", selection

            for getter in (
                get_draw_backend_factory,
                get_application_runtime_factory,
                get_application_ui_host_factory,
                get_scheduler_factory,
                get_screen_capture_factory,
                get_pet_window_factory,
                get_particle_overlay_factory,
                get_effect_overlay_factory,
                get_tray_host_factory,
                get_event_pump_factory,
                get_deferred_call,
                get_virtual_screen_provider,
                get_screen_for_point_provider,
                get_screen_capture_provider,
                get_layer_window_host_factory,
                get_window_host_factory,
                get_desktop_backend_bundle,
            ):
                assert getter() is not None, getter.__name__
            assert get_world_object_backend().__class__.__name__ == "QtWorldObjectBackend"
            print("ok")
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("ok", result.stdout)



_BUNDLE_HELPER = """
def make_bundle(label):
    from lib.core.render.registry import DesktopBackendBundle
    from lib.core.render.visuals.types import Rect

    def factory():
        raise AssertionError("factories are never called by these tests")

    return DesktopBackendBundle(
        draw_backend_factory=factory,
        application_runtime_factory=factory,
        application_ui_host_factory=factory,
        scheduler_factory=factory,
        screen_capture_factory=factory,
        pet_window_factory=lambda gifs, overlay: label,
        particle_overlay_factory=factory,
        effect_overlay_factory=factory,
        tray_host_factory=factory,
        event_pump_factory=lambda callback: label,
        deferred_call=lambda delay_ms, callback: None,
        virtual_screen_provider=lambda: Rect(0, 0, 1, 1),
        screen_for_point_provider=lambda point: Rect(0, 0, 1, 1),
        layer_window_host_factory=factory,
    )
"""


class SingleRenderBackendTests(unittest.TestCase):
    """Exactly one backend may be live in a process.

    Every case runs in a subprocess: installing a backend mutates the core
    registry for the lifetime of the process, so doing it in-process would
    decide the outcome of whichever test ran next.
    """

    @staticmethod
    def _run(script: str) -> subprocess.CompletedProcess:
        repo_root = Path(__file__).resolve().parents[1]
        source = textwrap.dedent(_BUNDLE_HELPER) + textwrap.dedent(script)
        return subprocess.run(
            [sys.executable, "-c", source],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def _assert_ok(self, script: str) -> None:
        result = self._run(script)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_second_different_backend_is_refused_and_the_first_survives(self):
        self._assert_ok(
            """
            from lib.core.render.registry import (
                BackendAlreadyConfiguredError,
                get_desktop_backend_bundle,
                install_desktop_backend_bundle,
            )

            first = make_bundle("first")
            install_desktop_backend_bundle(first)
            try:
                install_desktop_backend_bundle(make_bundle("second"))
            except BackendAlreadyConfiguredError:
                pass
            else:
                raise AssertionError("a second backend was allowed to install")
            assert get_desktop_backend_bundle() is first
            """
        )

    def test_repeat_install_from_the_same_owner_keeps_the_original_bundle(self):
        self._assert_ok(
            """
            from lib.core.render.registry import (
                get_desktop_backend_bundle,
                install_desktop_backend_bundle,
            )

            owner = object()
            first = make_bundle("first")
            install_desktop_backend_bundle(first, owner=owner)
            # A rebuilt bundle is never value-equal to the first one (its
            # bound-method wrappers are fresh objects), so a re-registration
            # has to be recognised by owner identity, and the original bundle
            # has to stay in force for hosts already built from it.
            install_desktop_backend_bundle(make_bundle("rebuilt"), owner=owner)
            assert get_desktop_backend_bundle() is first
            """
        )

    def test_a_foreign_owner_cannot_displace_a_live_backend(self):
        self._assert_ok(
            """
            from lib.core.render.registry import (
                BackendAlreadyConfiguredError,
                get_desktop_backend_bundle,
                install_desktop_backend_bundle,
            )

            first = make_bundle("first")
            install_desktop_backend_bundle(first, owner=object())
            try:
                install_desktop_backend_bundle(make_bundle("other"), owner=object())
            except BackendAlreadyConfiguredError:
                pass
            else:
                raise AssertionError("a foreign owner displaced the live backend")
            assert get_desktop_backend_bundle() is first
            """
        )

    def test_only_the_installer_can_retract_its_own_bundle(self):
        self._assert_ok(
            """
            from lib.core.render.registry import (
                get_desktop_backend_bundle,
                install_desktop_backend_bundle,
                uninstall_desktop_backend_bundle,
            )

            owner = object()
            first = make_bundle("first")
            install_desktop_backend_bundle(first, owner=owner)

            uninstall_desktop_backend_bundle(object())
            assert get_desktop_backend_bundle() is first, "a foreign owner retracted the bundle"

            uninstall_desktop_backend_bundle(owner)
            assert get_desktop_backend_bundle() is None
            """
        )

    def test_a_retracted_install_leaves_room_for_the_fallback_backend(self):
        self._assert_ok(
            """
            from lib.core.render.registry import (
                get_desktop_backend_bundle,
                install_desktop_backend_bundle,
                uninstall_desktop_backend_bundle,
            )

            owner = object()
            install_desktop_backend_bundle(make_bundle("failed"), owner=owner)
            uninstall_desktop_backend_bundle(owner)

            fallback = make_bundle("fallback")
            install_desktop_backend_bundle(fallback)
            assert get_desktop_backend_bundle() is fallback
            """
        )

    def test_anonymous_installs_cannot_re_register_themselves(self):
        self._assert_ok(
            """
            from lib.core.render.registry import (
                BackendAlreadyConfiguredError,
                get_desktop_backend_bundle,
                install_desktop_backend_bundle,
            )

            # Without an owner token there is no way to prove the second call
            # is the same backend coming back, so it must be refused.
            install_desktop_backend_bundle(make_bundle("first"))
            try:
                install_desktop_backend_bundle(make_bundle("second"))
            except BackendAlreadyConfiguredError:
                pass
            else:
                raise AssertionError("an anonymous re-install was accepted")
            assert get_desktop_backend_bundle() is not None
            """
        )


if __name__ == "__main__":
    unittest.main()
