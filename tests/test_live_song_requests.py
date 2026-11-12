from __future__ import annotations

import json
import struct
import threading
import time
import unittest
import zlib
import unittest.mock
from unittest.mock import patch

from lib.script.music.bilibili_live import (
    build_packet,
    iter_messages,
    normalize_room_id,
    parse_danmaku,
)
from lib.script.music.live_song_requests import (
    LiveSongRequestService,
    SongRequest,
    parse_song_request,
    probe_live_room,
)
from lib.script.music.service import MusicService
from lib.script.music.types import MusicTrack
from lib.core.event.center import EventType


def _danmaku_payload(text: str, *, user_id: str = "42", username: str = "观众") -> bytes:
    document = {
        "cmd": "DANMU_MSG",
        "info": [
            [],
            text,
            [int(user_id), username],
        ],
    }
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


class RoomIdTests(unittest.TestCase):
    def test_accepts_numeric_room_and_url_forms(self):
        self.assertEqual(normalize_room_id("123"), "123")
        self.assertEqual(normalize_room_id("00123"), "123")
        self.assertEqual(normalize_room_id("https://live.bilibili.com/123"), "123")
        self.assertEqual(normalize_room_id("https://live.bilibili.com/123?share=1"), "123")
        self.assertEqual(normalize_room_id("live.bilibili.com/123/"), "123")
        self.assertEqual(normalize_room_id("https://live.bilibili.com/blanc/123"), "123")

    def test_rejects_empty_non_numeric_and_zero(self):
        for value in (
            "",
            None,
            "abc",
            "https://example.com/123",
            "https://space.bilibili.com/123",
            "0",
            "live.bilibili.com/abc",
        ):
            self.assertEqual(normalize_room_id(value), "", value)


class SongRequestParsingTests(unittest.TestCase):
    def test_parses_title_only_and_artist(self):
        self.assertEqual(parse_song_request("点歌-纸飞机"), SongRequest(title="纸飞机"))
        self.assertEqual(
            parse_song_request("点歌-纸飞机（鸣潮）"),
            SongRequest(title="纸飞机", artist="鸣潮"),
        )

    def test_accepts_fullwidth_dash_and_parentheses(self):
        self.assertEqual(
            parse_song_request("点歌－ 繁星，新生，与你Astrum （Astrum）"),
            SongRequest(title="繁星，新生，与你Astrum", artist="Astrum"),
        )
        self.assertEqual(
            parse_song_request("點歌—纸飞机(鸣潮)"),
            SongRequest(title="纸飞机", artist="鸣潮"),
        )

    def test_repeated_dashes_are_consumed_as_one_separator(self):
        """观众常打「点歌——歌名」；多出来的横线不能留在歌名里污染搜索。"""
        for text in ("点歌——纸飞机", "点歌--纸飞机", "点歌—— 纸飞机（鸣潮）"):
            self.assertEqual(
                parse_song_request(text).title,
                "纸飞机",
                text,
            )
        self.assertEqual(
            parse_song_request("点歌——纸飞机（鸣潮）").artist,
            "鸣潮",
        )

    def test_custom_prefix_still_accepts_default_prefix(self):
        self.assertEqual(
            parse_song_request("来一首-纸飞机", prefix="来一首"),
            SongRequest(title="纸飞机"),
        )
        self.assertEqual(
            parse_song_request("点歌-纸飞机", prefix="来一首"),
            SongRequest(title="纸飞机"),
        )
        self.assertIsNone(parse_song_request("唱歌-纸飞机", prefix="来一首"))

    def test_rejects_invalid_or_too_long_requests(self):
        for text in ("", "点歌", "点歌-", "点歌 -   ", "纸飞机", "点歌-纸飞机（"):
            self.assertIsNone(parse_song_request(text), text)
        self.assertIsNone(parse_song_request("点歌-" + "长" * 51))
        self.assertIsNone(parse_song_request("点歌-纸飞机（" + "长" * 31 + "）"))
        self.assertIsNone(parse_song_request("点歌-纸飞机\x00"))


class ProtocolTests(unittest.TestCase):
    def test_iter_messages_reads_plain_and_zlib_packets(self):
        plain = build_packet(5, b"hello")
        compressed = build_packet(5, zlib.compress(build_packet(5, b"inner")))
        messages = list(iter_messages(plain + compressed))
        self.assertEqual(messages, [(5, b"hello"), (5, b"inner")])

    def test_iter_messages_stops_on_truncated_or_invalid_packet(self):
        truncated = struct.pack(">IHHII", 40, 16, 1, 5, 1) + b"short"
        self.assertEqual(list(iter_messages(truncated)), [])
        invalid_size = struct.pack(">IHHII", 4, 16, 1, 5, 1)
        self.assertEqual(list(iter_messages(invalid_size)), [])

    def test_parse_danmaku_extracts_text_and_user(self):
        message = parse_danmaku(_danmaku_payload("点歌-纸飞机", user_id="7", username="雪绒"))
        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual(message.text, "点歌-纸飞机")
        self.assertEqual(message.user_id, "7")
        self.assertEqual(message.username, "雪绒")

    def test_parse_danmaku_ignores_other_or_malformed_commands(self):
        self.assertIsNone(parse_danmaku(b"not-json"))
        self.assertIsNone(parse_danmaku(json.dumps({"cmd": "SEND_GIFT", "info": []}).encode()))
        document = {"cmd": "DANMU_MSG", "info": [[], "", [1, "x"]]}
        self.assertIsNone(parse_danmaku(json.dumps(document).encode()))


class DanmakuEndpointFallbackTests(unittest.TestCase):
    """`getDanmuInfo` 现在对裸请求回 `-352`，必须能回退到旧接口。"""

    @staticmethod
    def _response(payload):
        response = unittest.mock.Mock()
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        return response

    def test_falls_back_to_the_legacy_conf_endpoint(self):
        from lib.script.music import live_song_requests as module

        responses = [
            self._response({"code": -352, "message": "-352"}),
            self._response({
                "code": 0,
                "data": {
                    "token": "legacy-token",
                    "host_server_list": [{"host": "a.example", "wss_port": 443}],
                },
            }),
        ]
        with patch.object(module.requests, "get", side_effect=responses):
            token, hosts = module._fetch_danmaku_endpoint("23466627")

        self.assertEqual(token, "legacy-token")
        self.assertEqual(hosts[0]["host"], "a.example")

    def test_prefers_the_new_endpoint_when_it_answers(self):
        from lib.script.music import live_song_requests as module

        responses = [
            self._response({"code": 0, "data": {"token": "new-token", "host_list": [{"host": "b.example"}]}}),
        ]
        with patch.object(module.requests, "get", side_effect=responses) as getter:
            token, hosts = module._fetch_danmaku_endpoint("123")

        self.assertEqual(token, "new-token")
        self.assertEqual(hosts[0]["host"], "b.example")
        self.assertEqual(getter.call_count, 1)


class AuthReplyTests(unittest.TestCase):
    def test_auth_reply_code_is_parsed(self):
        from lib.script.music.bilibili_live import parse_auth_reply

        self.assertEqual(parse_auth_reply(b'{"code":0}'), 0)
        self.assertEqual(parse_auth_reply(b'{"code":-101}'), -101)
        self.assertIsNone(parse_auth_reply(b"not json"))

    def test_auth_failure_raises_instead_of_reporting_connected(self):
        """认证码非 0 必须报错：否则界面显示「已连接」却永远收不到弹幕。"""
        from lib.script.music.live_song_requests import _require_auth_ok

        class _Socket:
            def recv(self):
                return build_packet(8, b'{"code":-101}')

        with self.assertRaisesRegex(RuntimeError, "-101"):
            _require_auth_ok(_Socket())

    def test_auth_success_is_accepted(self):
        from lib.script.music.live_song_requests import _require_auth_ok

        class _Socket:
            def recv(self):
                return build_packet(8, b'{"code":0}')

        _require_auth_ok(_Socket())


class LiveServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = LiveSongRequestService()
        self.config = {
            "live_mode_enabled": False,
            "enabled": False,
            "room_id": "",
            "command_prefix": "点歌",
            "request_cooldown_seconds": 10,
            "duplicate_window_seconds": 120,
            "pending_limit": 10,
            "max_queue_size": 200,
            "song_title_max_length": 50,
            "artist_max_length": 30,
            "notify_on_enqueue": True,
        }
        patcher = patch("lib.script.music.live_song_requests.LIVE_STREAM", self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.service.cleanup)

    def test_enable_without_room_id_reports_configuration_error(self):
        self.assertFalse(self.service.set_enabled(True, source="test"))
        status = self.service.status()
        self.assertFalse(status.enabled)
        self.assertIn("直播间 ID", status.last_error)

    def test_request_cooldown_duplicate_and_pending_limit(self):
        request = SongRequest(title="纸飞机")
        self.assertTrue(self.service._allow_request(request, "user", 1000.0))
        self.assertFalse(self.service._allow_request(request, "other", 1001.0))
        self.assertTrue(self.service._allow_request(SongRequest(title="另一首"), "user", 1011.0))
        self.assertFalse(self.service._allow_request(SongRequest(title="第三首"), "user", 1012.0))

    def test_pending_futures_are_released_when_finished(self):
        from concurrent.futures import Future

        future = Future()
        with self.service._search_lock:
            self.service._pending.add(future)
        future.set_result(None)
        self.service._discard_pending(future)
        self.assertEqual(self.service._pending, set())

    def test_finished_task_does_not_deadlock_the_danmaku_thread(self):
        """搜索在 `add_done_callback` 之前就完成时，接收线程不能被锁死。

        `Future.add_done_callback` 对已完成的任务会**当场同步执行**回调；旧实现把注册
        写在 `with self._search_lock` 里面，而回调 `_discard_pending` 又要拿同一把锁，
        于是持锁线程自锁。这段代码跑在弹幕接收线程上——一旦卡住就不再 `recv()`、也不再
        发心跳，表现为「连接看着正常，却一条弹幕都没有」，只能重启程序恢复。
        """
        from concurrent.futures import Future

        class _Hub:
            @staticmethod
            def submit_io(fn, *args):
                # 模拟「任务已经跑完」：返回一个已完成 Future。
                done = Future()
                done.set_result(None)
                return done

        self.config["room_id"] = "123"
        self.service.set_song_enabled(True, source="test")
        payload = _danmaku_payload("点歌-纸飞机", user_id="7", username="雪绒")

        ran = []

        def call() -> None:
            with patch("lib.script.music.live_song_requests.get_compute_hub", return_value=_Hub()):
                self.service._handle_payload(payload)
            ran.append(True)

        worker = threading.Thread(target=call, name="live-song-recv", daemon=True)
        worker.start()
        worker.join(5.0)
        self.assertTrue(ran, "弹幕处理在已完成的搜索任务上自锁了")
        self.assertEqual(self.service._pending, set(), "已完成的任务要及时释放待处理名额")

    def test_cleanup_is_idempotent(self):
        self.service.cleanup()
        self.service.cleanup()
        self.assertFalse(self.service.is_enabled)

    def test_restart_does_not_reuse_a_stopped_worker_event(self):
        """快速关→开必须换新一代 worker，不能被旧停止事件立刻带停。"""
        self.config["room_id"] = "123"
        started: list[threading.Event] = []

        def fake_start() -> None:
            stop_event = threading.Event()
            started.append(stop_event)
            with self.service._lock:
                self.service._worker_stop = stop_event

        with patch.object(self.service, "_start_worker", fake_start):
            self.assertTrue(self.service.set_enabled(True, source="test"))
            self.assertTrue(self.service.set_enabled(False, source="test"))
            self.assertTrue(self.service.set_enabled(True, source="test"))

        self.assertEqual(len(started), 2)
        self.assertIsNot(started[0], started[1])
        self.assertTrue(started[0].is_set())
        self.assertFalse(started[1].is_set())

    def test_short_room_is_not_treated_as_a_config_change_after_resolve(self):
        """短号解析成真实房间号后，保存其它直播参数不应触发重连。"""
        self.config["room_id"] = "123"
        with patch.object(self.service, "_start_worker"):
            self.service.set_enabled(True, source="test")
        self.config["live_mode_enabled"] = True
        self.config["enabled"] = True
        self.service._thread = threading.Thread(target=lambda: None)
        self.service._configured_room_id = "123"
        with self.service._lock:
            self.service._status.room_id = "987654"

        restarted: list[bool] = []

        def fake_stop() -> None:
            restarted.append(True)

        with patch.object(self.service, "_stop_worker", fake_stop):
            self.service.reload_from_config()

        self.assertEqual(restarted, [])
        self.assertTrue(self.service.is_enabled)

    def test_danmu_info_reports_missing_token_instead_of_connecting(self):
        """未开播房间拿不到 token 时要留在错误态，不能带空 token 去连 WS。"""
        response = type("Response", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: {"code": 0, "data": {"token": "", "host_list": []}},
        })()
        with patch("lib.script.music.live_song_requests.requests.get", return_value=response):
            with self.assertRaises(RuntimeError) as ctx:
                self.service._danmu_info("123")
        self.assertIn("token", str(ctx.exception))

    def test_enqueue_song_request_parses_and_rejects_noise(self):
        self.assertFalse(self.service.enqueue_song_request("普通弹幕"))
        with patch.object(self.service, "_search_and_enqueue") as worker:
            self.assertTrue(self.service.enqueue_song_request("点歌-纸飞机", username="观众"))
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and not worker.called:
                time.sleep(0.01)
            worker.assert_called_once()


class LiveModeSwitchTests(unittest.TestCase):
    """总开关（直播模式）与弹幕点歌子开关是两件事。

    总开关管「要不要连弹幕服务器」，托盘「直播模式」用的就是它；点歌开关只管
    「收到弹幕要不要解析点歌命令」。礼物提醒与点歌共用同一条连接，所以关总开关时
    两者都不工作，但关点歌时连接必须保持——这是本次拆分的核心不变式。
    """

    def setUp(self):
        self.config = {
            "live_mode_enabled": False,
            "enabled": False,
            "room_id": "",
            "command_prefix": "点歌",
            "request_cooldown_seconds": 10,
            "duplicate_window_seconds": 120,
            "pending_limit": 10,
            "max_queue_size": 200,
            "song_title_max_length": 50,
            "artist_max_length": 30,
            "notify_on_enqueue": True,
        }
        # 先打补丁再建服务：服务构造时就会按配置落一次点歌开关状态。
        patcher = patch("lib.script.music.live_song_requests.LIVE_STREAM", self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.service = LiveSongRequestService()
        self.addCleanup(self.service.cleanup)

    def test_live_mode_requires_a_room_id(self):
        self.assertFalse(self.service.set_enabled(True, source="test"))
        self.assertIn("直播间 ID", self.service.status().last_error)

    def test_song_switch_does_not_open_a_connection(self):
        self.config["room_id"] = "123"
        self.assertTrue(self.service.set_song_enabled(True, source="test"))
        status = self.service.status()
        self.assertTrue(status.song_enabled)
        self.assertFalse(status.enabled, "点歌开关不能顺带打开总开关")
        self.assertIsNone(self.service._thread)

    def test_turning_song_off_keeps_the_connection(self):
        self.config["room_id"] = "123"
        with patch.object(self.service, "_start_worker"):
            self.service.set_enabled(True, source="test")
        self.service.set_song_enabled(True, source="test")
        with patch.object(self.service, "_stop_worker") as stop:
            self.assertTrue(self.service.set_song_enabled(False, source="test"))
        stop.assert_not_called()
        self.assertTrue(self.service.status().enabled)
        self.assertFalse(self.service.status().song_enabled)

    def test_song_enabled_follows_config_but_live_mode_does_not(self):
        self.config.update({"live_mode_enabled": False, "enabled": True, "room_id": "123"})
        self.service.reload_from_config()
        status = self.service.status()
        self.assertTrue(status.song_enabled)
        self.assertFalse(status.enabled, "总开关关着时不能仅凭点歌开关连上去")

    def test_reload_with_both_switches_on_connects(self):
        self.config.update({"live_mode_enabled": True, "enabled": True, "room_id": "123"})
        with patch.object(self.service, "_start_worker") as start:
            self.service.reload_from_config()
        start.assert_called_once()
        self.assertTrue(self.service.status().enabled)
        self.assertTrue(self.service.status().song_enabled)

    def test_danmaku_is_ignored_when_song_switch_is_off(self):
        """点歌关掉时连接仍在读弹幕（礼物提醒还要用），但不解析点歌命令。"""
        payload = _danmaku_payload("点歌-纸飞机", user_id="7", username="雪绒")
        with patch.object(self.service, "_allow_request") as allow:
            self.service.set_song_enabled(False, source="test")
            self.service._handle_payload(payload)
            allow.assert_not_called()
            self.service.set_song_enabled(True, source="test")
            self.service._handle_payload(payload)
            allow.assert_called_once()

    def test_status_event_carries_both_switches(self):
        events = []
        with patch("lib.script.music.live_song_requests.get_event_center") as center:
            center.return_value.subscribe = lambda *a, **k: None
            center.return_value.publish = lambda event: events.append(event)
            service = LiveSongRequestService()
            service._event_center = center.return_value
            service.set_song_enabled(True, source="test")
        self.assertTrue(events)
        data = events[-1].data
        self.assertIn("song_enabled", data)
        self.assertIn("enabled", data)


class LiveProbeTests(unittest.TestCase):
    """「测试直播间」的只读探测：解析房间号 -> 取 token -> 试连，全程不改配置。"""

    def _response(self, payload):
        return type("Response", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: payload,
        })()

    def _socket(self, *, recv_error=None):
        class Socket:
            def __init__(self):
                self.sent = []
                self.closed = False

            def send_binary(self, payload):
                self.sent.append(payload)

            def settimeout(self, _timeout):
                pass

            def recv(self):
                if recv_error is not None:
                    raise recv_error
                return b""

            def close(self):
                self.closed = True

        return Socket()

    def test_probe_reports_missing_room_id_without_network(self):
        with patch("lib.script.music.live_song_requests.requests.get") as getter:
            result = probe_live_room("")
        self.assertFalse(result.ok)
        self.assertIn("直播间 ID", result.message)
        getter.assert_not_called()

    def test_probe_rejects_a_room_that_does_not_exist(self):
        """`room_init` 回 60004 + 空 data 时不能沿用原房间号报成功。

        旧实现只看 HTTP 状态码，不存在的房间会被解析成「自己」，探测显示
        ✓ 已连接——而 `getConf` 对任何房间都发得出 token，WebSocket 也会回 code=0，
        后面两层都拦不住，所以校验必须在解析房间号这一步做完。
        """
        response = self._response({"code": 60004, "message": "房间不存在", "data": []})
        with patch("lib.script.music.live_song_requests.requests.get", return_value=response), \
                patch("websocket.create_connection") as connect:
            result = probe_live_room("99999999999")
        self.assertFalse(result.ok)
        self.assertIn("不存在", result.message)
        connect.assert_not_called()

    def test_probe_reports_offline_room_instead_of_connecting(self):
        """未开播/轮播的房间即使拿得到 token 也要报失败，否则用户会一直等弹幕。"""
        for live_status, hint in ((0, "未开播"), (2, "轮播")):
            response = self._response({"code": 0, "data": {"room_id": 123, "live_status": live_status}})
            with patch("lib.script.music.live_song_requests.requests.get", return_value=response), \
                    patch("websocket.create_connection") as connect:
                result = probe_live_room("123")
            self.assertFalse(result.ok, live_status)
            self.assertIn(hint, result.message)
            connect.assert_not_called()

    def test_probe_resolves_room_token_and_connects(self):
        responses = [
            self._response({"code": 0, "data": {"room_id": 987654}}),
            self._response({"code": 0, "data": {"token": "tok", "host_list": [{"host": "a.example"}]}}),
        ]
        socket = self._socket()
        config = {"room_id": "123", "enabled": False}

        with patch("lib.script.music.live_song_requests.LIVE_STREAM", config), \
                patch("lib.script.music.live_song_requests.requests.get", side_effect=responses), \
                patch("websocket.create_connection", return_value=socket) as connect:
            result = probe_live_room("https://live.bilibili.com/123")

        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.room_id, "987654")
        self.assertIn("987654", result.message)
        connect.assert_called_once()
        self.assertIn("a.example", connect.call_args[0][0])
        self.assertEqual(len(socket.sent), 1)
        self.assertTrue(socket.closed)
        # 探测只读：不改动运行中服务的开关与房间号。
        self.assertEqual(config, {"room_id": "123", "enabled": False})

    def test_probe_reports_missing_token_as_failure(self):
        # 两个接口都要走一遍：新版 getDanmuInfo 回空 token 时会回退到旧版 getConf，
        # 旧版同样没有 token 才判失败（未开播的直播间就是这样）。
        responses = [
            self._response({"code": 0, "data": {"room_id": 123}}),
            self._response({"code": 0, "data": {"token": "", "host_list": []}}),
            self._response({"code": 0, "data": {"token": "", "host_server_list": []}}),
        ]
        with patch("lib.script.music.live_song_requests.requests.get", side_effect=responses), \
                patch("websocket.create_connection") as connect:
            result = probe_live_room("123")
        self.assertFalse(result.ok)
        self.assertIn("token", result.message)
        connect.assert_not_called()

    def test_probe_reports_socket_failure_and_still_closes(self):
        responses = [
            self._response({"code": 0, "data": {"room_id": 123}}),
            self._response({"code": 0, "data": {"token": "tok", "host_list": []}}),
        ]
        with patch("lib.script.music.live_song_requests.requests.get", side_effect=responses), \
                patch("websocket.create_connection", side_effect=OSError("拒绝连接")):
            result = probe_live_room("123")
        self.assertFalse(result.ok)
        self.assertIn("拒绝连接", result.message)


class MusicEnqueueTests(unittest.TestCase):
    def test_enqueue_track_fails_when_no_backend_is_available(self):
        """没有播放后端时不能假装入队成功，否则直播点歌会静默丢歌。"""
        service = MusicService()
        with patch.object(service, "_get_backend_manager", return_value=None):
            self.assertFalse(service.enqueue_track(MusicTrack(
                provider="netease", track_id="netease:1", title="纸飞机",
            )))

    def test_enqueue_track_publishes_event_with_autoplay_flag(self):
        service = MusicService()
        events = []

        class _Backend:
            queue: list = []

        class _Center:
            def publish(self, event) -> None:
                events.append(event)

        with patch.object(service, "_get_backend_manager", return_value=_Backend()), \
                patch("lib.script.music.service.get_event_center", return_value=_Center()):
            ok = service.enqueue_track(
                MusicTrack(provider="netease", track_id="netease:1", title="纸飞机"),
                silent=True,
                autoplay=False,
            )

        self.assertTrue(ok)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, EventType.MUSIC_ENQUEUE)
        self.assertFalse(events[0].data["autoplay"])
        self.assertTrue(events[0].data["silent"])


if __name__ == "__main__":
    unittest.main()
