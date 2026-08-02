import unittest

from PyQt5.QtCore import QCoreApplication, QEventLoop, QTimer

from lib.core.qt_bridge.scheduler import QtScheduler


class QtSchedulerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QCoreApplication.instance() or QCoreApplication([])

    def test_periodic_timer_delivers_callbacks_and_releases_resources(self):
        scheduler = QtScheduler()
        loop = QEventLoop()
        callbacks = []

        def on_timeout():
            callbacks.append(True)
            loop.quit()

        timer = scheduler.create_periodic_timer(on_timeout)
        timer.start(5)
        QTimer.singleShot(200, loop.quit)
        loop.exec_()

        self.assertTrue(callbacks)
        self.assertTrue(timer.active)
        self.assertEqual(timer.interval_ms, 5)

        timer.set_interval(20)
        self.assertEqual(timer.interval_ms, 20)
        timer.stop()
        self.assertFalse(timer.active)

        scheduler.cleanup()
        scheduler.cleanup()
        with self.assertRaises(RuntimeError):
            scheduler.create_periodic_timer(lambda: None)


if __name__ == "__main__":
    unittest.main()
