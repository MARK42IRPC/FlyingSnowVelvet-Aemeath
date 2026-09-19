"""Provider 共享助手的行为契约。

这些函数从 netease / qq / kugou provider 与 cloudmusic、音响搜索面板里抽出来，
抽之前各处已经出现细微分叉（例如 `_mixin_events` 多一个 None 早退）。这里的用例同时
锁住“行为等价”与“各平台候选键仍需生效”，避免以后又有人抄一份带差异的副本回去。
"""

import unittest

from lib.script.music.providers._shared import (
    extract_first_artist,
    first_artist_from_list,
    format_duration_text,
    looks_like_single_slash_name,
    split_first_artist_text,
)


class FormatDurationTextTests(unittest.TestCase):
    def test_none_is_zero(self):
        self.assertEqual(format_duration_text(None), "00:00")

    def test_milliseconds_are_truncated_to_seconds(self):
        self.assertEqual(format_duration_text(233000), "03:53")
        self.assertEqual(format_duration_text(59999), "00:59")

    def test_minute_second_text_passes_through(self):
        self.assertEqual(format_duration_text("04:05"), "04:05")
        self.assertEqual(format_duration_text(" 12:34 "), "12:34")

    def test_numeric_string_is_treated_as_milliseconds(self):
        self.assertEqual(format_duration_text("151000"), "02:31")

    def test_dict_reads_the_first_known_duration_key(self):
        self.assertEqual(format_duration_text({"duration_ms": 151000}), "02:31")
        self.assertEqual(format_duration_text({"duration": 90000}), "01:30")
        self.assertEqual(format_duration_text({"dt": 5000}), "00:05")
        self.assertEqual(format_duration_text({"ms": 1000}), "00:01")
        self.assertEqual(format_duration_text({}), "00:00")

    def test_negative_and_garbage_do_not_raise(self):
        self.assertEqual(format_duration_text(-5000), "00:00")
        self.assertEqual(format_duration_text("abc"), "00:00")
        self.assertEqual(format_duration_text(object()), "00:00")


class SplitFirstArtistTests(unittest.TestCase):
    def test_common_separators_split_to_the_first_name(self):
        for text in ("爱弥斯、耶比", "爱弥斯,耶比", "爱弥斯 & 耶比", "爱弥斯 feat. 耶比"):
            with self.subTest(text=text):
                self.assertEqual(split_first_artist_text(text), "爱弥斯")

    def test_single_slash_keeps_the_whole_name(self):
        # `MC/DC` 这类是全大写短名，斜杠属于名字本身
        self.assertTrue(looks_like_single_slash_name("MC", "DC"))
        self.assertEqual(split_first_artist_text("MC/DC"), "MC/DC")

    def test_slash_between_two_full_names_takes_the_left_one(self):
        self.assertEqual(split_first_artist_text("爱弥斯/耶比"), "爱弥斯")

    def test_empty_input_returns_empty_string(self):
        self.assertEqual(split_first_artist_text(None), "")
        self.assertEqual(split_first_artist_text("   "), "")


class FirstArtistFromListTests(unittest.TestCase):
    def test_missing_list_uses_the_unknown_label(self):
        self.assertEqual(first_artist_from_list([]), "未知作者")
        self.assertEqual(first_artist_from_list([], unknown="Unknown Artist"), "Unknown Artist")

    def test_dict_and_plain_entries_both_work(self):
        self.assertEqual(first_artist_from_list([{"name": "爱弥斯"}]), "爱弥斯")
        self.assertEqual(first_artist_from_list(["耶比"]), "耶比")

    def test_blank_first_entry_falls_back_to_unknown(self):
        self.assertEqual(first_artist_from_list([{"name": "  "}]), "未知作者")
        self.assertEqual(first_artist_from_list(["   "]), "未知作者")


class ExtractFirstArtistTests(unittest.TestCase):
    def test_per_provider_key_order_is_honoured(self):
        # 酷狗原名 `SingerName` 与平铺层 `singer` 同时存在时，谁先被查到就归谁，
        # 所以两个平台必须传各自的键序，不能共用一份写死的顺序。
        song = {
            "raw": {"SingerName": "酷狗原名", "singer": "网易平铺名"},
            "singer": "网易平铺名",
        }
        kugou_style = extract_first_artist(
            song,
            raw_keys=("SingerName", "singer"),
            song_keys=("authors", "artist", "singer"),
        )
        netease_style = extract_first_artist(
            song,
            raw_keys=("singer", "singers", "artists", "artist"),
            song_keys=("singer", "singers", "artists", "artist"),
        )
        self.assertEqual(kugou_style, "酷狗原名")
        self.assertEqual(netease_style, "网易平铺名")

    def test_flat_song_keys_are_used_when_raw_is_absent(self):
        song = {"authors": [{"name": "爱弥斯"}]}
        self.assertEqual(
            extract_first_artist(song, raw_keys=("SingerName",), song_keys=("authors",)),
            "爱弥斯",
        )

    def test_no_candidate_returns_unknown(self):
        self.assertEqual(
            extract_first_artist({"raw": {}, "artist": ""}, raw_keys=("artist",), song_keys=("artist",)),
            "未知作者",
        )

    def test_nested_list_of_dicts_is_unwrapped(self):
        song = {"artists": [{"name": "爱弥斯"}, {"name": "耶比"}]}
        self.assertEqual(
            extract_first_artist(song, raw_keys=("artists",), song_keys=("artists",)),
            "爱弥斯",
        )


if __name__ == "__main__":
    unittest.main()
