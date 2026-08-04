import importlib
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.core.event.center import Event, EventType
from lib.core.graphics.types import Point, Rect
from lib.core.pet_window import PetWindow

SnowLeopardManager = importlib.import_module(
    "lib.script.obj-雪豹.manager"
).SnowLeopardManager
speaker_module = importlib.import_module("lib.script.obj-音响.manager")
SpeakerManager = speaker_module.SpeakerManager


class _EventSink:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


class _PetProbe:
    def __init__(self):
        self._event_center = _EventSink()

    def get_core_position(self):
        return Point(10, 20)

    def get_core_geometry(self):
        return Rect(10, 20, 100, 80)


class _CenterPoint:
    def x(self):
        return 60

    def y(self):
        return 60


class _Leopard:
    def __init__(self):
        self._fading = False
        self.fade_started = False

    def get_center(self):
        return _CenterPoint()

    def start_fadeout(self):
        self.fade_started = True


class _Sound:
    def __init__(self):
        self.played = False

    def play(self):
        self.played = True


class PetEventPayloadTests(unittest.TestCase):
    def test_entity_position_response_uses_core_point(self):
        probe = _PetProbe()

        PetWindow._handle_entity_position_request(
            probe,
            Event(EventType.ENTITY_POSITION_REQUEST, {
                "entity_id": "pet_window",
                "request_id": "probe",
            }),
        )

        response = probe._event_center.events[-1]
        self.assertIs(response.type, EventType.ENTITY_POSITION_RESPONSE)
        self.assertEqual(response.data["position"], Point(10, 20))
        self.assertEqual(response.data["size"], (100, 80))

    def test_snow_leopard_consumer_accepts_core_point_response(self):
        manager = SnowLeopardManager.__new__(SnowLeopardManager)
        manager._cfg = {"interact_radius": 1}
        manager._leopards = [_Leopard()]
        manager._ams_enh_sound = _Sound()
        manager._pending_play = False
        manager._event_center = _EventSink()

        snow_leopard_module = importlib.import_module(SnowLeopardManager.__module__)
        with patch.object(
            snow_leopard_module,
            "get_world_object_center",
            return_value=Point(60, 60),
        ):
            manager._handle_entity_position_response(Event(
                EventType.ENTITY_POSITION_RESPONSE,
                {
                    "request_id": "snow_leopard_interaction",
                    "position": Point(10, 20),
                    "size": (100, 80),
                },
            ))

        self.assertTrue(manager._leopards[0].fade_started)
        self.assertTrue(manager._ams_enh_sound.played)
        self.assertTrue(manager._pending_play)
        self.assertEqual(
            [event.type for event in manager._event_center.events],
            [EventType.MANAGER_INTERACTION, EventType.ENTITY_STATE_QUERY],
        )

    def test_speaker_window_response_uses_plain_core_geometry_values(self):
        manager = SpeakerManager.__new__(SpeakerManager)
        manager._speakers = [object()]
        manager._event_center = _EventSink()

        with patch.object(
            speaker_module,
            "get_world_object_geometry",
            return_value=Rect(10, 20, 80, 40),
        ), patch.object(
            manager,
            "_get_alive_speakers",
            return_value=manager._speakers,
        ):
            manager._on_window_request(Event(EventType.SPEAKER_WINDOW_REQUEST, {}))

        response = manager._event_center.events[-1]
        self.assertIs(response.type, EventType.SPEAKER_WINDOW_RESPONSE)
        self.assertEqual(response.data["rects"], [(10, 20, 90, 60)])

    def test_production_callers_use_core_pet_geometry_interfaces(self):
        repo_root = Path(__file__).resolve().parents[1]
        relative_paths = (
            "lib/core/pet_window.py",
            "lib/script/obj-摩托/manager.py",
            "lib/script/obj-闹钟/manager.py",
            "lib/script/obj-沙发/manager.py",
            "lib/script/obj-雪豹/manager.py",
            "lib/script/obj-雪堆/manager.py",
            "lib/script/obj-雪球/manager.py",
            "lib/script/obj-音响/manager.py",
            "lib/script/ui/command_dialog.py",
            "lib/script/ui/restore_button.py",
        )

        for relative_path in relative_paths:
            source = (repo_root / relative_path).read_text(encoding="utf-8")
            self.assertNotIn(".get_position()", source, relative_path)
            self.assertNotIn(".get_geometry()", source, relative_path)

    def test_world_object_managers_do_not_prepare_qt_assets_inline(self):
        repo_root = Path(__file__).resolve().parents[1]
        manager_paths = (
            "lib/script/obj-摩托/manager.py",
            "lib/script/obj-闹钟/manager.py",
            "lib/script/obj-沙发/manager.py",
            "lib/script/obj-雪堆/manager.py",
            "lib/script/obj-雪球/manager.py",
            "lib/script/obj-雪豹/manager.py",
            "lib/script/obj-音响/manager.py",
        )
        forbidden_tokens = (
            "from PyQt5",
            "import PyQt5",
            "lib.core.qt_bridge",
            "QPixmap(",
            "QImage(",
            "QTransform(",
            "QApplication",
            "get_screen_geometry_for_point",
            "to_qpoint",
            ".geometry()",
            ".get_center()",
            "pixmap",
        )

        for relative_path in manager_paths:
            source = (repo_root / relative_path).read_text(encoding="utf-8")
            for token in forbidden_tokens:
                self.assertNotIn(token, source, f"{relative_path}: {token}")

    def test_world_object_managers_use_backend_factory_for_widget_creation(self):
        repo_root = Path(__file__).resolve().parents[1]
        manager_types = {
            "lib/script/obj-摩托/manager.py": "Mortor(",
            "lib/script/obj-闹钟/manager.py": "Clock(",
            "lib/script/obj-沙发/manager.py": "Sofa(",
            "lib/script/obj-雪堆/manager.py": "SnowPile(",
            "lib/script/obj-雪球/manager.py": "Snowball(",
            "lib/script/obj-雪豹/manager.py": "SnowLeopard(",
            "lib/script/obj-音响/manager.py": "Speaker(",
        }

        for relative_path, constructor in manager_types.items():
            source = (repo_root / relative_path).read_text(encoding="utf-8")
            self.assertIn("create_world_object(", source, relative_path)
            self.assertNotIn(constructor, source, relative_path)


if __name__ == "__main__":
    unittest.main()
