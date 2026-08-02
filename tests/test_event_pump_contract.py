import threading

from lib.core.event.center import Event, EventCenter, EventType


class _FakePump:
    def __init__(self, callback):
        self.callback = callback
        self.emits = 0
        self.disconnected = False

    def emit(self):
        self.emits += 1
        self.callback()

    def disconnect(self):
        self.disconnected = True


def test_event_center_uses_injected_pump_for_background_events():
    pumps = []
    received = []

    def factory(callback):
        pump = _FakePump(callback)
        pumps.append(pump)
        return pump

    center = EventCenter(factory)
    center.subscribe(EventType.INFORMATION, received.append)
    worker = threading.Thread(
        target=lambda: center.publish(Event(EventType.INFORMATION, {"value": 1}))
    )
    worker.start()
    worker.join()

    assert [event.data["value"] for event in received] == [1]
    assert pumps[0].emits == 1
    center.cleanup()
    assert pumps[0].disconnected
