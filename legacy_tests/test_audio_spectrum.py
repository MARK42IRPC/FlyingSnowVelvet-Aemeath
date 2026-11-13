from __future__ import annotations

import gc
import sys
import threading
import types
import unittest
from unittest.mock import patch

import numpy as np

from lib.core import audio_meter as audio_meter_module
from lib.core import audio_spectrum as audio_spectrum_module
from lib.core.audio_meter import AudioMeter
from lib.core.audio_spectrum import (
    AudioSpectrumAnalyzer,
    band_level_db,
    bands_level_db,
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


class BandsLevelTests(unittest.TestCase):
    """一次 FFT 求多个频段：每个频段口径与单频段入口一致。"""

    def _tone(self, *frequencies: float, sample_rate: float = 32000.0, size: int = 4096):
        time = np.arange(size, dtype=np.float32) / float(sample_rate)
        signal = sum(np.sin(2.0 * np.pi * hz * time) for hz in frequencies)
        return (signal / max(1, len(frequencies))).astype(np.float32)

    def test_each_band_reports_its_own_energy(self):
        samples = self._tone(100.0, 1000.0)
        levels = bands_level_db(
            samples,
            32000.0,
            ((80.0, 120.0), (900.0, 1100.0), (3000.0, 4000.0)),
        )

        self.assertEqual(len(levels), 3)
        self.assertGreater(levels[0], -30.0)
        self.assertAlmostEqual(levels[0], levels[1], delta=1.0)
        self.assertLess(levels[2], levels[0] - 20.0)

    def test_single_band_entry_matches_the_multi_band_result(self):
        samples = self._tone(60.0)
        single = band_level_db(samples, 32000.0, 50.0, 70.0)
        multiple = bands_level_db(samples, 32000.0, ((50.0, 70.0),))[0]

        self.assertAlmostEqual(single, multiple, places=9)

    def test_reversed_band_bounds_behave_like_the_single_band_entry(self):
        samples = self._tone(60.0)
        self.assertAlmostEqual(
            bands_level_db(samples, 32000.0, ((70.0, 50.0),))[0],
            band_level_db(samples, 32000.0, 70.0, 50.0),
            places=9,
        )

    def test_empty_band_list_returns_no_levels(self):
        self.assertEqual(bands_level_db([], 32000.0, ()), ())

    def test_missing_frames_fall_back_to_silence_for_every_band(self):
        silence = band_level_db([], 32000.0, 50.0, 70.0)
        levels = bands_level_db([], 32000.0, ((50.0, 70.0), (100.0, 200.0)))
        self.assertEqual(levels, (silence, silence))


class _FakeSpectrum:
    def __init__(self, *, freq_min, freq_max, level_db=None):
        self.freq_min = freq_min
        self.freq_max = freq_max
        self.level_db = level_db
        self.cleaned = False

    def get_level_db(self, index: int = 0):
        return self.level_db
    def set_bands(self, bands):
        self.bands = tuple(bands)

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


class _FakeComObject:
    """COM 对象替身：只记录引用计数，释放由指针替身负责。"""

    def __init__(self, name):
        self.name = name
        self.refcount = 0

    def add_ref(self):
        self.refcount += 1

    def release(self):
        self.refcount -= 1


class _FakeInterfacePointer:
    """comtypes 接口指针替身：构造即持有一次引用，QueryInterface 返回自带引用的新指针。

    真实环境里 `ctypes.cast(client, POINTER(IFace))` 不增加引用计数，被 cast 的临时
    指针与结果指针共享同一个 COM 引用；临时指针先析构就会把仍在使用的接口提前释放，
    进程退出期表现为 access violation 或 “COM method call without VTable”。这里用引用
    计数把这个约束固化下来：临时指针析构后接口仍必须有人持有。
    """

    def __init__(self, target, iid=None):
        self.target = target
        self.iid = iid
        target.add_ref()

    def __del__(self):
        target = self.target
        self.target = None
        if target is not None:
            target.release()

    def QueryInterface(self, interface):
        return _FakeInterfacePointer(self.target, getattr(interface, "_iid_", None))

    def __getattr__(self, name):
        if name == "target":  # 属性还没建立时不要递归
            raise AttributeError(name)
        return getattr(self.target, name)


class _FakeIAudioClient:
    _iid_ = "IID_IAudioClient"


class _FakeIAudioMeterInformation:
    _iid_ = "IID_IAudioMeterInformation"


class _FakeAudioClientObject(_FakeComObject):
    """IAudioClient 替身：回环流打开路径会用到的成员。"""

    def __init__(self):
        super().__init__("IAudioClient")
        self.mix_format = types.SimpleNamespace(
            wFormatTag=0x0003,
            nChannels=2,
            nSamplesPerSec=48000,
            wBitsPerSample=32,
            nBlockAlign=8,
        )
        self.initialize_args = None
        self.started = False
        self.stopped = False
        self.service_object = _FakeComObject("IAudioCaptureClient")

    def GetMixFormat(self):
        return types.SimpleNamespace(contents=self.mix_format)

    def Initialize(self, *args):
        self.initialize_args = args

    def GetService(self, iid):
        return _FakeInterfacePointer(self.service_object, iid)

    def Start(self):
        self.started = True

    def Stop(self):
        self.stopped = True


class _FakeAudioMeterObject(_FakeComObject):
    def __init__(self):
        super().__init__("IAudioMeterInformation")
        self.peak = 0.25

    def GetPeakValue(self):
        return self.peak


class _FakeAudioDevice:
    """IMMDevice 替身：按 iid 分发 Activate 结果。"""

    def __init__(self):
        self.client_object = _FakeAudioClientObject()
        self.meter_object = _FakeAudioMeterObject()
        self.activate_count = 0

    def Activate(self, iid, clsctx, params):
        if iid == _FakeIAudioMeterInformation._iid_:
            target = self.meter_object
        else:
            target = self.client_object
        self.activate_count += 1
        return _FakeInterfacePointer(target, iid)


def _fake_audio_modules(device):
    """注入 pycaw / comtypes 替身，让 COM 路径在没有音频设备的机器上也能跑。"""
    pycaw = types.ModuleType("pycaw")
    pycaw_pycaw = types.ModuleType("pycaw.pycaw")
    pycaw_pycaw.AudioUtilities = types.SimpleNamespace(
        GetSpeakers=lambda: types.SimpleNamespace(_dev=device)
    )
    pycaw_pycaw.IAudioMeterInformation = _FakeIAudioMeterInformation
    pycaw_api = types.ModuleType("pycaw.api")
    pycaw_audioclient = types.ModuleType("pycaw.api.audioclient")
    pycaw_audioclient.IAudioClient = _FakeIAudioClient
    pycaw_api.audioclient = pycaw_audioclient

    class _FakeIUnknown:
        """只是让 `_declare_capture_client` 里的类定义能执行。"""

    comtypes = types.ModuleType("comtypes")
    comtypes.CLSCTX_ALL = 23
    comtypes.IUnknown = _FakeIUnknown
    comtypes.GUID = lambda value: value
    comtypes.COMMETHOD = lambda *args, **kwargs: None

    return {
        "pycaw": pycaw,
        "pycaw.pycaw": pycaw_pycaw,
        "pycaw.api": pycaw_api,
        "pycaw.api.audioclient": pycaw_audioclient,
        "comtypes": comtypes,
    }


def _bare_analyzer():
    """只带 `_open_capture` 所需字段的分析器（不启动采样线程）。"""
    analyzer = object.__new__(AudioSpectrumAnalyzer)
    analyzer._freq_min = 60.0
    analyzer._freq_max = 250.0
    analyzer._window_size = 4096
    analyzer._lock = threading.Lock()
    analyzer._sample_rate = 0
    analyzer._channels = 0
    analyzer._tag = 0
    analyzer._bits = 0
    analyzer._block_align = 0
    return analyzer


class CapturePointerOwnershipTests(unittest.TestCase):
    """回环流接口各自持有一次引用（退出期 comtypes 崩溃的回归用例）。"""

    def setUp(self):
        saved = audio_spectrum_module._CAPTURE_CLIENT_CLASS
        audio_spectrum_module._CAPTURE_CLIENT_CLASS = None
        self.addCleanup(
            setattr, audio_spectrum_module, "_CAPTURE_CLIENT_CLASS", saved
        )
        self.device = _FakeAudioDevice()
        patcher = patch.dict(sys.modules, _fake_audio_modules(self.device))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_open_capture_keeps_one_reference_per_interface(self):
        analyzer = _bare_analyzer()

        capture, client, window, error = analyzer._open_capture()

        self.assertEqual(error, "")
        self.assertEqual(analyzer._sample_rate, 48000)
        self.assertEqual(analyzer._channels, 2)
        self.assertEqual(analyzer._block_align, 8)
        self.assertEqual(window.shape, (4096,))
        self.assertTrue(self.device.client_object.started)
        # Activate / GetService 的临时指针已经析构，引用只由 client / capture 持有。
        self.assertEqual(self.device.client_object.refcount, 1)
        self.assertEqual(self.device.client_object.service_object.refcount, 1)

        del capture, client
        gc.collect()

        self.assertEqual(self.device.client_object.refcount, 0)
        self.assertEqual(self.device.client_object.service_object.refcount, 0)


class MeterPointerOwnershipTests(unittest.TestCase):
    """IAudioMeterInformation 同样不能被临时指针抢走引用。"""

    def setUp(self):
        self.device = _FakeAudioDevice()
        patcher = patch.dict(sys.modules, _fake_audio_modules(self.device))
        patcher.start()
        self.addCleanup(patcher.stop)
        spectrum = _FakeSpectrum(freq_min=60.0, freq_max=250.0)
        spectrum_patcher = patch(
            "lib.core.audio_spectrum.AudioSpectrumAnalyzer", lambda **kwargs: spectrum
        )
        spectrum_patcher.start()
        self.addCleanup(spectrum_patcher.stop)

    def test_cleanup_releases_meter_pointer(self):
        meter = AudioMeter()
        self.addCleanup(meter.cleanup)

        self.assertIsNotNone(meter._meter)
        self.assertEqual(self.device.meter_object.refcount, 1)

        meter.cleanup()

        self.assertIsNone(meter._meter)
        self.assertEqual(self.device.meter_object.refcount, 0)


if __name__ == "__main__":
    unittest.main()
