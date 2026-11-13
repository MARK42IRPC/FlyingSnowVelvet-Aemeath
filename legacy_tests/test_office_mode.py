import unittest

from lib.core.event.center import Event, EventCenter, EventType
from lib.script.office.contracts import InteractionMode
from lib.script.office.mode import InteractionModeService


class OfficeModeTests(unittest.TestCase):
    def setUp(self):
        self.events = EventCenter(pump_factory=None)
        self.service = InteractionModeService(self.events)

    def tearDown(self):
        self.service.cleanup()
        self.events.cleanup()

    def test_default_is_companion_and_routes_ordinary_text(self):
        received = []
        self.events.subscribe(EventType.INPUT_CHAT, lambda event: received.append(event.data))

        self.events.publish(Event(EventType.INPUT_TEXT, {"text": " hello ", "source": "test"}))

        self.assertEqual(self.service.mode, InteractionMode.COMPANION)
        self.assertEqual(received[0]["text"], "hello")
        self.assertEqual(received[0]["mode_generation"], 0)

    def test_switch_routes_to_office_and_invalidates_old_generation(self):
        office = []
        changes = []
        stops = []
        self.events.subscribe(EventType.OFFICE_INPUT, lambda event: office.append(event.data))
        self.events.subscribe(EventType.INTERACTION_MODE_CHANGED, lambda event: changes.append(event.data))
        self.events.subscribe(EventType.MIC_STT_STOP, lambda event: stops.append(event.data))

        old_generation = self.service.generation
        changed = self.service.set_mode("office", source="test")
        self.events.publish(Event(EventType.INPUT_TEXT, {"text": "build it"}))

        self.assertTrue(changed)
        self.assertFalse(self.service.accepts_companion_generation(old_generation))
        self.assertEqual(office[0]["interaction_mode"], "office")
        self.assertEqual(changes[0]["generation"], 1)
        self.assertTrue(stops[0]["auto_only"])

    def test_set_mode_accepts_interaction_mode_enum(self):
        self.assertTrue(self.service.set_mode(InteractionMode.OFFICE, source="test"))
        self.assertEqual(self.service.mode, InteractionMode.OFFICE)

    def test_toggle_event_returns_to_companion(self):
        self.events.publish(Event(EventType.INTERACTION_MODE_SET, {"mode": "office"}))
        self.events.publish(Event(EventType.INTERACTION_MODE_SET, {"toggle": True}))
        self.assertEqual(self.service.mode, InteractionMode.COMPANION)
        self.assertEqual(self.service.generation, 2)


if __name__ == "__main__":
    unittest.main()
