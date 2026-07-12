import unittest

from lib.script.music.provider import MusicProvider
from lib.script.music.router import SourceRouter
from lib.script.music.types import MusicTrack


class _Provider(MusicProvider):
    def __init__(self, name: str, tracks: list[MusicTrack]):
        self.provider_name = name
        self.provider_label = name
        self._tracks = tracks

    def search(self, keyword: str, mode: str = "song", limit: int = 25) -> list[MusicTrack]:
        return self._tracks[:limit]


class MusicSourceRoutingTests(unittest.TestCase):
    def test_search_without_fallback_uses_only_primary_provider(self):
        router = SourceRouter()
        providers = {
            "netease": _Provider("netease", []),
            "qq": _Provider("qq", []),
            "kugou": _Provider("kugou", [
                MusicTrack(
                    provider="kugou",
                    track_id="kugou:HASH",
                    title="kugou song",
                    artist="artist",
                )
            ]),
        }

        results = router.search(
            providers=providers,
            primary_provider="netease",
            keyword="test",
            fallback_enabled=False,
            fallback_order=("kugou", "qq"),
        )

        self.assertEqual(results, [])
        self.assertEqual(set(router.provider_stats().keys()), {"netease"})

    def test_search_with_fallback_can_use_kugou_after_primary(self):
        router = SourceRouter()
        providers = {
            "netease": _Provider("netease", []),
            "qq": _Provider("qq", []),
            "kugou": _Provider("kugou", [
                MusicTrack(
                    provider="kugou",
                    track_id="kugou:HASH",
                    title="kugou song",
                    artist="artist",
                )
            ]),
        }

        results = router.search(
            providers=providers,
            primary_provider="netease",
            keyword="test",
            fallback_enabled=True,
            fallback_order=("kugou", "qq"),
        )

        self.assertEqual([track.provider for track in results], ["kugou"])


if __name__ == "__main__":
    unittest.main()
