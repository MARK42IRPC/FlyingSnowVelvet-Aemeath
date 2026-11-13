import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from lib.core.event.center import Event, EventCenter, EventType
from lib.core.timing.manager import TimingManager
from tests.timing_fakes import FakePump, FakeScheduler


class TimingSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.event_center = EventCenter(
            pump_factory=lambda callback: FakePump(callback),
        )
        self.scheduler = FakeScheduler()
        self.manager = TimingManager(
            frame_fps=100,
            gif_fps=20,
            scheduler=self.scheduler,
            event_center=self.event_center,
        )
        self.addCleanup(self.manager.cleanup)

    def test_start_and_stop_control_all_periodic_timers(self):
        self.assertEqual(len(self.scheduler.timers), 3)
        self.assertFalse(any(timer.active for timer in self.scheduler.timers))

        self.manager.start()

        self.assertEqual(
            [timer.interval_ms for timer in self.scheduler.timers],
            [50, 10, 50],
        )
        self.assertTrue(all(timer.active for timer in self.scheduler.timers))

        self.manager.stop()

        self.assertFalse(any(timer.active for timer in self.scheduler.timers))

    def test_tick_timer_publishes_events_and_runs_core_tasks(self):
        ticks = []
        triggered_tasks = []
        self.event_center.subscribe(EventType.TICK, lambda event: ticks.append(event.data))
        self.event_center.subscribe(
            EventType.TIMER,
            lambda event: triggered_tasks.append(event.data),
        )
        task_id = self.manager.add_task(120, repeat=False)
        self.manager.start()

        self.scheduler.timers[0].fire(2)

        self.assertEqual([tick["tick_count"] for tick in ticks], [1, 2])
        self.assertEqual(
            triggered_tasks,
            [{"task_id": task_id, "repeat": False}],
        )
        self.assertNotIn(task_id, self.manager._tasks)

    def test_pause_sources_suspend_tasks_but_not_global_ticks(self):
        ticks = []
        triggered_tasks = []
        self.event_center.subscribe(EventType.TICK, lambda event: ticks.append(event))
        self.event_center.subscribe(EventType.TIMER, lambda event: triggered_tasks.append(event))
        self.manager.add_task(50)
        self.manager.start()

        self.event_center.publish(Event(EventType.TIMER_PAUSE, {"source": "dialog"}))
        self.event_center.publish(Event(EventType.TIMER_PAUSE, {"source": "game"}))
        self.scheduler.timers[0].fire()
        self.event_center.publish(Event(EventType.TIMER_RESUME, {"source": "dialog"}))
        self.scheduler.timers[0].fire()

        self.assertEqual(len(ticks), 2)
        self.assertEqual(triggered_tasks, [])

        self.event_center.publish(Event(EventType.TIMER_RESUME, {"source": "game"}))
        self.scheduler.timers[0].fire()

        self.assertEqual(len(triggered_tasks), 1)

    def test_runtime_rate_changes_update_backend_intervals(self):
        self.manager.start()

        self.manager.set_frame_fps_limit("workbench", 25)
        self.manager.set_gif_fps(10)

        self.assertEqual(self.scheduler.timers[1].interval_ms, 40)
        self.assertEqual(self.scheduler.timers[2].interval_ms, 100)
        self.assertTrue(self.scheduler.timers[1].active)
        self.assertTrue(self.scheduler.timers[2].active)

    def test_cleanup_is_idempotent_and_releases_backend(self):
        self.manager.start()

        self.manager.cleanup()
        self.manager.cleanup()

        self.assertTrue(self.scheduler.cleaned)
        self.assertTrue(all(timer.cleaned for timer in self.scheduler.timers))
        self.assertFalse(any(timer.active for timer in self.scheduler.timers))
        with self.assertRaises(RuntimeError):
            self.manager.start()

    def test_core_timing_runs_when_pyqt_imports_are_blocked(self):
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

            from lib.core.event.center import EventCenter
            from lib.core.timing.manager import TimingManager

            class Pump:
                def __init__(self, callback): self.callback = callback
                def emit(self): self.callback()
                def disconnect(self): pass

            class Timer:
                def __init__(self, callback):
                    self.callback = callback
                    self.interval_ms = 0
                    self.active = False
                def start(self, interval_ms):
                    self.interval_ms = interval_ms
                    self.active = True
                def stop(self): self.active = False
                def set_interval(self, interval_ms): self.interval_ms = interval_ms
                def cleanup(self): self.stop()

            class Scheduler:
                def __init__(self): self.timers = []
                def create_periodic_timer(self, callback):
                    timer = Timer(callback)
                    self.timers.append(timer)
                    return timer
                def cleanup(self):
                    for timer in self.timers: timer.cleanup()

            center = EventCenter(pump_factory=lambda callback: Pump(callback))
            scheduler = Scheduler()
            manager = TimingManager(scheduler=scheduler, event_center=center)
            manager.start()
            assert [timer.interval_ms for timer in scheduler.timers] == [50, 16, 100]
            manager.cleanup()
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


if __name__ == "__main__":
    unittest.main()
