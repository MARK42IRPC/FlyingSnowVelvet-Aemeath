from __future__ import annotations

import unittest

from lib.core.backend_router import (
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
        router.register_backend("directx", lambda: calls.append("directx"))

        selection = router.configure_selected_backend("directx")

        self.assertEqual(calls, ["qt"])
        self.assertEqual(selection.requested_backend, "directx")
        self.assertEqual(selection.active_backend, "qt")
        self.assertTrue(selection.fallback_used)
        self.assertIn("not implemented", selection.reason or "")

    def test_dx_alias_routes_to_registered_directx_backend(self):
        calls: list[str] = []
        router = BackendRouter(
            (
                BackendDescriptor("qt", "Qt", True),
                BackendDescriptor("directx", "DirectX", True),
            )
        )
        router.register_backend("qt", lambda: calls.append("qt"))
        router.register_backend("directx", lambda: calls.append("directx"))

        selection = router.configure_selected_backend("DX")

        self.assertEqual(calls, ["directx"])
        self.assertEqual(selection.active_backend, "directx")
        self.assertFalse(selection.fallback_used)

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
        from lib.core.desktop_backend import (
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
        )
        from lib.core.qt_bridge.desktop_backend import configure_qt_desktop_backend
        from lib.core.world_objects import get_world_object_backend

        router = BackendRouter()
        router.register_backend("qt", configure_qt_desktop_backend)

        selection = router.configure_selected_backend("qt")

        self.assertEqual(selection.active_backend, "qt")
        self.assertIsNotNone(get_draw_backend_factory())
        self.assertIsNotNone(get_application_runtime_factory())
        self.assertIsNotNone(get_application_ui_host_factory())
        self.assertIsNotNone(get_scheduler_factory())
        self.assertIsNotNone(get_screen_capture_factory())
        self.assertIsNotNone(get_pet_window_factory())
        self.assertIsNotNone(get_particle_overlay_factory())
        self.assertIsNotNone(get_effect_overlay_factory())
        self.assertIsNotNone(get_tray_host_factory())
        self.assertIsNotNone(get_event_pump_factory())
        self.assertIsNotNone(get_deferred_call())
        self.assertIsNotNone(get_virtual_screen_provider())
        self.assertIsNotNone(get_screen_for_point_provider())
        self.assertIsNotNone(get_screen_capture_provider())
        self.assertIsNotNone(get_layer_window_host_factory())
        self.assertIsNotNone(get_desktop_backend_bundle())
        self.assertEqual(get_world_object_backend().__class__.__name__, "QtWorldObjectBackend")


if __name__ == "__main__":
    unittest.main()
