"""动感响应频段：对数映射、单块中心 ±10Hz 吸附、按音响登记，以及与 AudioMeter 的对接。"""
from __future__ import annotations

import math
import unittest
from unittest.mock import patch

from config.config_music import SPEAKER_AUDIO
from lib.core.audio_meter import AudioMeter
from lib.core.speaker_band import (
    BAND_HALF_WIDTH_HZ,
    BAND_SNAP_HZ,
    band_center_hz,
    band_center_ratio,
    band_from_center_hz,
    band_from_center_ratio,
    band_key,
    band_label,
    band_max_hz,
    band_min_hz,
    band_ratios,
    center_limits_hz,
    clamp_band,
    clear_speaker_bands,
    default_band,
    forget_speaker_band,
    frequency_from_ratio,
    get_speaker_band,
    ratio_from_frequency,
    registered_bands,
    set_speaker_band,
    snap_frequency_hz,
    speaker_response_intensity,
)


class BandMathTests(unittest.TestCase):
    def test_default_band_follows_speaker_audio_config(self):
        low, high = default_band()
        self.assertAlmostEqual(low, float(SPEAKER_AUDIO['freq_min']))
        self.assertAlmostEqual(high, float(SPEAKER_AUDIO['freq_max']))

    def test_slider_range_brackets_the_default_band(self):
        self.assertLessEqual(band_min_hz(), default_band()[0])
        self.assertGreaterEqual(band_max_hz(), default_band()[1])

    def test_ratio_and_frequency_are_logarithmic_inverses(self):
        middle = frequency_from_ratio(0.5)
        self.assertAlmostEqual(
            middle, math.sqrt(band_min_hz() * band_max_hz()), places=6,
        )
        self.assertAlmostEqual(ratio_from_frequency(middle), 0.5, places=9)
        self.assertAlmostEqual(frequency_from_ratio(1.0), band_max_hz(), places=6)
        self.assertAlmostEqual(frequency_from_ratio(0.0), band_min_hz(), places=6)

    def test_ratio_helpers_clamp_out_of_range_values(self):
        self.assertAlmostEqual(frequency_from_ratio(-3.0), band_min_hz(), places=6)
        self.assertAlmostEqual(frequency_from_ratio(9.0), band_max_hz(), places=6)
        self.assertAlmostEqual(ratio_from_frequency(1e9), 1.0, places=9)
        self.assertAlmostEqual(ratio_from_frequency(-1.0), 0.0, places=9)

    def test_clamp_band_sorts_and_keeps_a_minimum_gap(self):
        low, high = clamp_band(900.0, 120.0)
        self.assertLess(low, high)
        same, same_high = clamp_band(300.0, 300.0)
        self.assertLess(same, same_high)
        low, high = clamp_band(-20.0, 1e9)
        self.assertAlmostEqual(low, band_min_hz(), places=6)
        self.assertAlmostEqual(high, band_max_hz(), places=6)

    def test_clamp_band_survives_non_numeric_input(self):
        low, high = clamp_band('x', None)
        self.assertLess(low, high)
        self.assertGreaterEqual(low, band_min_hz())

    def test_band_label_rounds_to_whole_hertz(self):
        self.assertEqual(band_label((60.0, 250.0)), '60–250 Hz')

    def test_band_ratios_are_ordered_low_to_high(self):
        low_ratio, high_ratio = band_ratios((61.0, 244.0))
        self.assertLess(low_ratio, high_ratio)

    def test_center_band_is_always_half_width_either_side(self):
        low, high = band_from_center_hz(120.0)
        self.assertAlmostEqual(low, 120.0 - BAND_HALF_WIDTH_HZ, places=6)
        self.assertAlmostEqual(high, 120.0 + BAND_HALF_WIDTH_HZ, places=6)
        self.assertAlmostEqual(band_center_hz((low, high)), 120.0, places=6)

    def test_center_is_snapped_to_ten_hertz_steps(self):
        low, high = band_from_center_hz(123.4)
        center = band_center_hz((low, high))
        self.assertAlmostEqual(center, 120.0, places=6)
        self.assertAlmostEqual(center % BAND_SNAP_HZ, 0.0, places=6)

    def test_center_ratio_and_band_round_trip(self):
        for center in (60.0, 250.0, 1000.0):
            band = band_from_center_hz(center)
            self.assertAlmostEqual(
                band_center_hz(band_from_center_ratio(band_center_ratio(band))),
                center,
                places=6,
            )

    def test_center_stays_inside_the_slider_with_the_whole_block(self):
        low, high = center_limits_hz()
        self.assertAlmostEqual(low, band_min_hz() + BAND_HALF_WIDTH_HZ, places=6)
        self.assertAlmostEqual(high, band_max_hz() - BAND_HALF_WIDTH_HZ, places=6)
        for center in (0.0, 1e9, -50.0):
            snapped = snap_frequency_hz(center)
            self.assertGreaterEqual(snapped, low - 1e-9)
            self.assertLessEqual(snapped, high + 1e-9)

    def test_snap_frequency_survives_non_numeric_input(self):
        low, high = center_limits_hz()
        for bad in ("x", None, float("nan")):
            snapped = snap_frequency_hz(bad)
            self.assertGreaterEqual(snapped, low - 1e-9)
            self.assertLessEqual(snapped, high + 1e-9)

    def test_band_key_requires_a_valid_identity(self):
        self.assertEqual(band_key('qt', 3), 'qt:3')
        self.assertEqual(band_key('', 3), '')
        self.assertEqual(band_key('qt', 0), '')
        self.assertEqual(band_key('qt', 'abc'), '')


class BandRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_speaker_bands()
        self.addCleanup(clear_speaker_bands)

    def test_unknown_instance_falls_back_to_the_default_band(self):
        self.assertEqual(get_speaker_band('qt', 11), default_band())
        self.assertEqual(registered_bands(), ())

    def test_bands_are_registered_per_backend_and_instance(self):
        set_speaker_band('qt', 11, 120.0, 400.0)
        set_speaker_band('qt', 12, 40.0, 90.0)
        set_speaker_band('directx', 11, 200.0, 600.0)

        self.assertEqual(get_speaker_band('qt', 11), clamp_band(120.0, 400.0))
        self.assertEqual(get_speaker_band('qt', 12), clamp_band(40.0, 90.0))
        self.assertEqual(get_speaker_band('directx', 11), clamp_band(200.0, 600.0))
        self.assertEqual(len(registered_bands()), 3)

    def test_set_returns_the_clamped_band(self):
        band = set_speaker_band('qt', 11, -5.0, 1e9)
        self.assertEqual(band, clamp_band(-5.0, 1e9))
        self.assertEqual(get_speaker_band('qt', 11), band)

    def test_invalid_identity_is_ignored(self):
        set_speaker_band('', 0, 100.0, 200.0)
        self.assertEqual(registered_bands(), ())

    def test_forget_drops_the_entry_and_unregisters_from_the_meter(self):
        meter = _RecordingMeter()
        set_speaker_band('qt', 11, 100.0, 400.0)
        with patch('lib.core.audio_meter.get_audio_meter', return_value=meter):
            forget_speaker_band('qt', 11)

        self.assertEqual(meter.unregistered, ['qt:11'])
        self.assertEqual(get_speaker_band('qt', 11), default_band())

    def test_forget_of_unknown_instance_does_not_touch_the_meter(self):
        meter = _RecordingMeter()
        with patch('lib.core.audio_meter.get_audio_meter', return_value=meter):
            forget_speaker_band('qt', 99)

        self.assertEqual(meter.unregistered, [])


class _RecordingMeter:
    """只记录登记/注销/取值的 AudioMeter 替身。"""

    def __init__(self, default: float = 0.25, keyed: float = 0.5) -> None:
        self.default = float(default)
        self.keyed = float(keyed)
        self.registered: dict[str, tuple[float, float]] = {}
        self.unregistered: list[str] = []
        self.reads: list[str | None] = []

    def register_band(self, key, low_hz, high_hz) -> None:
        self.registered[str(key)] = (float(low_hz), float(high_hz))

    def unregister_band(self, key) -> None:
        self.unregistered.append(str(key))
        self.registered.pop(str(key), None)

    def get_frequency_intensity(self, key=None):
        self.reads.append(key)
        return self.default if key is None else self.keyed


class SpeakerBandMeterRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_speaker_bands()
        self.addCleanup(clear_speaker_bands)
        self.meter = _RecordingMeter()
        patcher = patch('lib.core.audio_meter.get_audio_meter', return_value=self.meter)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_untouched_speaker_reads_the_default_band(self):
        self.assertEqual(speaker_response_intensity('qt', 5), 0.25)
        self.assertEqual(self.meter.reads, [None])
        self.assertEqual(self.meter.registered, {})

    def test_custom_band_is_registered_then_read_by_key(self):
        set_speaker_band('qt', 5, 90.0, 400.0)
        self.assertEqual(speaker_response_intensity('qt', 5), 0.5)
        self.assertEqual(self.meter.reads, ['qt:5'])
        self.assertEqual(self.meter.registered, {'qt:5': clamp_band(90.0, 400.0)})

    def test_instance_without_identity_still_reads_the_default_band(self):
        self.assertEqual(speaker_response_intensity('', 0), 0.25)
        self.assertEqual(self.meter.reads, [None])

    def test_reset_back_to_default_stops_using_the_registered_key(self):
        set_speaker_band('qt', 5, 90.0, 400.0)
        speaker_response_intensity('qt', 5)
        set_speaker_band('qt', 5, *default_band())
        self.assertEqual(speaker_response_intensity('qt', 5), 0.25)
        self.assertEqual(self.meter.reads, ['qt:5', None])


class _FakeSpectrum:
    def __init__(self, *, level_db: float | None = -25.0) -> None:
        self.level_db = level_db
        self.level_by_index: dict[int, float | None] = {}
        self.bands = ()
        self.cleaned = False

    def set_bands(self, bands) -> None:
        self.bands = tuple(bands)

    def get_level_db(self, index: int = 0):
        if int(index) in self.level_by_index:
            return self.level_by_index[int(index)]
        return self.level_db

    def status(self):
        return {'ready': True, 'level_db': self.level_db}

    def cleanup(self):
        self.cleaned = True


class MeterBandRegistryTests(unittest.TestCase):
    def _meter(self, spectrum):
        with patch.object(AudioMeter, '_init_meter', lambda self: None), patch(
            'lib.core.audio_spectrum.AudioSpectrumAnalyzer',
            lambda **kwargs: spectrum,
        ):
            return AudioMeter()

    def setUp(self) -> None:
        self.spectrum = _FakeSpectrum()
        self.meter = self._meter(self.spectrum)
        self.addCleanup(self.meter.cleanup)

    def test_default_band_is_always_the_first_analyzed_band(self):
        self.assertEqual(self.spectrum.bands, ())
        self.meter.register_band('qt:1', 80.0, 400.0)
        self.assertEqual(
            self.spectrum.bands, (self.meter._default_band(), (80.0, 400.0)),
        )
        self.meter.register_band('qt:2', 30.0, 60.0)
        self.assertEqual(
            self.spectrum.bands,
            (self.meter._default_band(), (80.0, 400.0), (30.0, 60.0)),
        )

    def test_registering_the_same_range_twice_does_not_touch_the_analyzer(self):
        self.meter.register_band('qt:1', 80.0, 400.0)
        self.spectrum.bands = 'unchanged'
        self.meter.register_band('qt:1', 80.0, 400.0)
        self.assertEqual(self.spectrum.bands, 'unchanged')

    def test_update_keeps_the_slot_and_replaces_the_range(self):
        self.meter.register_band('qt:1', 80.0, 400.0)
        self.meter.register_band('qt:1', 100.0, 500.0)
        self.assertEqual(
            self.spectrum.bands, (self.meter._default_band(), (100.0, 500.0)),
        )

    def test_unregister_removes_the_band(self):
        self.meter.register_band('qt:1', 80.0, 400.0)
        self.meter.unregister_band('qt:1')
        self.assertEqual(self.spectrum.bands, (self.meter._default_band(),))
        self.assertEqual(self.meter.band_status()['keys'], ())

    def test_key_lookup_falls_back_to_the_default_band(self):
        self.meter.register_band('qt:1', 80.0, 400.0)
        self.assertEqual(self.meter._band_index('qt:1'), 1)
        self.assertEqual(self.meter._band_index('qt:404'), 0)
        self.assertEqual(self.meter._band_index(None), 0)
        self.assertEqual(self.meter._band_index(''), 0)

    def test_intensity_uses_the_registered_index(self):
        self.spectrum.level_db = -50.0
        self.spectrum.level_by_index[1] = 0.0
        self.meter.register_band('qt:1', 80.0, 400.0)

        self.assertAlmostEqual(self.meter.get_frequency_intensity(), 0.0, places=6)
        self.assertAlmostEqual(self.meter.get_frequency_intensity('qt:1'), 1.0, places=6)

    def test_registry_is_capped_and_drops_the_oldest(self):
        limit = speaker_band_band_cap()
        for index in range(limit + 2):
            self.meter.register_band(f'qt:{index}', 80.0, 400.0 + index)
        keys = self.meter.band_status()['keys']
        self.assertEqual(len(keys), limit)
        self.assertNotIn('qt:0', keys)
        self.assertEqual(len(self.spectrum.bands), limit + 1)


def speaker_band_band_cap() -> int:
    from lib.core import audio_meter

    return int(audio_meter._MAX_REGISTERED_BANDS)


if __name__ == '__main__':
    unittest.main()
