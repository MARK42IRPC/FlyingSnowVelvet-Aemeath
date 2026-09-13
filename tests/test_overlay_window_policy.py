"""覆盖层窗口策略回归：激活抑制与清空后的隐藏去抖。

桌面覆盖层清空即 ``hide()`` / 重新出现即 ``show()`` 会让前台窗口在覆盖层和桌宠之间
来回移动，Windows 任务栏因此闪烁（用户反馈"类似焦点争夺"）。这里锁定两条契约：
覆盖层永不参与激活，且粒子/特效清空后先滞留一小段时间再隐藏。
"""
from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PyQt5.QtCore

_QT_ROOT = os.path.dirname(PyQt5.QtCore.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from config.config import PARTICLES
from lib.core.event.center import Event, EventType
from lib.core.qt_bridge.effect_system import EffectOverlay
from lib.core.qt_bridge.overlay_policy import (
    DEFAULT_HIDE_LINGER_MS,
    enable_no_activate,
    resolve_hide_linger_ms,
)
from lib.core.qt_bridge.particle_system import ParticleOverlay

_WS_EX_NOACTIVATE = 0x08000000


class _FakeWidget:
    def __init__(self, handle: int = 4321) -> None:
        self._handle = handle

    def winId(self) -> int:
        return self._handle


class _FakeWindowLongApis:
    """最小的 Win32 扩展样式替身。"""

    def __init__(self, *, window_exists: bool = True, ex_style: int = 0) -> None:
        self.window_exists = window_exists
        self.ex_style = ex_style
        self.writes = 0

    def _get(self, _handle: int, _index: int) -> int:
        return self.ex_style

    def _set(self, _handle: int, _index: int, value: int) -> int:
        self.writes += 1
        self.ex_style = int(value)
        return 0

    def _is_window(self, _handle: int) -> bool:
        return self.window_exists

    def apis(self) -> tuple:
        return (self._get, self._set, self._is_window)


class _FakeParticle:
    def __init__(self) -> None:
        self.x = 10.0
        self.y = 20.0
        self.size = 6.0
        self.alive = True
        self.layer = 650
        self.z = 0

    def update(self) -> None:
        return None


class _ParticleScript:
    def __init__(self, particle: _FakeParticle) -> None:
        self._particle = particle

    def create_particles(self, _area_type: str, _area_data) -> list:
        return [self._particle]


class _ParticleManager:
    def __init__(self, particle: _FakeParticle) -> None:
        self._script = _ParticleScript(particle)

    def get_script(self, _particle_id: str):
        return self._script


class _EffectManager:
    def get_script(self, _effect_id: str):
        return None


class _FakeEffect:
    def __init__(self) -> None:
        self.text = "policy"
        self.x = 12.0
        self.y = 12.0
        self.age = 0.0
        self.alive = True
        self.opacity = 1.0
        self.scale = 1.0
        self.rotation = 0.0
        self.layer = 0
        self.z = 0

    def update(self) -> None:
        self.age += 0.1


class OverlayPolicyHelperTests(unittest.TestCase):
    def test_enable_no_activate_sets_extension_style_once(self):
        apis = _FakeWindowLongApis()

        self.assertTrue(enable_no_activate(_FakeWidget(), window_long_apis=apis.apis()))
        self.assertTrue(apis.ex_style & _WS_EX_NOACTIVATE)
        self.assertEqual(apis.writes, 1)

        self.assertTrue(enable_no_activate(_FakeWidget(), window_long_apis=apis.apis()))
        self.assertEqual(apis.writes, 1, "已带 NOACTIVATE 时不应重复写扩展样式")

    def test_enable_no_activate_requires_a_native_window(self):
        apis = _FakeWindowLongApis(window_exists=False)

        self.assertFalse(enable_no_activate(_FakeWidget(), window_long_apis=apis.apis()))
        self.assertEqual(apis.writes, 0)

    def test_enable_no_activate_tolerates_missing_win32_apis(self):
        self.assertFalse(enable_no_activate(_FakeWidget(), window_long_apis=(None, None, None)))

    def test_enable_no_activate_keeps_positive_style_without_sign_extension(self):
        apis = _FakeWindowLongApis(ex_style=0x80000080 - 0x100000000)

        self.assertTrue(enable_no_activate(_FakeWidget(), window_long_apis=apis.apis()))
        self.assertEqual(apis.ex_style, 0x88000080)

    def test_resolve_hide_linger_ms_follows_config_and_overrides(self):
        self.assertEqual(resolve_hide_linger_ms(), int(PARTICLES["overlay_hide_linger_ms"]))
        self.assertEqual(resolve_hide_linger_ms(0), 0)
        self.assertEqual(resolve_hide_linger_ms("250"), 250)
        self.assertEqual(resolve_hide_linger_ms("bad"), DEFAULT_HIDE_LINGER_MS)


class _OverlayTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _wait_for(self, predicate, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        self.app.processEvents()
        return predicate()

    def _make_particle_overlay(self, linger_ms: int):
        particle = _FakeParticle()
        overlay = ParticleOverlay(
            _ParticleManager(particle),
            hide_linger_ms=linger_ms,
        )
        self.addCleanup(overlay.cleanup)
        return overlay, particle

    def _burst(self, overlay: ParticleOverlay) -> None:
        overlay._on_particle_request(Event(EventType.PARTICLE_REQUEST, {
            "particle_id": "policy_particle",
            "area_type": "point",
            "area_data": (120.0, 120.0),
        }))
        overlay._on_tick(Event(EventType.TICK))


class ParticleOverlayPolicyTests(_OverlayTestCase):
    def test_overlay_shows_without_activating(self):
        overlay, _particle = self._make_particle_overlay(linger_ms=200)

        self.assertTrue(overlay.testAttribute(Qt.WA_ShowWithoutActivating))

    def test_empty_particles_linger_before_hiding(self):
        overlay, particle = self._make_particle_overlay(linger_ms=200)
        self._burst(overlay)
        self.assertTrue(overlay.isVisible())

        particle.alive = False
        overlay._on_tick(Event(EventType.TICK))

        self.assertFalse(overlay._particles)
        self.assertTrue(overlay.isVisible(), "粒子清空后应先滞留，而不是立刻 hide()")
        self.assertTrue(self._wait_for(lambda: not overlay.isVisible()))

    def test_new_particles_cancel_the_pending_hide(self):
        overlay, particle = self._make_particle_overlay(linger_ms=150)
        self._burst(overlay)

        particle.alive = False
        overlay._on_tick(Event(EventType.TICK))
        particle.alive = True
        self._burst(overlay)

        time.sleep(0.3)
        self.app.processEvents()
        self.assertTrue(overlay.isVisible(), "滞留期内重新出现粒子应取消隐藏")

    def test_topmost_is_reasserted_only_on_a_real_show(self):
        overlay, particle = self._make_particle_overlay(linger_ms=150)

        with patch.object(overlay._layer_manager, "enforce_burst") as burst:
            self._burst(overlay)
            self.assertEqual(burst.call_count, 1)

            particle.alive = False
            overlay._on_tick(Event(EventType.TICK))
            particle.alive = True
            self._burst(overlay)
            self.assertEqual(burst.call_count, 1, "滞留期内复现不应重新 show()/重申置顶")

            particle.alive = False
            overlay._on_tick(Event(EventType.TICK))
            self.assertTrue(self._wait_for(lambda: not overlay.isVisible()))
            self._burst(overlay)
            self.assertEqual(burst.call_count, 2)
            self.assertTrue(overlay.isVisible())

    def test_flush_immediately_skips_the_linger(self):
        overlay, _particle = self._make_particle_overlay(linger_ms=5000)
        self._burst(overlay)
        self.assertTrue(overlay.isVisible())

        overlay.flush_immediately()

        self.assertFalse(overlay.isVisible(), "退出流程必须立即隐藏，不受滞留影响")

    def test_linger_can_be_disabled(self):
        overlay, particle = self._make_particle_overlay(linger_ms=0)
        self._burst(overlay)

        particle.alive = False
        overlay._on_tick(Event(EventType.TICK))

        self.assertFalse(overlay.isVisible())


class EffectOverlayPolicyTests(_OverlayTestCase):
    def test_effect_overlay_shows_without_activating(self):
        overlay = EffectOverlay(_EffectManager(), hide_linger_ms=200)
        self.addCleanup(overlay.cleanup)

        self.assertTrue(overlay.testAttribute(Qt.WA_ShowWithoutActivating))

    def test_empty_effects_linger_before_hiding(self):
        overlay = EffectOverlay(_EffectManager(), hide_linger_ms=150)
        self.addCleanup(overlay.cleanup)
        effect = _FakeEffect()
        overlay._effects = [effect]
        overlay.show()
        self.assertTrue(overlay.isVisible())

        effect.alive = False
        overlay._on_tick(Event(EventType.TICK))

        self.assertTrue(overlay.isVisible(), "特效清空后应先滞留，而不是立刻 hide()")
        self.assertTrue(self._wait_for(lambda: not overlay.isVisible()))

    def test_flush_immediately_skips_the_linger(self):
        overlay = EffectOverlay(_EffectManager(), hide_linger_ms=5000)
        self.addCleanup(overlay.cleanup)
        overlay._effects = [_FakeEffect()]
        overlay.show()

        overlay.flush_immediately()

        self.assertFalse(overlay.isVisible())


if __name__ == "__main__":
    unittest.main()
