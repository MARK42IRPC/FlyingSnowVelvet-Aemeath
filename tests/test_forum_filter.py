"""留言墙本地内容过滤与设备标识的判定边界。"""

from __future__ import annotations

import unittest

from lib.core.forum import (
    FORUM_MAX_NICKNAME,
    build_content_with_device_tag,
    device_tag_of,
    normalize_nickname,
    parse_feed,
    strip_device_tag,
)
from lib.core.forum_filter import (
    FORUM_REASON_BANNED_WORD,
    FORUM_REASON_LINK,
    FORUM_REASON_LONG_NUMBER,
    check_content,
    find_banned_word,
    find_link,
    find_long_number,
    is_allowed,
    normalize_for_matching,
)


class LinkRuleTests(unittest.TestCase):
    def test_http_and_www_and_bare_domains_are_rejected(self):
        for text in (
            "看看 https://example.com",
            "http://a.cn",
            "www.baidu.com",
            "my site example.com",
            "点这里 [url] 领取",
            "<a href='x'>点我</a>",
        ):
            with self.subTest(text=text):
                violation = check_content(text)
                self.assertIsNotNone(violation, text)
                self.assertEqual(violation.reason, FORUM_REASON_LINK)

    def test_plain_sentences_survive(self):
        for text in ("今天天气不错", "我喜欢雪绒", "打个中文句号。", "1.5 倍速"):
            with self.subTest(text=text):
                self.assertIsNone(find_link(text), text)


class LongNumberRuleTests(unittest.TestCase):
    def test_six_or_more_digits_are_rejected(self):
        for text in ("123456", "电话13800138000", "qq 123 456 789", "编号:123-456"):
            with self.subTest(text=text):
                violation = check_content(text)
                self.assertIsNotNone(violation, text)
                self.assertEqual(violation.reason, FORUM_REASON_LONG_NUMBER)

    def test_shorter_numbers_are_allowed(self):
        for text in ("12345", "2026 年了", "价格 12.5 元", "第 3 条"):
            with self.subTest(text=text):
                self.assertIsNone(find_long_number(text), text)


class BannedWordRuleTests(unittest.TestCase):
    def test_banned_words_are_matched_across_separators(self):
        for text in ("加微信", "加*微*信", "加 微 信", "加·微·信", "加　微　信"):
            with self.subTest(text=text):
                self.assertIsNotNone(find_banned_word(text), text)

    def test_matching_folds_width_and_case(self):
        self.assertEqual(normalize_for_matching("加 微 信"), "加微信")

    def test_long_numbers_win_over_words(self):
        """判定顺序固定：链接 → 长数字 → 违规词。"""
        violation = check_content("加微信 123456")
        self.assertEqual(violation.reason, FORUM_REASON_LONG_NUMBER)

    def test_links_win_over_everything(self):
        violation = check_content("加微信 https://a.com 123456")
        self.assertEqual(violation.reason, FORUM_REASON_LINK)

    def test_clean_content_passes(self):
        self.assertTrue(is_allowed("今天雪下得真好看，注意保暖呀。"))
        self.assertIsNone(check_content("   "))


class DeviceTagTests(unittest.TestCase):
    def test_tag_is_appended_once_and_stripped_for_display(self):
        content = build_content_with_device_tag("你好", tag="sha-123abcDE")
        self.assertEqual(content, "你好[sha-123abcDE]")
        self.assertEqual(strip_device_tag(content), "你好")
        self.assertEqual(device_tag_of(content), "sha-123abcDE")

    def test_existing_tag_is_not_doubled(self):
        content = "你好[sha-123abcDE]"
        self.assertEqual(build_content_with_device_tag(content, tag="sha-ffffeeee"), content)

    def test_feed_parsing_splits_content_and_tag(self):
        messages, _total = parse_feed({
            "ok": True,
            "total": 2,
            "messages": [
                {"id": 9, "nickname": "小明", "content": "一起去玩[sha-123abcDE]",
                 "accent": "pink", "created_at": 1},
                {"id": 8, "nickname": "匿名", "content": "纯匿名留言",
                 "accent": "snow", "created_at": 2},
            ],
        })
        self.assertEqual(messages[0].content, "一起去玩")
        self.assertEqual(messages[0].device_tag, "sha-123abcDE")
        self.assertEqual(messages[1].device_tag, "")

    def test_tag_only_content_is_dropped(self):
        messages, _total = parse_feed({
            "ok": True, "total": 1,
            "messages": [{"id": 1, "content": "[sha-123abcDE]", "accent": "snow"}],
        })
        self.assertEqual(messages, ())


class NicknameLimitTests(unittest.TestCase):
    def test_nickname_is_truncated_to_the_cap(self):
        self.assertEqual(len(normalize_nickname("一" * 40)), FORUM_MAX_NICKNAME)
        self.assertEqual(normalize_nickname("  小明  "), "小明")

    def test_blank_nickname_falls_back_to_anonymous(self):
        self.assertEqual(normalize_nickname(""), "匿名")
        self.assertEqual(normalize_nickname(None), "匿名")


if __name__ == "__main__":
    unittest.main()
