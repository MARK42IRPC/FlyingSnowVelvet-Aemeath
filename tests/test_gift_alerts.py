"""礼物提醒：解析、模板渲染、阈值判定与投递。"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from lib.core.event.center import EventType, get_event_center
from lib.script.music import gift_alerts
from lib.script.music.bilibili_live import parse_gift


def _gift_payload(*, name="小心心", num=3, price=100, uname="观众A", uid="42"):
    return json.dumps({
        "cmd": "SEND_GIFT",
        "data": {
            "giftName": name,
            "num": num,
            "price": price,
            "uname": uname,
            "uid": uid,
        },
    }).encode("utf-8")


def _settings(**overrides):
    values = {
        "enabled": True,
        "template": "感谢<name>送出的<number>个<gift>",
        "min_price": 0,
        "notify_in_chat": True,
        "speak": True,
    }
    values.update(overrides)
    return values


class ParseGiftTests(unittest.TestCase):
    def test_reads_gift_fields(self):
        gift = parse_gift(_gift_payload())

        self.assertEqual(gift.gift_name, "小心心")
        self.assertEqual(gift.number, 3)
        self.assertEqual(gift.price, 100)
        self.assertEqual(gift.username, "观众A")
        self.assertEqual(gift.total_price, 300)

    def test_non_gift_payloads_are_ignored(self):
        self.assertIsNone(parse_gift(b'{"cmd":"DANMU_MSG","info":[]}'))
        self.assertIsNone(parse_gift(b"not json"))
        # 没有礼物名的负载对提醒没有意义。
        self.assertIsNone(parse_gift(json.dumps({"cmd": "SEND_GIFT", "data": {"num": 1}}).encode()))

    def test_missing_or_invalid_numbers_fall_back_to_safe_defaults(self):
        gift = parse_gift(_gift_payload(num=None, price="abc"))

        self.assertEqual(gift.number, 1)
        self.assertEqual(gift.price, 0)


class RenderGiftTextTests(unittest.TestCase):
    def test_default_template_when_none_is_configured(self):
        gift = parse_gift(_gift_payload())

        self.assertEqual(gift_alerts.render_gift_text(gift, ""), "感谢观众A送出的3个小心心")
        self.assertEqual(
            gift_alerts.render_gift_text(gift, "   "),
            gift_alerts.render_gift_text(gift, gift_alerts.DEFAULT_GIFT_TEMPLATE),
        )

    def test_placeholders_are_case_insensitive_and_any_order(self):
        gift = parse_gift(_gift_payload())

        self.assertEqual(
            gift_alerts.render_gift_text(gift, "<NAME>送了<Gift>×<Number>"),
            "观众A送了小心心×3",
        )

    def test_unknown_placeholders_are_kept_verbatim(self):
        gift = parse_gift(_gift_payload())

        self.assertEqual(
            gift_alerts.render_gift_text(gift, "<gift> <3 <name>"),
            "小心心 <3 观众A",
        )

    def test_missing_username_falls_back_to_a_generic_name(self):
        gift = parse_gift(_gift_payload(uname=""))

        self.assertEqual(gift_alerts.render_gift_text(gift, ""), "感谢有人送出的3个小心心")

    def test_whitespace_left_by_placeholders_is_collapsed(self):
        gift = parse_gift(_gift_payload())

        self.assertEqual(
            gift_alerts.render_gift_text(gift, "感谢 <name>  送出 <gift>"),
            "感谢 观众A 送出 小心心",
        )


class ShouldAlertTests(unittest.TestCase):
    def test_disabled_never_alerts(self):
        gift = parse_gift(_gift_payload(price=99999))

        self.assertFalse(gift_alerts.should_alert(gift, enabled=False, min_price=0))

    def test_zero_threshold_accepts_everything(self):
        gift = parse_gift(_gift_payload(price=0))

        self.assertTrue(gift_alerts.should_alert(gift, enabled=True, min_price=0))

    def test_threshold_compares_the_batch_total(self):
        """阈值按单价 × 数量判定：10 个 1 元与 1 个 10 元等价。"""
        cheap_batch = parse_gift(_gift_payload(price=10, num=10))
        single = parse_gift(_gift_payload(price=10, num=1))

        self.assertTrue(gift_alerts.should_alert(cheap_batch, enabled=True, min_price=100))
        self.assertFalse(gift_alerts.should_alert(single, enabled=True, min_price=100))

    def test_invalid_threshold_is_treated_as_zero(self):
        gift = parse_gift(_gift_payload(price=1))

        self.assertTrue(gift_alerts.should_alert(gift, enabled=True, min_price="abc"))


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.center = get_event_center()
        self._subscriptions = []
        self.received = []
        self._subscribe(
            EventType.INFORMATION,
            lambda event: self.received.append(("message", event.data.get("text"))),
        )
        self._subscribe(
            EventType.AI_VOICE_REQUEST,
            lambda event: self.received.append(("voice", event.data.get("text"))),
        )

    def tearDown(self):
        # 只退订本用例挂的订阅，不销毁全局事件中心：`cleanup_event_center()` 会把单例
        # 置空，而 `GiftAlertService` 在构造时就抓住了旧单例，后续用例的事件全发到
        # 一个已经没人订阅的旧对象上（表现为「投递了但收不到」）。
        for event_type, callback in self._subscriptions:
            self.center.unsubscribe(event_type, callback)

    def _subscribe(self, event_type, callback):
        self.center.subscribe(event_type, callback)
        self._subscriptions.append((event_type, callback))

    def _handle(self, payload, **settings):
        with patch.object(gift_alerts, "_settings", return_value=_settings(**settings)):
            return gift_alerts.get_gift_alert_service().handle_payload(payload)

    def test_alert_goes_to_both_queues(self):
        text = self._handle(_gift_payload())

        self.assertEqual(text, "感谢观众A送出的3个小心心")
        self.assertEqual(
            self.received,
            [("message", text), ("voice", text)],
        )

    def test_disabled_service_publishes_nothing(self):
        self.assertEqual(self._handle(_gift_payload(), enabled=False), "")
        self.assertEqual(self.received, [])

    def test_below_threshold_publishes_nothing(self):
        self.assertEqual(self._handle(_gift_payload(price=10, num=1), min_price=100), "")
        self.assertEqual(self.received, [])

    def test_switches_can_silence_one_queue_each(self):
        self._handle(_gift_payload(), speak=False)
        self.assertEqual([kind for kind, _text in self.received], ["message"])

        self.received.clear()
        self._handle(_gift_payload(), notify_in_chat=False)
        self.assertEqual([kind for kind, _text in self.received], ["voice"])

    def test_danmaku_payload_is_not_treated_as_a_gift(self):
        payload = json.dumps({"cmd": "DANMU_MSG", "info": [[], "点歌-纸飞机", [1, "观众"]]}).encode()

        self.assertEqual(self._handle(payload), "")
        self.assertEqual(self.received, [])

    def test_message_carries_its_own_source(self):
        sources = []
        self._subscribe(
            EventType.INFORMATION,
            lambda event: sources.append(event.data.get("source")),
        )

        self._handle(_gift_payload())

        self.assertEqual(sources, ["gift_alert"])


if __name__ == "__main__":
    unittest.main()
