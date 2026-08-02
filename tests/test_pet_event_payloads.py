import importlib
import unittest
from pathlib import Path

from lib.core.event.center import Event, EventType
from lib.core.graphics.types import Point, Rect
from lib.core.pet_window import PetWindow

SnowLeopardManager = importlib.import_module(
    "lib.script.obj-雪豹.manager"
).SnowLeopardManager


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


if __name__ == "__main__":
    unittest.main()
