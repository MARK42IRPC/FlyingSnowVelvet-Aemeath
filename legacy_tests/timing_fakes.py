class FakePump:
    def __init__(self, callback):
        self._callback = callback

    def emit(self):
        self._callback()

    def disconnect(self):
        self._callback = lambda: None


class FakePeriodicTimer:
    def __init__(self, callback):
        self.callback = callback
        self.interval_ms = 0
        self.active = False
        self.cleaned = False

    def start(self, interval_ms):
        if self.cleaned:
            raise RuntimeError("timer has been cleaned up")
        self.interval_ms = int(interval_ms)
        self.active = True

    def stop(self):
        self.active = False

    def set_interval(self, interval_ms):
        if self.cleaned:
            raise RuntimeError("timer has been cleaned up")
        self.interval_ms = int(interval_ms)

    def fire(self, count=1):
        for _ in range(count):
            if self.active:
                self.callback()

    def cleanup(self):
        self.stop()
        self.cleaned = True
        self.callback = lambda: None


class FakeScheduler:
    def __init__(self):
        self.timers = []
        self.cleaned = False

    def create_periodic_timer(self, callback):
        timer = FakePeriodicTimer(callback)
        self.timers.append(timer)
        return timer

    def cleanup(self):
        if self.cleaned:
            return
        self.cleaned = True
        for timer in self.timers:
            timer.cleanup()
