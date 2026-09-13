from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from lib.core import audio_meter as audio_meter_module
from lib.core.audio_meter import AudioMeter
from lib.core.audio_spectrum import (
    band_level_db,
    level_to_intensity,
)

_SAMPLE_RATE = 48000
_WINDOW = 4096


def _tone(frequency: float, amplitude: float = 1.0, size: int = _WINDOW) -> np.ndarray:
    time = np.arange(size, dtype=np.float32) / _SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * frequency * time)).astype(np.float32)


class BandLevelTests(unittest.TestCase):
    def test_silence_is_reported_as_floor(self):
        self.assertEqual(band_level_db(np.zeros(_WINDOW), _SAMPLE_RATE, 60, 250), -120.0)

    def test_in_band_tone_is_loud_and_out_of_band_tone_is_quiet(self):
        in_band = band_level_db(_tone(110.0), _SAMPLE_RATE, 60, 250)
        out_of_band = band_level_db(_tone(4000.0), _SAMPLE_RATE, 60, 250)

        self.assertGreater(in_band, -6.0)
        self.assertLess(out_of_band, -60.0)

    def test_level_tracks_amplitude_by_20db_per_decade(self):
        loud = band_level_db(_tone(110.0, 0.5), _SAMPLE_RATE, 60, 250)
        quiet = band_level_db(_tone(110.0, 0.05), _SAMPLE_RATE, 60, 250)

        self.assertAlmostEqual(loud - quiet, 20.0, delta=1.5)

    def test_empty_and_invalid_inputs_degrade_to_floor(self):
        self.assertEqual(band_level_db(np.zeros(0), _SAMPLE_RATE, 60, 250), -120.0)
        self.assertEqual(band_level_db(_tone(110.0), 0, 60, 250), -120.0)
        self.assertEqual(band_level_db(_tone(110.0), _SAMPLE_RATE, 900, 100), -120.0)

    def test_window_shorter_than_window_size_is_padded(self):
        level = band_level_db(_tone(110.0, size=512), _SAMPLE_RATE, 60, 250)

        self.assertGreater(level, -35.0)


class LevelToIntensityTests(unittest.TestCase):
    def test_maps_floor_to_zero_and_ceiling_to_one(self):
        self.assertEqual(level_to_intensity(-50.0, -50.0, 0.0), 0.0)
        self.assertEqual(level_to_intensity(0.0, -50.0, 0.0), 1.0)
        self.assertAlmostEqual(level_to_intensity(-25.0, -50.0, 0.0), 0.5)

    def test_clamps_out_of_range_values(self):
        self.assertEqual(level_to_intensity(-120.0, -50.0, 0.0), 0.0)
        self.assertEqual(level_to_intensity(12.0, -50.0, 0.0), 1.0)

    def test_degenerate_range_returns_zero(self):
        self.assertEqual(level_to_intensity(-10.0, 0.0, 0.0), 0.0)
        self.assertEqual(level_to_intensity(-10.0, 0.0, -20.0), 0.0)


class _FakeSpectrum:
    def __init__(self, *, freq_min, freq_max, level_db=None):
        self.freq_min = freq_min
        self.freq_max = freq_max
        self.level_db = level_db
        self.cleaned = False

    def get_level_db(self):
        return self.level_db

    def status(self):
        return {"ready": True, "level_db": self.level_db}

    def cleanup(self):
        self.cleaned = True


class AudioMeterFrequencyTests(unittest.TestCase):
    def setUp(self):
        self._config = patch.dict(
            "config.config_music.SPEAKER_AUDIO",
            {
                "freq_min": 60.0,
                "freq_max": 250.0,
                "level_floor_db": -50.0,
                "level_ceil_db": 0.0,
            },
            clear=False,
        )
        self._config.start()
        self.addCleanup(self._config.stop)

    def _meter(self, spectrum):
        with patch.object(AudioMeter, "_init_meter", lambda self: None), patch(
            "lib.core.audio_spectrum.AudioSpectrumAnalyzer",
            lambda **kwargs: spectrum,
        ):
            return AudioMeter()

    def test_band_level_drives_intensity_with_configured_mapping(self):
        spectrum = _FakeSpectrum(freq_min=60.0, freq_max=250.0, level_db=-25.0)
        meter = self._meter(spectrum)

        self.assertEqual(meter.get_frequency_source(), "spectrum")
        self.assertAlmostEqual(meter.get_frequency_intensity(), 0.5, places=4)
        self.assertEqual(meter.spectrum_status()["level_db"], -25.0)
        meter.cleanup()
        self.assertTrue(spectrum.cleaned)

    def test_peak_path_is_used_when_band_has_no_data(self):
        spectrum = _FakeSpectrum(freq_min=60.0, freq_max=250.0, level_db=None)
        meter = self._meter(spectrum)
        meter._peak = 0.25

        self.assertEqual(meter.get_frequency_source(), "peak")
        self.assertAlmostEqual(meter.get_frequency_intensity(), 0.5, places=4)

        meter._peak = 0.0
        self.assertEqual(meter.get_frequency_source(), "none")
        self.assertEqual(meter.get_frequency_intensity(), 0.0)

    def test_missing_spectrum_falls_back_to_peak(self):
        meter = self._meter(None)
        self.assertIsNone(meter._spectrum)
        meter._peak = 0.81

        self.assertEqual(meter.get_frequency_source(), "peak")
        self.assertAlmostEqual(meter.get_frequency_intensity(), 0.9, places=4)

    def test_spectrum_config_defaults_are_used_when_config_is_unavailable(self):
        with patch.dict("sys.modules", {"config.config_music": None}):
            config = audio_meter_module._spectrum_config()

        self.assertEqual(config["freq_min"], 60.0)
        self.assertEqual(config["freq_max"], 250.0)
        self.assertEqual(config["level_floor_db"], -50.0)
        self.assertEqual(config["level_ceil_db"], 0.0)


if __name__ == "__main__":
    unittest.main()
