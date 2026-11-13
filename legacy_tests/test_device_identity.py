"""设备标识：格式、稳定性与不泄漏原文。"""

from __future__ import annotations

import re
import unittest
from unittest.mock import patch

from lib.core.device_identity import (
    DEVICE_TAG_HEX_LENGTH,
    DEVICE_TAG_PREFIX,
    get_device_tag,
    reset_device_tag_cache,
)

#: 与留言墙尾注 `[sha-123abcDE]` 同一格式：前缀 + 八位十六进制。
_TAG_RE = re.compile(r"^sha-[0-9a-fA-F]{8}$")


class DeviceTagTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_device_tag_cache()

    def tearDown(self) -> None:
        reset_device_tag_cache()

    def test_tag_matches_the_message_wall_format(self):
        tag = get_device_tag()
        self.assertRegex(tag, _TAG_RE)
        self.assertEqual(len(tag), len(DEVICE_TAG_PREFIX) + DEVICE_TAG_HEX_LENGTH)

    def test_tag_is_stable_within_a_process(self):
        self.assertEqual(get_device_tag(), get_device_tag())

    def test_same_seed_yields_the_same_tag(self):
        with patch(
            "lib.core.device_identity._windows_machine_guid",
            return_value="fixed-machine-guid",
        ):
            first = get_device_tag()
            reset_device_tag_cache()
            second = get_device_tag()
        self.assertEqual(first, second)

    def test_different_machines_get_different_tags(self):
        with patch(
            "lib.core.device_identity._windows_machine_guid",
            return_value="machine-a",
        ):
            reset_device_tag_cache()
            first = get_device_tag()
        with patch(
            "lib.core.device_identity._windows_machine_guid",
            return_value="machine-b",
        ):
            reset_device_tag_cache()
            second = get_device_tag()
        self.assertNotEqual(first, second)

    def test_tag_does_not_contain_the_machine_guid(self):
        with patch(
            "lib.core.device_identity._windows_machine_guid",
            return_value="secret-machine-guid-value",
        ):
            reset_device_tag_cache()
            tag = get_device_tag()
        self.assertNotIn("secret", tag)
        self.assertNotIn("guid", tag.lower())


if __name__ == "__main__":
    unittest.main()
