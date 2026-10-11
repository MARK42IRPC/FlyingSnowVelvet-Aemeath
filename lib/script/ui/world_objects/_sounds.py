"""世界对象声音工厂（产品侧唯一 import `lib.script.voice.*` 的落点）。

世界对象宿主住在档位 D（`lib/core/render/backends/qt/widgets/`），不得 import 产品包。这里
按契约名分派到具体的 Sound 类，并缓存实例（同一声音名重复取用时复用，与原实现
「每个世界对象构造一个 Sound 实例」的语义一致）。
"""

from __future__ import annotations


class _SilentSound:
    """未知声音名时的静音实现：任何方法都安全无副作用。"""

    def play(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


def make_sound(name: str):
    """按名字构造一个世界对象声音；未知名字退化为静音对象。"""
    key = str(name or "").strip()
    if key == "gear":
        from lib.script.voice.gear import GearSound

        return GearSound()
    if key == "ring":
        from lib.script.voice.ring import RingSound

        return RingSound()
    if key == "chrack":
        from lib.script.voice.chrack import ChrackSound

        return ChrackSound()
    if key == "snow":
        from lib.script.voice.snow import SnowSound

        return SnowSound()
    if key == "snowball":
        from lib.script.voice.snowball_sound import SnowballSound

        return SnowballSound()
    if key == "sofa":
        from lib.script.voice.sofa import SofaSound

        return SofaSound()
    return _SilentSound()


__all__ = ["make_sound"]
