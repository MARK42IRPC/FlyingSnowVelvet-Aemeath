import unittest
from unittest.mock import patch

from lib.script.cloudmusic._mixin_events import _EventsMixin
from lib.script.qqmusic.qqmisic import QQmisic


class QQMusicMetadataTests(unittest.TestCase):
    @staticmethod
    def _client() -> QQmisic:
        client = object.__new__(QQmisic)
        client._song_cache = {}
        return client

    def test_legacy_playlist_songname_is_used_as_title(self):
        client = self._client()

        song = client._normalize_song({
            "songmid": "001iHkPP0gy29f",
            "strMediaMid": "MEDIA001",
            "songname": "未竟之旅",
            "singer": [{"name": "鸣潮先约电台"}],
            "interval": 233,
        })

        self.assertIsNotNone(song)
        self.assertEqual(song["title"], "未竟之旅")
        self.assertEqual(song["artist"], "鸣潮先约电台")
        self.assertEqual(song["media_mid"], "MEDIA001")
        self.assertEqual(song["duration_ms"], 233000)

    def test_identifier_title_does_not_poison_detail_cache(self):
        client = self._client()

        song = client._normalize_song({
            "songmid": "002RcLNb0nJZwT",
            "singer": [{"name": "鸣潮先约电台"}],
        })

        self.assertEqual(song["title"], "002RcLNb0nJZwT")
        self.assertNotIn("002RcLNb0nJZwT", client._song_cache)

    def test_liked_queue_hydrates_identifier_title_from_detail(self):
        class Client:
            def get_liked_tracks(self, limit):
                return [{
                    "mid": "002RcLNb0nJZwT",
                    "title": "002RcLNb0nJZwT",
                    "artist": "鸣潮先约电台",
                    "duration_ms": 151000,
                }]

            def get_last_liked_meta(self):
                return {"ok": True, "reason": "test"}

            def get_song_detail(self, mid):
                self.requested_mid = mid
                return {
                    "mid": mid,
                    "title": "Against the Tide (逆潮) (Remix Ver.)",
                    "artist": "鸣潮先约电台",
                    "duration_ms": 151000,
                }

        client = Client()
        with patch(
            "lib.script.cloudmusic._mixin_events.get_qqmusic_provider_client",
            return_value=client,
        ):
            items = _EventsMixin()._fetch_qq_liked_tracks(limit=1)

        self.assertEqual(client.requested_mid, "002RcLNb0nJZwT")
        self.assertEqual(items, [(
            "qq:002RcLNb0nJZwT",
            "02:31 Against the Tide (逆潮) (Remix Ver.) - 鸣潮先约电台",
        )])


if __name__ == "__main__":
    unittest.main()
