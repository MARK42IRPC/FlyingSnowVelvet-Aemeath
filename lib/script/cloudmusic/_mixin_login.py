"""网易云音乐管理器 - 登录系统 Mixin"""

import json
import math
import re
import threading
import time
import base64
from pathlib import Path
from urllib.parse import quote_plus, urljoin

import requests

from lib.core.event.center import EventType, Event
from lib.core.logger import get_logger
from lib.script.browser_auth import launch_playwright_chromium, parse_cookie_header, parse_set_cookie_headers
from config.config import TIMEOUTS, CLOUD_MUSIC

from ._provider_clients import get_kugou_provider_client, get_qqmusic_provider_client
from ._constants import (
    _KUGOU_LOGIN_CACHE_FILE,
    _LOGIN_CACHE_FILE,
    _PROJECT_ROOT,
    _QQ_LOGIN_CACHE_FILE,
    _QR_LOGIN_TIMEOUT,
    _QR_POLL_INTERVAL,
    _QR_REFRESH_INTERVAL,
)

logger = get_logger(__name__)

_QQ_QR_AUTO_REFRESH_INTERVAL = 45.0


class _LoginMixin:
    """网易云账号登录系统：缓存恢复、匿名登录、二维码登录与退出。"""
    _QQ_LOGIN_APPID = "716027609"
    _QQ_LOGIN_DAID = "383"
    _QQ_LOGIN_PT_3RD_AID = "100497308"
    _QQ_LOGIN_S_URL = "https://graph.qq.com/oauth2.0/login_jump"
    _QQ_LOGIN_UI_STYLE = "40"
    _QQ_LOGIN_DEVICE = "2"
    _QQ_XLOGIN_URL = "https://xui.ptlogin2.qq.com/cgi-bin/xlogin"
    _QQ_QRSHOW_URL = "https://xui.ptlogin2.qq.com/ssl/ptqrshow"
    _QQ_PTQRLOGIN_URL = "https://ssl.ptlogin2.qq.com/ptqrlogin"
    _QQ_LOGIN_TIMEOUT = (8, 20)

    # ------------------------------------------------------------------
    # 静态辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _is_account_logged_in(status: dict) -> bool:
        """判断当前登录态是否为非匿名账号。"""
        if not isinstance(status, dict):
            return False
        account = status.get('account') or {}
        if not isinstance(account, dict) or not account:
            return False
        return not bool(account.get('anonimousUser', False))

    @staticmethod
    def _profile_from_status(status: dict) -> dict:
        """从登录状态中提取可展示的账号信息。"""
        if not isinstance(status, dict):
            return {}
        profile = status.get('profile')
        if isinstance(profile, dict) and profile:
            return profile
        account = status.get('account') or {}
        if isinstance(account, dict):
            name = str(account.get('userName') or '').strip()
            if name:
                return {'nickname': name}
        return {}

    @staticmethod
    def _is_cookie_conflict_error(error: Exception) -> bool:
        """判断是否为 pyncm requests-cookie 冲突错误（同名 __csrf）。"""
        text = str(error)
        if not text:
            return False
        lower = text.lower()
        return "__csrf" in lower and "multiple cookies" in lower

    @staticmethod
    def _current_provider() -> str:
        return str(CLOUD_MUSIC.get('provider', 'netease') or 'netease').strip().lower()

    @staticmethod
    def _is_qq_provider() -> bool:
        return _LoginMixin._current_provider() == 'qq'

    @staticmethod
    def _is_kugou_provider() -> bool:
        return _LoginMixin._current_provider() == 'kugou'

    @staticmethod
    def _qq_ptqrtoken(qrsig: str) -> int:
        e = 0
        for ch in str(qrsig or ''):
            e += (e << 5) + ord(ch)
        return e & 2147483647

    @staticmethod
    def _parse_qq_login_cb(raw_text: str) -> tuple[str, str, str, str]:
        """
        解析 ptqrlogin 返回：ptuiCB('code','sub','url','flag','msg','nickname')
        返回 (code, redirect_url, message, nickname)
        """
        text = str(raw_text or '').strip()
        m = re.search(r"ptuiCB\((.*)\)", text)
        if not m:
            return "", "", text, ""
        inner = m.group(1)
        parts = re.findall(r"'([^']*)'", inner)
        if len(parts) >= 5:
            code = parts[0]
            redirect = parts[2]
            msg = parts[4]
            nickname = parts[5] if len(parts) >= 6 else ""
            return code, redirect, msg, nickname
        return "", "", text, ""

    @staticmethod
    def _normalize_qq_uin(raw_uin: str | None) -> str:
        text = str(raw_uin or '0').strip()
        match = re.search(r'(\d+)', text)
        return match.group(1) if match else '0'

    @classmethod
    def _qq_xlogin_params(cls) -> dict[str, str]:
        # 同步 QQ 官方 login_10.js 当前参数，避免旧参数导致 ptqrlogin 403。
        return {
            "appid": cls._QQ_LOGIN_APPID,
            "daid": cls._QQ_LOGIN_DAID,
            "style": "33",
            "login_text": "登录",
            "hide_title_bar": "1",
            "hide_border": "1",
            "target": "self",
            "s_url": cls._QQ_LOGIN_S_URL,
            "pt_3rd_aid": cls._QQ_LOGIN_PT_3RD_AID,
            "theme": "2",
            "verify_theme": "",
        }

    @classmethod
    def _qq_qrshow_params(cls) -> dict[str, str]:
        return {
            "s": "8",
            "e": "0",
            "appid": cls._QQ_LOGIN_APPID,
            "type": "1",
            "t": str(time.time()),
            "u1": cls._QQ_LOGIN_S_URL,
            "daid": cls._QQ_LOGIN_DAID,
            "pt_3rd_aid": cls._QQ_LOGIN_PT_3RD_AID,
        }

    @classmethod
    def _qq_ptqrlogin_params(cls, ptqrtoken: str) -> dict[str, str]:
        return {
            "u1": cls._QQ_LOGIN_S_URL,
            "from_ui": "1",
            "type": "1",
            "ptlang": "2052",
            "ptqrtoken": str(ptqrtoken or ""),
            "daid": cls._QQ_LOGIN_DAID,
            "aid": cls._QQ_LOGIN_APPID,
            "pt_3rd_aid": cls._QQ_LOGIN_PT_3RD_AID,
            "device": cls._QQ_LOGIN_DEVICE,
            "ptopt": "1",
            "pt_uistyle": cls._QQ_LOGIN_UI_STYLE,
        }

    def _qq_sync_login_session(self, session, headers: dict[str, str], redirect_url: str) -> None:
        # 登录成功后补齐跳转链路，促使 y.qq.com 侧 Cookie 写入完整。
        urls = [
            redirect_url,
            self._QQ_LOGIN_S_URL,
            "https://y.qq.com/m/login/redirect.html?is_qq_connect=1&login_type=1&surl=https%3A%2F%2Fy.qq.com%2Fn%2Fryqq%2Findex.html",
            "https://y.qq.com/",
            "https://y.qq.com/n/ryqq/index.html",
        ]
        for url in urls:
            if not url:
                continue
            try:
                session.get(str(url), headers=headers, timeout=self._QQ_LOGIN_TIMEOUT, allow_redirects=True)
            except Exception:
                continue

    @staticmethod
    def _qq_build_browser_cookie_items(cookie_map: dict[str, str] | None) -> list[dict[str, object]]:
        data = cookie_map if isinstance(cookie_map, dict) else {}
        items: list[dict[str, object]] = []
        if not data:
            return items
        for name, value in data.items():
            cookie_name = str(name or '').strip()
            cookie_value = str(value or '').strip()
            if not cookie_name or not cookie_value:
                continue
            for domain in ('.qq.com', '.y.qq.com', '.music.qq.com', '.c.y.qq.com', '.c6.y.qq.com'):
                items.append(
                    {
                        'name': cookie_name,
                        'value': cookie_value,
                        'domain': domain,
                        'path': '/',
                        'httpOnly': False,
                        'secure': True,
                    }
                )
        return items

    def _qq_sync_login_context(self, context, page, redirect_url: str, seed_cookie_map: dict[str, str] | None = None) -> dict[str, str]:
        merged_cookie_map: dict[str, str] = {}
        if isinstance(seed_cookie_map, dict):
            merged_cookie_map.update({str(k): str(v) for k, v in seed_cookie_map.items() if k and v is not None})

        if context is not None and merged_cookie_map:
            try:
                context.add_cookies(self._qq_build_browser_cookie_items(merged_cookie_map))
            except Exception:
                pass

        urls = [
            redirect_url,
            self._QQ_LOGIN_S_URL,
            'https://y.qq.com/m/login/redirect.html?is_qq_connect=1&login_type=1&surl=https%3A%2F%2Fy.qq.com%2Fn%2Fryqq%2Findex.html',
            'https://y.qq.com/n/ryqq/index.html',
            'https://y.qq.com/portal/profile.html',
        ]
        for url in urls:
            if not url:
                continue
            try:
                page.goto(str(url), wait_until='domcontentloaded', timeout=15000)
            except Exception:
                continue
            try:
                page.wait_for_timeout(500)
            except Exception:
                time.sleep(0.5)
            try:
                merged_cookie_map.update(self._qq_collect_context_cookie_map(context))
            except Exception:
                pass
            try:
                merged_cookie_map.update(self._qq_collect_storage_state_map(page, context))
            except Exception:
                pass
            if self._qq_cookie_map_has_uin(merged_cookie_map) and self._qq_cookie_map_has_music_auth(merged_cookie_map):
                break
        return merged_cookie_map

    @staticmethod
    def _qq_nickname_hint(session, fallback: str = "QQ账号") -> str:
        try:
            cookies = session.cookies.get_dict() or {}
        except Exception:
            return fallback
        nickname = str(cookies.get("nick") or "").strip()
        if nickname:
            return nickname
        uin = str(cookies.get("uin") or cookies.get("p_uin") or "").strip()
        m = re.search(r"(\d{4,})", uin)
        if not m:
            return fallback
        num = m.group(1)
        return f"QQ账号({num[-4:]})"

    @staticmethod
    def _login_success_message(platform_name: str, nickname: str | None) -> str:
        clean_platform = str(platform_name or "").strip() or "平台"
        clean_nickname = str(nickname or "").strip() or "未知用户"
        return f"{clean_platform}登录成功:{clean_nickname}"

    @staticmethod
    def _qq_is_png_bytes(data: bytes) -> bool:
        return isinstance(data, (bytes, bytearray)) and bytes(data).startswith(b"\x89PNG\r\n\x1a\n")

    def _qq_qr_png_from_response(self, response) -> bytes | None:
        data = bytes(getattr(response, "content", b"") or b"")
        if self._qq_is_png_bytes(data):
            return data

        text = str(getattr(response, "text", "") or "").strip()
        if not text:
            return data or None

        payload_obj = None
        m = re.search(r"ptui_qrcode_CB\((\{.*\})\)\s*;?\s*$", text)
        raw_json = m.group(1) if m else (text if text.startswith("{") and text.endswith("}") else "")
        if raw_json:
            try:
                payload_obj = json.loads(raw_json)
            except Exception:
                payload_obj = None

        if isinstance(payload_obj, dict):
            qr_url = str(payload_obj.get("qrcode") or payload_obj.get("url") or "").strip()
            if qr_url:
                qr_png = self._create_qr_png(qr_url)
                if qr_png:
                    return qr_png

        return data or None

    @staticmethod
    def _qq_has_login_cookies(session) -> bool:
        """通过 QQ 常见登录 Cookie 判断当前会话是否已完成登录。"""
        if session is None:
            return False
        try:
            cookies = session.cookies.get_dict() or {}
        except Exception:
            return False

        uin = str(cookies.get('uin') or cookies.get('wxuin') or cookies.get('p_uin') or '').strip()
        if not uin:
            return False

        return bool(
            str(cookies.get('p_skey') or '').strip()
            or str(cookies.get('skey') or '').strip()
            or str(cookies.get('p_uin') or '').strip()
            or str(cookies.get('pt4_token') or '').strip()
            or str(cookies.get('qm_keyst') or '').strip()
            or str(cookies.get('qqmusic_key') or '').strip()
            or str(cookies.get('music_key') or '').strip()
        )

    @staticmethod
    def _qq_has_uin_cookie(session) -> bool:
        if session is None:
            return False
        try:
            cookies = session.cookies.get_dict() or {}
        except Exception:
            return False
        return bool(str(cookies.get('uin') or cookies.get('wxuin') or cookies.get('p_uin') or '').strip())


    @staticmethod
    def _qq_has_music_auth_cookies(session) -> bool:
        if session is None:
            return False
        try:
            cookies = session.cookies.get_dict() or {}
        except Exception:
            return False
        return bool(
            str(cookies.get('qqmusic_key') or '').strip()
            or str(cookies.get('music_key') or '').strip()
            or str(cookies.get('qm_keyst') or '').strip()
        )

    @staticmethod
    def _qq_cookie_map_has_uin(cookie_map: dict[str, str] | None) -> bool:
        data = cookie_map if isinstance(cookie_map, dict) else {}
        return bool(str(data.get('uin') or data.get('wxuin') or data.get('p_uin') or '').strip())

    @staticmethod
    def _qq_cookie_map_has_music_auth(cookie_map: dict[str, str] | None) -> bool:
        data = cookie_map if isinstance(cookie_map, dict) else {}
        return bool(
            str(data.get('qqmusic_key') or '').strip()
            or str(data.get('music_key') or '').strip()
            or str(data.get('qm_keyst') or '').strip()
        )

    @staticmethod
    def _qq_cookie_map_has_auth(cookie_map: dict[str, str] | None) -> bool:
        data = cookie_map if isinstance(cookie_map, dict) else {}
        return bool(
            str(data.get('p_skey') or '').strip()
            or str(data.get('skey') or '').strip()
            or str(data.get('qqmusic_key') or '').strip()
            or str(data.get('music_key') or '').strip()
            or str(data.get('qm_keyst') or '').strip()
            or str(data.get('pt4_token') or '').strip()
        )

    @staticmethod
    def _qq_collect_browser_cookie_map(raw_cookies) -> dict[str, str]:
        cookie_map: dict[str, str] = {}
        for item in raw_cookies or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get('name') or '').strip()
            value = str(item.get('value') or '').strip()
            if not name or not value:
                continue
            cookie_map[name] = value
        if cookie_map.get('p_uin') and not cookie_map.get('uin'):
            cookie_map['uin'] = cookie_map['p_uin']
        if cookie_map.get('p_skey') and not cookie_map.get('skey'):
            cookie_map['skey'] = cookie_map['p_skey']
        return cookie_map

    @staticmethod
    def _qq_collect_context_cookie_map(context) -> dict[str, str]:
        if context is None:
            return {}
        try:
            raw_cookies = context.cookies()
        except Exception:
            return {}
        return _LoginMixin._qq_collect_browser_cookie_map(raw_cookies)

    @staticmethod
    def _qq_collect_document_cookie_map(page) -> dict[str, str]:
        cookie_map: dict[str, str] = {}
        if page is None:
            return cookie_map
        try:
            frames = list(page.frames)
        except Exception:
            frames = []
        for frame in frames:
            try:
                raw_cookie = frame.evaluate("() => document.cookie || ''")
            except Exception:
                continue
            cookie_map.update(parse_cookie_header(raw_cookie))
        if cookie_map.get('p_uin') and not cookie_map.get('uin'):
            cookie_map['uin'] = cookie_map['p_uin']
        if cookie_map.get('p_skey') and not cookie_map.get('skey'):
            cookie_map['skey'] = cookie_map['p_skey']
        return cookie_map

    @staticmethod
    def _qq_collect_network_cookie_map(headers) -> dict[str, str]:
        if not isinstance(headers, dict):
            return {}
        raw_cookie = str(headers.get('cookie') or headers.get('Cookie') or '').strip()
        cookie_map = parse_cookie_header(raw_cookie)
        if cookie_map.get('p_uin') and not cookie_map.get('uin'):
            cookie_map['uin'] = cookie_map['p_uin']
        if cookie_map.get('p_skey') and not cookie_map.get('skey'):
            cookie_map['skey'] = cookie_map['p_skey']
        return cookie_map

    @staticmethod
    def _qq_extract_music_auth_from_storage(storage_items) -> dict[str, str]:
        result: dict[str, str] = {}

        def _walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    key_text = str(key or '').strip()
                    if key_text in {'qqmusic_key', 'music_key', 'qm_keyst', 'qm_uin', 'qqmusic_uin'}:
                        text = str(item or '').strip()
                        if text:
                            result[key_text] = text
                    _walk(item)
            elif isinstance(value, list):
                for item in value:
                    _walk(item)

        for item in storage_items or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get('key') or '').strip()
            value = item.get('value')
            if key in {'qqmusic_key', 'music_key', 'qm_keyst', 'qm_uin', 'qqmusic_uin'}:
                text = str(value or '').strip()
                if text:
                    result[key] = text
                    continue
            if isinstance(value, str):
                text = value.strip()
                if not text:
                    continue
                try:
                    parsed = json.loads(text)
                except Exception:
                    parsed = None
                if parsed is not None:
                    _walk(parsed)
        return result

    def _qq_collect_storage_state_map(self, page, context=None) -> dict[str, str]:
        storage_items = []
        if context is not None:
            try:
                state = context.storage_state()
                for origin in (state or {}).get('origins', []) or []:
                    if not isinstance(origin, dict):
                        continue
                    for item in origin.get('localStorage', []) or []:
                        if isinstance(item, dict):
                            payload = dict(item)
                            payload['scope'] = str(origin.get('origin') or '')
                            storage_items.append(payload)
            except Exception:
                pass

        frames = []
        if page is not None:
            try:
                frames = list(page.frames)
            except Exception:
                frames = []

        for frame in frames:
            try:
                page_items = frame.evaluate(
                    """
                    () => {
                      const dump = (store, scope) => {
                        const items = [];
                        try {
                          for (let i = 0; i < store.length; i += 1) {
                            const key = store.key(i);
                            items.push({ scope, key, value: store.getItem(key) });
                          }
                        } catch (e) {}
                        return items;
                      };
                      return [...dump(window.localStorage, 'localStorage'), ...dump(window.sessionStorage, 'sessionStorage')];
                    }
                    """
                )
                storage_items.extend(page_items or [])
            except Exception:
                pass

        return self._qq_extract_music_auth_from_storage(storage_items)

    def _qq_probe_music_auth_context(self, page, cookie_map: dict[str, str] | None = None) -> None:
        if page is None:
            return
        normalized_uin = self._normalize_qq_uin((cookie_map or {}).get('uin') or (cookie_map or {}).get('p_uin'))
        probe_urls = [
            'https://y.qq.com/',
            'https://y.qq.com/n/ryqq/index.html',
            'https://y.qq.com/portal/profile.html',
        ]
        if normalized_uin and normalized_uin != '0':
            probe_urls.extend(
                [
                    f'https://y.qq.com/n/ryqq/profile/{normalized_uin}',
                    f'https://y.qq.com/portal/profile.html?uin={normalized_uin}',
                ]
            )
        seen: set[str] = set()
        for url in probe_urls:
            target = str(url or '').strip()
            if not target or target in seen:
                continue
            seen.add(target)
            try:
                page.goto(target, wait_until='domcontentloaded', timeout=20000)
            except Exception:
                continue
            try:
                page.wait_for_timeout(1200)
            except Exception:
                time.sleep(1.2)
            try:
                page.evaluate(
                    """
                    async (uin) => {
                      const tasks = [];
                      const profileUrl = uin && uin !== '0'
                        ? `https://c.y.qq.com/rsc/fcgi-bin/fcg_get_profile_homepage.fcg?format=json&hostuin=${uin}&loginUin=${uin}&needNewCode=0`
                        : 'https://c.y.qq.com/rsc/fcgi-bin/fcg_get_profile_homepage.fcg?format=json&needNewCode=0';
                      tasks.push(fetch(profileUrl, { credentials: 'include', mode: 'no-cors' }).catch(() => null));
                      tasks.push(fetch('https://c.y.qq.com/splcloud/fcgi-bin/fcg_musiclist_getmyfav.fcg?format=json&needNewCode=0', { credentials: 'include', mode: 'no-cors' }).catch(() => null));
                      await Promise.all(tasks);
                      return true;
                    }
                    """,
                    normalized_uin,
                )
            except Exception:
                pass
            try:
                page.wait_for_timeout(800)
            except Exception:
                time.sleep(0.8)

    def _qq_build_requests_session_from_context(self, context) -> requests.Session:
        session = requests.Session()
        session.headers.update({'User-Agent': 'Mozilla/5.0', 'Referer': 'https://xui.ptlogin2.qq.com/'})
        try:
            raw_cookies = context.cookies() if context is not None else []
        except Exception:
            raw_cookies = []
        for item in raw_cookies or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get('name') or '').strip()
            value = str(item.get('value') or '').strip()
            domain = str(item.get('domain') or '').strip() or None
            if name and value:
                session.cookies.set(name, value, domain=domain)
        return session

    def _qq_poll_login_result(self, context) -> tuple[str, str, str, str, dict[str, str]]:
        session = self._qq_build_requests_session_from_context(context)
        cookie_map = session.cookies.get_dict() or {}
        qrsig = str(cookie_map.get('qrsig') or '').strip()
        if not qrsig:
            return '', '', '', '', cookie_map
        ptqrtoken = str(self._qq_ptqrtoken(qrsig))
        headers = {'Referer': 'https://xui.ptlogin2.qq.com/', 'User-Agent': 'Mozilla/5.0'}
        try:
            response = session.get(
                self._QQ_PTQRLOGIN_URL,
                params=self._qq_ptqrlogin_params(ptqrtoken),
                headers=headers,
                timeout=self._QQ_LOGIN_TIMEOUT,
            )
            raw_text = str(response.text or '').strip()
        except Exception:
            return '', '', '', '', cookie_map
        code, redirect_url, message, nickname = self._parse_qq_login_cb(raw_text)
        if code == '0' and redirect_url:
            self._qq_sync_login_session(session, headers, redirect_url)
            cookie_map = session.cookies.get_dict() or cookie_map
        return code, redirect_url, message, nickname, cookie_map

    @staticmethod
    def _qq_login_qrcode_path() -> Path:
        return _PROJECT_ROOT / 'logs' / 'qqmusic_login_qrcode.png'

    def _qq_clear_login_qrcode(self) -> None:
        try:
            self._qq_login_qrcode_path().unlink(missing_ok=True)
        except Exception:
            pass

    @staticmethod
    def _qq_first_visible(candidates, timeout: int = 1200):
        for locator in candidates:
            try:
                locator.wait_for(state='visible', timeout=timeout)
                box = locator.bounding_box()
                if box and float(box.get('width') or 0) >= 12 and float(box.get('height') or 0) >= 12:
                    return locator
            except Exception:
                continue
        return None

    def _qq_find_login_entry(self, page):
        text_regex = re.compile(r'登录|立即登录|去登录|QQ登录|扫码登录|二维码登录|login|sign in|scan', re.I)
        primary_candidates = [
            page.get_by_role('button', name=text_regex).first,
            page.get_by_role('link', name=text_regex).first,
            page.get_by_text(text_regex).first,
            page.locator(
                "header [class*='login'], nav [class*='login'], [class*='top_login'], "
                "[class*='sign'], [data-testid*='login'], [data-event*='login']"
            ).first,
        ]
        primary = self._qq_first_visible(primary_candidates, timeout=1500)
        if primary is not None:
            return primary

        group = page.locator("button, a, [role='button'], [class*='login'], [class*='sign']")
        try:
            count = min(group.count(), 24)
        except Exception:
            return None

        for idx in range(count):
            locator = group.nth(idx)
            try:
                locator.wait_for(state='visible', timeout=300)
                text_content = (locator.text_content() or '').strip()
                aria_label = (locator.get_attribute('aria-label') or '').strip()
                title = (locator.get_attribute('title') or '').strip()
                class_name = (locator.get_attribute('class') or '').strip().lower()
                hint = ' '.join(part for part in (text_content, aria_label, title, class_name) if part)
                if text_regex.search(hint) or 'login' in class_name or 'sign' in class_name:
                    return locator
            except Exception:
                continue
        return None

    def _qq_try_switch_scan_tab(self, page) -> bool:
        iframe = self._qq_login_frame_locator(page)
        ready_candidates = [
            iframe.locator('#qrlogin_img').first,
            iframe.locator('#qlogin').first,
            iframe.locator('#qr_area').first,
        ]
        for locator in ready_candidates:
            try:
                locator.wait_for(state='visible', timeout=500)
                return True
            except Exception:
                continue

        scan_regex = re.compile(r'扫码登录|二维码登录|QQ扫码登录|手机扫码登录|扫码授权|二维码|扫码|切换扫码', re.I)
        candidates = [
            page.locator('.yqq-dialog-wrap .login-box-tit__item').filter(has_text=scan_regex).first,
            page.locator(".yqq-dialog-wrap [role='tab']").filter(has_text=scan_regex).first,
            iframe.get_by_role('tab', name=scan_regex).first,
            iframe.get_by_role('button', name=scan_regex).first,
            iframe.get_by_text(scan_regex).first,
        ]
        clicked = False
        for locator in candidates:
            try:
                locator.wait_for(state='visible', timeout=600)
                locator.click(force=True, timeout=1500)
                clicked = True
                try:
                    page.wait_for_timeout(800)
                except Exception:
                    time.sleep(0.8)
            except Exception:
                continue
        return clicked

    def _qq_try_click_account_provider(self, page) -> bool:
        provider_regex = re.compile(r'^QQ登录$|^QQ账号登录$|^使用QQ登录$|^使用QQ账号登录$', re.I)
        iframe = self._qq_login_frame_locator(page)
        ready_candidates = [
            iframe.locator('#qrlogin_img').first,
            iframe.locator('#qlogin').first,
            iframe.locator('#qr_area').first,
        ]
        for locator in ready_candidates:
            try:
                locator.wait_for(state='visible', timeout=500)
                return True
            except Exception:
                continue

        candidates = [
            page.locator('.yqq-dialog-wrap .login-box-tit__item').filter(has_text=provider_regex).first,
            page.locator(".yqq-dialog-wrap [role='tab']").filter(has_text=provider_regex).first,
            page.locator(".yqq-dialog-wrap a.login-box-tit__item.current").first,
            page.locator(".yqq-dialog-wrap a.login-box-tit__item").filter(has_text='QQ登录').first,
        ]
        clicked = False
        for locator in candidates:
            try:
                locator.wait_for(state='visible', timeout=800)
                locator.click(force=True, timeout=1800)
                clicked = True
                try:
                    page.wait_for_timeout(900)
                except Exception:
                    time.sleep(0.9)
                break
            except Exception:
                continue
        return clicked

    @staticmethod
    def _qq_login_frame_locator(page):
        outer = page.frame_locator(
            "iframe#login_frame, "
            "iframe.login-box-bd__ifr_qq, "
            "iframe[src*='graph.qq.com/oauth2.0/authorize'], "
            "iframe[src*='graph.qq.com/oauth2.0/show']"
        )
        return outer.frame_locator(
            "iframe#ptlogin_iframe, "
            "iframe[name='ptlogin_iframe'], "
            "iframe[src*='xui.ptlogin2.qq.com/cgi-bin/xlogin']"
        )

    def _qq_prepare_official_login_flow(self, page) -> bool:
        clicked_any = False
        login_button = self._qq_find_login_entry(page)
        if login_button is not None:
            try:
                login_button.scroll_into_view_if_needed()
            except Exception:
                pass
            try:
                login_button.click(timeout=5000)
                clicked_any = True
            except Exception:
                try:
                    login_button.click(force=True, timeout=2500)
                    clicked_any = True
                except Exception:
                    pass
            if clicked_any:
                try:
                    page.wait_for_timeout(1200)
                except Exception:
                    time.sleep(1.2)

        if self._qq_try_click_account_provider(page):
            clicked_any = True
        if self._qq_try_switch_scan_tab(page):
            clicked_any = True
        return clicked_any

    def _qq_try_confirm_authorization(self, page) -> bool:
        return False

    def _qq_login_frame_text(self, page) -> str:
        iframe = self._qq_login_frame_locator(page)
        text_parts: list[str] = []
        for locator in (
            iframe.locator('body').first,
            iframe.locator('#qlogin').first,
            iframe.locator('#login').first,
            iframe.locator('#authorize').first,
            iframe.locator('#accredit_info').first,
        ):
            try:
                locator.wait_for(state='attached', timeout=300)
                text = str(locator.text_content() or '').strip()
                if text:
                    text_parts.append(text)
            except Exception:
                continue
        return '\n'.join(text_parts)

    def _qq_login_frame_state(self, page) -> str:
        text = self._qq_login_frame_text(page)
        if not text:
            return ''
        if any(token in text for token in ('登录成功', '授权成功', '正在跳转', '跳转中', '已成功登录')):
            return 'success'
        if any(token in text for token in ('请在手机上确认', '扫描成功', '扫码成功', '请确认登录')):
            return 'scanned'
        if any(token in text for token in ('二维码失效', '请点击刷新', '已过期')):
            return 'expired'
        if any(token in text for token in ('授权即同意', '获取以下权限', '授权登录', '同意授权')):
            return 'authorize'
        if any(token in text for token in ('扫码登录', '二维码登录', '使用QQ手机版扫码登录')):
            return 'qrcode'
        return ''

    @staticmethod
    def _qq_choose_best_qrcode_locator(group):
        best_locator = None
        best_score = -1.0
        try:
            count = min(group.count(), 20)
        except Exception:
            return None

        for idx in range(count):
            locator = group.nth(idx)
            try:
                locator.wait_for(state='visible', timeout=500)
                box = locator.bounding_box()
                if not box:
                    continue
                width = float(box.get('width') or 0.0)
                height = float(box.get('height') or 0.0)
                if width < 60 or height < 60:
                    continue
                ratio = max(width, height) / max(1.0, min(width, height))
                if ratio > 1.35:
                    continue

                text_content = (locator.text_content() or '').strip().lower()
                class_name = ((locator.get_attribute('class')) or '').strip().lower()
                alt_text = ((locator.get_attribute('alt')) or '').strip().lower()
                src = ((locator.get_attribute('src')) or '').strip().lower()
                hint = ' '.join(part for part in (text_content, class_name, alt_text, src) if part)
                if any(token in hint for token in ('logo', 'icon', 'avatar', 'close', 'cover', 'album', 'song', 'poster', 'mv', 'background', 'bg')):
                    continue

                score = width * height
                if any(token in hint for token in ('qr', 'qrcode', 'code', 'scan', '二维码', '扫码', 'ptqrshow')):
                    score += 1_000_000
                if score > best_score:
                    best_score = score
                    best_locator = locator
            except Exception:
                continue
        return best_locator

    def _qq_find_qrcode_locator(self, page):
        iframe = self._qq_login_frame_locator(page)
        upstream_candidates = [
            iframe.locator('#qrlogin_img').first,
            iframe.locator('#qr_area').first,
            iframe.locator('.qrImg').first,
            iframe.locator("img[src*='ptqrshow'], img[src*='qrcode'], img[src*='qr']").first,
            iframe.locator('canvas').first,
            page.locator("img[src*='ptqrshow'], img[src*='qrcode'], img[src*='qr']").first,
            page.locator('canvas').first,
        ]

        locator = self._qq_first_visible(upstream_candidates, timeout=1200)
        if locator is not None:
            box = locator.bounding_box()
            if box and float(box.get('width') or 0) >= 60 and float(box.get('height') or 0) >= 60:
                return locator

        groups = (
            iframe.locator("#qrlogin_img, #qr_area, .qrImg, .qrlogin_img_out, .qlogin_show, img, canvas, svg, [class*='qr'], [class*='code'], [id*='qr'], [id*='code']"),
            page.locator(
                "[role='dialog'] img, [role='dialog'] canvas, [role='dialog'] svg, "
                "[role='dialog'] [class*='qr'], [role='dialog'] [class*='code'], "
                "img, canvas, svg, [class*='qr'], [class*='code'], [id*='qr'], [id*='code']"
            ),
        )
        for group in groups:
            best = self._qq_choose_best_qrcode_locator(group)
            if best is not None:
                return best
        return None

    def _qq_wait_for_qrcode_locator(self, page, timeout_ms: int = 15000):
        deadline = time.monotonic() + max(1.0, timeout_ms / 1000.0)
        scan_switched = False
        while time.monotonic() < deadline:
            locator = self._qq_find_qrcode_locator(page)
            if locator is not None:
                return locator
            if not scan_switched:
                scan_switched = self._qq_try_switch_scan_tab(page)
            time.sleep(0.4)
        return None

    @staticmethod
    def _qq_decode_data_url_bytes(data_url: str) -> bytes | None:
        text = str(data_url or '').strip()
        if not text.startswith('data:image') or ',' not in text:
            return None
        try:
            _, encoded = text.split(',', 1)
            return base64.b64decode(encoded)
        except Exception:
            return None

    @staticmethod
    def _qq_crop_qr_image_bytes(data: bytes) -> bytes | None:
        raw = bytes(data or b'')
        if not raw:
            return None
        try:
            import cv2
            import numpy as np
        except Exception:
            return None

        try:
            img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        except Exception:
            img = None
        if img is None:
            return None

        detector = cv2.QRCodeDetector()

        def _crop(points) -> bytes | None:
            if points is None:
                return None
            pts = np.array(points, dtype=np.float32).reshape(-1, 2)
            if pts.size < 8:
                return None
            min_x = max(0, int(np.floor(np.min(pts[:, 0]))) - 16)
            min_y = max(0, int(np.floor(np.min(pts[:, 1]))) - 16)
            max_x = min(img.shape[1], int(np.ceil(np.max(pts[:, 0]))) + 16)
            max_y = min(img.shape[0], int(np.ceil(np.max(pts[:, 1]))) + 16)
            if max_x <= min_x or max_y <= min_y:
                return None
            cropped = img[min_y:max_y, min_x:max_x]
            if cropped.size == 0:
                return None
            ok, encoded = cv2.imencode('.png', cropped)
            if not ok:
                return None
            return bytes(encoded)

        try:
            ok, decoded_info, points, _ = detector.detectAndDecodeMulti(img)
        except Exception:
            ok, decoded_info, points = False, (), None
        if ok and points is not None and len(points):
            best_idx = 0
            best_area = -1.0
            for idx, quad in enumerate(points):
                quad_np = np.array(quad, dtype=np.float32).reshape(-1, 2)
                area = float(cv2.contourArea(quad_np.astype(np.int32)))
                if area > best_area and str((decoded_info[idx] if idx < len(decoded_info) else '') or '').strip():
                    best_area = area
                    best_idx = idx
            cropped = _crop(points[best_idx])
            if cropped:
                return cropped

        try:
            decoded, points, _ = detector.detectAndDecode(img)
        except Exception:
            decoded, points = '', None
        if str(decoded or '').strip() and points is not None:
            return _crop(points)
        return None

    def _qq_fetch_locator_image_bytes(self, page, locator) -> bytes | None:
        if locator is None:
            return None
        try:
            tag_name = str(locator.evaluate("el => (el.tagName || '').toLowerCase()") or '').strip().lower()
        except Exception:
            tag_name = ''

        if tag_name == 'img':
            try:
                src = str(locator.get_attribute('src') or '').strip()
            except Exception:
                src = ''
            if src:
                data = self._qq_decode_data_url_bytes(src)
                if data:
                    return data
            try:
                data = locator.screenshot(type='png')
                if data:
                    return data
            except Exception:
                pass

        if tag_name == 'canvas':
            try:
                data_url = str(locator.evaluate("el => el.toDataURL('image/png')") or '').strip()
            except Exception:
                data_url = ''
            return self._qq_decode_data_url_bytes(data_url)

        try:
            return locator.screenshot(type='png')
        except Exception:
            return None

    def _qq_capture_qrcode_png(self, page, locator=None) -> bytes | None:
        png_bytes = None
        candidates = []
        if locator is not None:
            candidates.append(locator)
        iframe = self._qq_login_frame_locator(page)
        candidates.extend([
            iframe.locator('#qrlogin_img').first,
            iframe.locator('#qr_area').first,
            iframe.locator('.qrImg').first,
            iframe.locator("img[src*='ptqrshow'], img[src*='qrcode'], img[src*='qr'], canvas, svg").first,
            page.locator("[role='dialog'] img[src*='ptqrshow'], [role='dialog'] img[src*='qrcode'], [role='dialog'] img[src*='qr'], [role='dialog'] canvas, [role='dialog'] svg").first,
        ])

        for candidate in candidates:
            try:
                candidate.wait_for(state='visible', timeout=1000)
                raw_bytes = self._qq_fetch_locator_image_bytes(page, candidate)
                png_bytes = self._qq_crop_qr_image_bytes(raw_bytes or b'')
                if not png_bytes and raw_bytes:
                    try:
                        hint = ' '.join(
                            str(candidate.get_attribute(name) or '').strip().lower()
                            for name in ('src', 'class', 'alt', 'id')
                        )
                    except Exception:
                        hint = ''
                    if any(token in hint for token in ('ptqrshow', 'qrcode', 'qrlogin', 'qr_code', 'qrsig')):
                        png_bytes = raw_bytes
                if png_bytes:
                    break
            except Exception:
                continue

        if not png_bytes:
            try:
                dialog = page.locator("[role='dialog']").first
                dialog.wait_for(state='visible', timeout=1000)
                raw_bytes = dialog.screenshot(type='png')
                png_bytes = self._qq_crop_qr_image_bytes(raw_bytes or b'')
            except Exception:
                png_bytes = None

        if not png_bytes:
            try:
                raw_bytes = page.screenshot(type='png', full_page=False)
                png_bytes = self._qq_crop_qr_image_bytes(raw_bytes or b'')
            except Exception:
                png_bytes = None

        if png_bytes:
            try:
                path = self._qq_login_qrcode_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(png_bytes)
            except Exception:
                pass
        return png_bytes or None

    def _qq_current_qrcode_signature(self, page) -> str:
        iframe = self._qq_login_frame_locator(page)
        candidates = [
            iframe.locator('#qrlogin_img').first,
            iframe.locator('.qrImg').first,
            iframe.locator("img[src*='ptqrshow'], img[src*='qrcode'], img[src*='qr']").first,
        ]
        for locator in candidates:
            try:
                locator.wait_for(state='visible', timeout=500)
                src = str(locator.get_attribute('src') or '').strip()
                if src:
                    return src
            except Exception:
                continue
        return ''

    def _qq_publish_qrcode_snapshot(self, page, status: str, locator=None, title: str = 'QQ音乐扫码登录') -> bool:
        qr_png = self._qq_capture_qrcode_png(page, locator=locator)
        if not qr_png:
            return False
        self._publish_qr_show(qr_png, status, title=title)
        return True

    def _qq_refresh_expired_qrcode(self, page) -> bool:
        refresh_regex = re.compile(r'二维码已失效|二维码过期|已过期|刷新|重新获取|重试|retry|refresh|expired', re.I)
        iframe = self._qq_login_frame_locator(page)
        candidates = [
            page.get_by_role('button', name=refresh_regex).first,
            page.get_by_text(refresh_regex).first,
            iframe.get_by_role('button', name=refresh_regex).first,
            iframe.get_by_text(refresh_regex).first,
            iframe.locator('#qr_area').first,
            iframe.locator('#qlogin_show').first,
            iframe.locator('.qrlogin_img_out').first,
            page.locator("[class*='refresh'], [aria-label*='refresh' i], [title*='refresh' i], [aria-label*='刷新'], [title*='刷新']").first,
            iframe.locator("[class*='refresh'], [aria-label*='refresh' i], [title*='refresh' i], [aria-label*='刷新'], [title*='刷新']").first,
        ]
        for locator in candidates:
            try:
                locator.wait_for(state='visible', timeout=600)
                locator.click(force=True, timeout=1500)
                try:
                    page.wait_for_timeout(1000)
                except Exception:
                    time.sleep(1.0)
                return True
            except Exception:
                continue
        return False

    # ------------------------------------------------------------------
    # Cookie 管理
    # ------------------------------------------------------------------

    def _clear_runtime_login_cookies(self):
        """清空当前进程内 pyncm 会话 Cookie，解决同名 Cookie 冲突。"""
        try:
            from pyncm.apis.login import GetCurrentSession
        except Exception:
            return

        try:
            session = GetCurrentSession()
        except Exception as e:
            logger.debug('[CloudMusic] 获取当前会话失败，无法清理 Cookie: %s', e)
            return

        jar = getattr(session, 'cookies', None)
        if jar is None:
            return

        try:
            jar.clear()
            return
        except Exception:
            pass

        # 兼容少数 CookieJar 实现 clear() 失败的场景。
        try:
            for cookie in list(jar):
                try:
                    jar.clear(domain=cookie.domain, path=cookie.path, name=cookie.name)
                except Exception:
                    continue
        except Exception as e:
            logger.debug('[CloudMusic] Cookie 逐项清理失败: %s', e)

    def _call_with_cookie_recover(self, func, *args, **kwargs):
        """
        调用 pyncm 接口；若遇到 __csrf 多 Cookie 冲突则清理后重试一次。
        """
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if not self._is_cookie_conflict_error(e):
                raise
            logger.warning('[CloudMusic] 检测到 __csrf Cookie 冲突，清理后重试: %s', e)
            self._clear_runtime_login_cookies()
            return func(*args, **kwargs)

    def _login_call_timeout(self) -> float:
        try:
            v = float(TIMEOUTS.get('login_call', 12))
        except (TypeError, ValueError):
            v = 12.0
        return max(2.0, min(60.0, v))

    def _safe_login_call(self, func, *args, **kwargs):
        done = threading.Event()
        out = {}
        err = {}
        timeout_s = self._login_call_timeout()

        def _run():
            try:
                out['v'] = self._call_with_cookie_recover(func, *args, **kwargs)
            except Exception as e:
                err['e'] = e
            finally:
                done.set()

        threading.Thread(target=_run, daemon=True, name='cm-login-call').start()
        if not done.wait(timeout=timeout_s):
            name = getattr(func, '__name__', 'login_call')
            raise TimeoutError(f'{name} 超时 ({timeout_s:.1f}s)')
        if 'e' in err:
            raise err['e']
        return out.get('v')

    # ------------------------------------------------------------------
    # 登录缓存
    # ------------------------------------------------------------------

    def _clear_login_cache(self):
        try:
            _LOGIN_CACHE_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    def _save_login_cache(self) -> bool:
        """保存登录 cookies 到项目根目录，便于用户手动删除。"""
        if not self.provider_logged_in('netease'):
            return False
        try:
            from pyncm.apis.login import GetCurrentSession
            cookies = GetCurrentSession().cookies.get_dict() or {}
            if not cookies:
                return False
            payload = {
                'version': 1,
                'saved_at': int(time.time()),
                'cookies': cookies,
            }
            with open(_LOGIN_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            logger.info('[CloudMusic] 登录缓存已写入: %s', _LOGIN_CACHE_FILE)
            return True
        except Exception as e:
            logger.warning('[CloudMusic] 保存登录缓存失败: %s', e)
            return False

    def _restore_login_from_cache(self) -> bool:
        """从项目根目录缓存恢复登录。"""
        if not _LOGIN_CACHE_FILE.exists():
            return False
        try:
            with open(_LOGIN_CACHE_FILE, 'r', encoding='utf-8') as f:
                payload = json.load(f)
            cookies = payload.get('cookies') if isinstance(payload, dict) else None
            if not isinstance(cookies, dict) or not cookies:
                self._clear_login_cache()
                return False

            from pyncm.apis.login import LoginViaCookie, GetCurrentLoginStatus
            self._clear_runtime_login_cookies()
            self._safe_login_call(LoginViaCookie, **cookies)
            status = self._safe_login_call(GetCurrentLoginStatus)
            if self._is_account_logged_in(status):
                self._set_login_state(True, self._profile_from_status(status), provider='netease')
                logger.info('[CloudMusic] 已从缓存恢复账号登录')
                return True

            logger.info('[CloudMusic] 登录缓存已失效，回退匿名登录')
            self._clear_login_cache()
            return False
        except Exception as e:
            logger.warning('[CloudMusic] 恢复登录缓存失败: %s', e)
            self._clear_login_cache()
            return False

    def _clear_qq_login_cache(self):
        try:
            _QQ_LOGIN_CACHE_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    def _save_qq_login_cache(self) -> bool:
        try:
            cookies = get_qqmusic_provider_client().export_cookies()
            if not cookies:
                return False
            payload = {
                'version': 1,
                'saved_at': int(time.time()),
                'cookies': cookies,
            }
            with open(_QQ_LOGIN_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            logger.info('[CloudMusic] QQ 登录缓存已写入: %s', _QQ_LOGIN_CACHE_FILE)
            return True
        except Exception as e:
            logger.warning('[CloudMusic] 保存 QQ 登录缓存失败: %s', e)
            return False

    def _restore_qq_login_from_cache(self) -> bool:
        if not _QQ_LOGIN_CACHE_FILE.exists():
            return False
        try:
            with open(_QQ_LOGIN_CACHE_FILE, 'r', encoding='utf-8') as f:
                payload = json.load(f)
            cookies = payload.get('cookies') if isinstance(payload, dict) else None
            if not isinstance(cookies, dict) or not cookies:
                self._clear_qq_login_cache()
                return False
            client = get_qqmusic_provider_client()
            client.set_cookies({str(k): str(v) for k, v in cookies.items() if v is not None})
            if not client.is_logged_in():
                logger.warning('[CloudMusic] QQ 缓存登录态无有效鉴权 Cookie，清理缓存并回退未登录')
                self._clear_qq_login_cache()
                self._set_login_state(False, {}, provider='qq')
                return False
            self._set_login_state(True, {'nickname': 'QQ账号'}, provider='qq')
            if not self._qq_has_music_auth_cookies(client.get_session()):
                logger.warning('[CloudMusic] 已恢复 QQ 基础登录态，但缺少 QQ 音乐专用 Cookie；喜欢歌单需重新浏览器登录')
            else:
                logger.info('[CloudMusic] 已从缓存恢复 QQ 登录态')
            return True
        except Exception as e:
            logger.warning('[CloudMusic] 恢复 QQ 登录缓存失败: %s', e)
            self._clear_qq_login_cache()
            return False

    def _clear_kugou_login_cache(self):
        try:
            _KUGOU_LOGIN_CACHE_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    def _save_kugou_login_cache(self) -> bool:
        try:
            cookies = get_kugou_provider_client().export_cookies()
            if not cookies:
                return False
            payload = {
                'version': 1,
                'saved_at': int(time.time()),
                'cookies': cookies,
            }
            with open(_KUGOU_LOGIN_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            logger.info('[CloudMusic] 酷狗登录缓存已写入: %s', _KUGOU_LOGIN_CACHE_FILE)
            return True
        except Exception as e:
            logger.warning('[CloudMusic] 保存酷狗登录缓存失败: %s', e)
            return False

    def _restore_kugou_login_from_cache(self) -> bool:
        if not _KUGOU_LOGIN_CACHE_FILE.exists():
            return False
        try:
            with open(_KUGOU_LOGIN_CACHE_FILE, 'r', encoding='utf-8') as f:
                payload = json.load(f)
            cookies = payload.get('cookies') if isinstance(payload, dict) else None
            if not isinstance(cookies, dict) or not cookies:
                self._clear_kugou_login_cache()
                return False

            client = get_kugou_provider_client()
            client.set_cookies({str(k): str(v) for k, v in cookies.items() if v is not None})
            if client.is_logged_in():
                uid = str(cookies.get('userid') or '').strip()
                nickname = f"酷狗用户({uid[-4:]})" if uid else "酷狗用户"
                self._set_login_state(True, {'nickname': nickname}, provider='kugou')
                logger.info('[CloudMusic] 已从缓存恢复酷狗登录态')
                return True

            logger.info('[CloudMusic] 酷狗登录缓存已失效')
            self._clear_kugou_login_cache()
            return False
        except Exception as e:
            logger.warning('[CloudMusic] 恢复酷狗登录缓存失败: %s', e)
            self._clear_kugou_login_cache()
            return False

    # ------------------------------------------------------------------
    # 登录方式
    # ------------------------------------------------------------------

    def _anonymous_login(self):
        """执行匿名登录（用于播放功能兜底）。"""
        from pyncm.apis.login import LoginViaAnonymousAccount, GetCurrentLoginStatus
        t0 = time.monotonic()
        self._clear_runtime_login_cookies()
        t1 = time.monotonic()
        self._safe_login_call(LoginViaAnonymousAccount)
        t2 = time.monotonic()
        status = self._safe_login_call(GetCurrentLoginStatus)
        t3 = time.monotonic()
        self._set_login_state(
            self._is_account_logged_in(status),
            self._profile_from_status(status),
            provider='netease',
        )
        logger.info("[CloudMusic] 匿名登录完成")
        logger.debug(
            "[CloudMusic] 匿名登录耗时: clear=%.3fs login=%.3fs status=%.3fs total=%.3fs",
            t1 - t0,
            t2 - t1,
            t3 - t2,
            t3 - t0,
        )

    # ------------------------------------------------------------------
    # 二维码生成
    # ------------------------------------------------------------------

    def _create_qr_png(self, qr_url: str) -> bytes | None:
        """将二维码 URL 渲染成 PNG 二进制。"""
        try:
            import io
            import qrcode

            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=10,
                border=2,
            )
            qr.add_data(qr_url)
            qr.make(fit=True)
            img = qr.make_image(fill_color='black', back_color='white')
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            return buf.getvalue()
        except Exception as e:
            logger.debug('[CloudMusic] 本地二维码生成失败，尝试在线兜底: %s', e)

        try:
            online = f'https://api.qrserver.com/v1/create-qr-code/?size=320x320&data={quote_plus(qr_url)}'
            resp = requests.get(online, timeout=(8, 12))
            resp.raise_for_status()
            if resp.content:
                return resp.content
        except Exception as e:
            logger.warning('[CloudMusic] 在线二维码生成失败: %s', e)
        return None

    # ------------------------------------------------------------------
    # 二维码登录事件发布
    # ------------------------------------------------------------------

    def _publish_qr_show(self, qr_png: bytes | None, status: str, title: str = '音乐扫码登录'):
        self._ec.publish(Event(EventType.MUSIC_LOGIN_QR_SHOW, {
            'qr_png': qr_png,
            'status': status,
            'title': title,
        }))

    def _publish_qr_status(self, status: str, refresh_left: int | None = None):
        data = {'status': status}
        if refresh_left is not None:
            data['refresh_left'] = max(0, int(refresh_left))
        self._ec.publish(Event(EventType.MUSIC_LOGIN_QR_STATUS, data))

    def _publish_qr_hide(self):
        self._ec.publish(Event(EventType.MUSIC_LOGIN_QR_HIDE, {}))

    def _request_qr_payload(self, LoginQrcodeUnikey, GetLoginQRCodeUrl) -> tuple[str, bytes]:
        """申请新的二维码登录 key 并返回可展示 PNG。"""
        unikey_resp = LoginQrcodeUnikey()
        unikey = (
            (unikey_resp or {}).get('unikey')
            or (unikey_resp or {}).get('data', {}).get('unikey')
        )
        if not unikey:
            raise RuntimeError(f'获取二维码 key 失败: {unikey_resp}')

        qr_url = GetLoginQRCodeUrl(str(unikey))
        qr_png = self._create_qr_png(qr_url)
        if not qr_png:
            raise RuntimeError('二维码生成失败')

        return str(unikey), qr_png

    # ------------------------------------------------------------------
    # 登录事件处理
    # ------------------------------------------------------------------

    def _on_login_request(self, event: Event):
        """处理登录按钮点击：发起二维码登录流程。"""
        if self._is_qq_provider():
            self._on_qq_login_request()
            return
        if self._is_kugou_provider():
            self._on_kugou_login_request()
            return

        already_logged_in = self.provider_logged_in('netease')
        if already_logged_in:
            self._show_info('网易云账号已登录')
            return

        if self._qr_login_thread is not None and self._qr_login_thread.is_alive():
            self._show_info('二维码登录进行中，请完成手机确认')
            return

        self._qr_login_cancel.clear()
        self._qr_login_thread = threading.Thread(
            target=self._qr_login_worker,
            daemon=True,
            name='cm-qr-login',
        )
        self._qr_login_thread.start()

    def _on_login_cancel_request(self, event: Event):
        """处理退出扫码请求：仅取消当前扫码流程，不执行账号登出。"""
        self._qr_login_cancel.set()
        self._publish_qr_hide()
        if self._qr_login_thread is not None and self._qr_login_thread.is_alive():
            self._show_info('已退出扫码登录')

    def _on_logout_request(self, event: Event):
        """处理退出登录请求：退出网易云账号并清除本地登录缓存。"""
        if self._is_qq_provider():
            self._on_qq_logout_request()
            return
        if self._is_kugou_provider():
            self._on_kugou_logout_request()
            return

        # 终止可能存在的二维码登录流程，并关闭二维码弹窗。
        self._qr_login_cancel.set()
        self._publish_qr_hide()
        threading.Thread(
            target=self._logout_worker,
            daemon=True,
            name='cm-logout',
        ).start()

    def _logout_worker(self):
        t0 = time.monotonic()

        try:
            from pyncm.apis.login import LoginLogout
            self._safe_login_call(LoginLogout)
        except ImportError:
            logger.warning('[CloudMusic] pyncm 未安装，跳过远端退出登录')
        except Exception as e:
            logger.warning('[CloudMusic] 远端退出登录失败: %s', e)

        self._clear_login_cache()

        # 退出账号后回退匿名态，保持音乐功能可用，同时 UI 应显示未登录。
        try:
            self._anonymous_login()
        except ImportError:
            self._set_login_state(False, {}, provider='netease')
        except Exception as e:
            logger.warning('[CloudMusic] 回退匿名登录失败: %s', e)
            self._set_login_state(False, {}, provider='netease')

        self._show_info('已退出网易云登录并清除缓存')
        logger.debug('[CloudMusic] 退出登录流程耗时: %.3fs', time.monotonic() - t0)

    def _on_qq_logout_request(self):
        self._qr_login_cancel.set()
        self._publish_qr_hide()
        threading.Thread(
            target=self._qq_logout_worker,
            daemon=True,
            name='cm-qq-logout',
        ).start()

    def _qq_logout_worker(self):
        try:
            get_qqmusic_provider_client().set_cookies({})
        except Exception:
            pass
        self._clear_qq_login_cache()
        self._set_login_state(False, {}, provider='qq')
        self._show_info('已退出QQ登录并清除缓存')

    def _on_kugou_logout_request(self):
        self._qr_login_cancel.set()
        self._publish_qr_hide()
        threading.Thread(
            target=self._kugou_logout_worker,
            daemon=True,
            name='cm-kugou-logout',
        ).start()

    def _kugou_logout_worker(self):
        try:
            get_kugou_provider_client().set_cookies({})
        except Exception:
            pass
        self._clear_kugou_login_cache()
        self._set_login_state(False, {}, provider='kugou')
        self._show_info('已退出酷狗登录并清除缓存')

    # ------------------------------------------------------------------
    # 二维码登录后台线程
    # ------------------------------------------------------------------

    def _on_kugou_login_request(self):
        already_logged_in = self.provider_logged_in('kugou')
        if already_logged_in:
            self._show_info('酷狗账号已登录')
            return

        if self._qr_login_thread is not None and self._qr_login_thread.is_alive():
            self._show_info('酷狗扫码登录进行中，请完成手机确认')
            return

        self._qr_login_cancel.clear()
        self._qr_login_thread = threading.Thread(
            target=self._kugou_login_worker,
            daemon=True,
            name='cm-kugou-qr-login',
        )
        self._qr_login_thread.start()

    def _on_qq_login_request(self):
        client = get_qqmusic_provider_client()
        already_logged_in = self.provider_logged_in('qq')
        has_music_auth = self._qq_has_music_auth_cookies(client.get_session())
        if already_logged_in and has_music_auth:
            self._show_info('QQ账号已登录')
            return

        if self._qr_login_thread is not None and self._qr_login_thread.is_alive():
            self._show_info('QQ登录进行中，请在浏览器中完成扫码或确认')
            return

        if already_logged_in and not has_music_auth:
            self._show_info('检测到当前QQ登录态缺少QQ音乐专用凭据，正在重新打开浏览器补齐')

        self._qr_login_cancel.clear()
        self._publish_qr_show(None, '正在启动QQ音乐登录流程，请稍候...', title='QQ音乐扫码登录')
        self._qr_login_thread = threading.Thread(
            target=self._qq_login_worker,
            daemon=True,
            name='cm-qq-browser-login',
        )
        self._qr_login_thread.start()

    def _qq_login_worker(self):
        """Run QQ Music browser login flow."""
        self._qq_browser_login_worker()

    def _qq_browser_login_worker(self):
        browser = None
        context = None
        page = None
        playwright = None
        last_cookie_map: dict[str, str] = {}
        captured_cookie_map: dict[str, str] = {}
        promoted = False
        should_hide_qr = False
        basic_auth_since: float | None = None
        last_qr_refresh_at: float | None = None
        last_qr_signature = ''
        last_qr_snapshot_at: float | None = None
        official_login_confirmed = False
        official_nickname = ''
        promotion_started_at: float | None = None
        try:
            try:
                from playwright.sync_api import sync_playwright
            except Exception as exc:
                raise RuntimeError('未安装 Playwright，无法启动内置浏览器登录') from exc

            client = get_qqmusic_provider_client()
            self._qq_clear_login_qrcode()
            self._publish_qr_show(None, '正在启动QQ音乐内置浏览器，请稍候...', title='QQ音乐扫码登录')
            self._publish_qr_status('正在启动QQ音乐内置浏览器...')
            self._show_info('正在启动QQ音乐官方网页登录流程，请在二维码窗口中扫码或确认登录')

            playwright = sync_playwright().start()
            browser = launch_playwright_chromium(
                playwright,
                headless=True,
                allow_visible_fallback=False,
            )

            context = browser.new_context(locale='zh-CN', viewport={'width': 1280, 'height': 960})
            page = context.new_page()

            def _merge_cookie_candidates(source: dict[str, str] | None) -> None:
                nonlocal last_cookie_map, captured_cookie_map
                if not isinstance(source, dict) or not source:
                    return
                captured_cookie_map.update({str(k): str(v) for k, v in source.items() if k and v is not None})
                merged_cookie_map = dict(last_cookie_map or {})
                merged_cookie_map.update(captured_cookie_map)
                last_cookie_map = merged_cookie_map
                client.set_cookies(last_cookie_map)

            def _on_request(request) -> None:
                try:
                    headers = request.all_headers()
                except Exception:
                    headers = getattr(request, 'headers', None) or {}
                _merge_cookie_candidates(self._qq_collect_network_cookie_map(headers))

            def _on_response(response) -> None:
                try:
                    header_items = response.headers_array()
                except Exception:
                    header_items = []
                _merge_cookie_candidates(parse_set_cookie_headers(header_items))

            context.on('request', _on_request)
            context.on('response', _on_response)
            self._publish_qr_status('正在打开QQ音乐页面...')
            page.goto('https://y.qq.com/n/ryqq/index.html', wait_until='domcontentloaded', timeout=30000)
            try:
                page.wait_for_timeout(1500)
            except Exception:
                time.sleep(1.5)

            self._publish_qr_status('正在点击QQ音乐登录入口...')
            if self._qq_prepare_official_login_flow(page):
                self._publish_qr_show(None, '二维码准备中，请稍候...', title='QQ音乐扫码登录')
                self._publish_qr_status('二维码准备中，请稍候...')

            self._publish_qr_show(None, '正在等待QQ音乐登录二维码...', title='QQ音乐扫码登录')
            self._publish_qr_status('正在等待QQ音乐登录二维码...')
            qrcode_locator = self._qq_wait_for_qrcode_locator(page, timeout_ms=20000)

            if qrcode_locator is None:
                self._publish_qr_show(None, '未拿到官网二维码，正在重试QQ音乐登录入口...', title='QQ音乐扫码登录')
                self._publish_qr_status('未拿到官网二维码，正在重试QQ音乐登录入口...')
                self._qq_prepare_official_login_flow(page)
                qrcode_locator = self._qq_wait_for_qrcode_locator(page, timeout_ms=20000)

            if qrcode_locator is None and not self._qq_publish_qrcode_snapshot(page, '请使用QQ扫码登录QQ音乐'):
                raise RuntimeError('未能在QQ音乐官网登录弹层中捕获二维码')

            if qrcode_locator is not None:
                self._qq_publish_qrcode_snapshot(page, '请使用QQ扫码登录QQ音乐', locator=qrcode_locator)
                last_qr_signature = self._qq_current_qrcode_signature(page)
                last_qr_snapshot_at = time.monotonic()
            last_qr_refresh_at = time.monotonic()

            self._publish_qr_status('二维码已生成，请使用QQ扫码登录')

            deadline = time.monotonic() + max(90, int(_QR_LOGIN_TIMEOUT))
            while time.monotonic() < deadline:
                if self._qr_login_cancel.is_set():
                    self._publish_qr_status('已取消QQ登录')
                    return

                current_qr_signature = self._qq_current_qrcode_signature(page)
                now = time.monotonic()
                if (not has_uin if 'has_uin' in locals() else True):
                    should_refresh_snapshot = False
                    if current_qr_signature and current_qr_signature != last_qr_signature:
                        should_refresh_snapshot = True
                    elif last_qr_snapshot_at is None or (now - last_qr_snapshot_at) >= 8.0:
                        should_refresh_snapshot = True
                    if should_refresh_snapshot:
                        qr_locator_for_update = self._qq_find_qrcode_locator(page)
                        if self._qq_publish_qrcode_snapshot(page, '请使用QQ扫码登录QQ音乐', locator=qr_locator_for_update):
                            last_qr_signature = current_qr_signature or last_qr_signature
                            last_qr_snapshot_at = now

                poll_code, poll_redirect_url, poll_message, poll_nickname, polled_cookie_map = self._qq_poll_login_result(context)
                if polled_cookie_map:
                    _merge_cookie_candidates(polled_cookie_map)

                if poll_code == '66':
                    self._publish_qr_status('等待扫码...')
                elif poll_code == '67':
                    self._publish_qr_status('已扫码，请在手机上确认登录')
                elif poll_code == '65':
                    self._publish_qr_status('二维码已失效，请稍候刷新')
                elif poll_code == '0':
                    official_login_confirmed = True
                    official_nickname = str(poll_nickname or '').strip()
                    self._publish_qr_status('QQ音乐授权成功，正在同步登录态...')
                    logger.info('[CloudMusic] QQ官方扫码确认成功，开始同步浏览器登录态')
                    synced_cookie_map = self._qq_sync_login_context(
                        context,
                        page,
                        poll_redirect_url,
                        seed_cookie_map=last_cookie_map,
                    )
                    if synced_cookie_map:
                        _merge_cookie_candidates(synced_cookie_map)
                    self._qq_probe_music_auth_context(page, last_cookie_map)
                    _merge_cookie_candidates(self._qq_collect_context_cookie_map(context))
                    _merge_cookie_candidates(self._qq_collect_document_cookie_map(page))
                    _merge_cookie_candidates(self._qq_collect_storage_state_map(page, context))
                    logger.info(
                        '[CloudMusic] QQ登录态同步结果 uin=%s auth=%s music_auth=%s',
                        self._qq_cookie_map_has_uin(last_cookie_map),
                        self._qq_cookie_map_has_auth(last_cookie_map),
                        self._qq_cookie_map_has_music_auth(last_cookie_map),
                        )

                frame_state = self._qq_login_frame_state(page)
                if not poll_code and frame_state == 'scanned':
                    self._publish_qr_status('已扫码，请在手机上确认登录')
                elif not official_login_confirmed and frame_state == 'authorize':
                    self._publish_qr_status('已扫码成功，正在等待授权确认...')
                elif not official_login_confirmed and frame_state == 'success':
                    official_login_confirmed = True
                    self._publish_qr_status('QQ音乐授权成功，正在同步登录态...')

                context_cookie_map = self._qq_collect_context_cookie_map(context)
                document_cookie_map = self._qq_collect_document_cookie_map(page)
                storage_state_map = self._qq_collect_storage_state_map(page, context)
                _merge_cookie_candidates(context_cookie_map)
                _merge_cookie_candidates(document_cookie_map)
                _merge_cookie_candidates(storage_state_map)

                has_uin = self._qq_cookie_map_has_uin(last_cookie_map)
                has_auth = self._qq_cookie_map_has_auth(last_cookie_map)
                has_music_auth = self._qq_cookie_map_has_music_auth(last_cookie_map)
                if has_uin and has_auth:
                    if basic_auth_since is None:
                        basic_auth_since = time.monotonic()

                if has_uin and has_music_auth:
                    nickname = official_nickname or self._qq_nickname_hint(client.get_session())
                    self._set_login_state(True, {'nickname': nickname}, provider='qq')
                    self._save_qq_login_cache()
                    should_hide_qr = True
                    self._publish_qr_status('QQ音乐登录成功，已同步完整权限')
                    self._show_info(self._login_success_message('QQ平台', nickname))
                    return

                if official_login_confirmed and not promoted:
                    promoted = True
                    promotion_started_at = time.monotonic()
                    self._publish_qr_status('已扫码成功，正在同步QQ音乐登录态...')
                    synced_cookie_map = self._qq_sync_login_context(
                        context,
                        page,
                        poll_redirect_url,
                        seed_cookie_map=last_cookie_map,
                    )
                    if synced_cookie_map:
                        _merge_cookie_candidates(synced_cookie_map)
                    self._qq_probe_music_auth_context(page, last_cookie_map)
                    _merge_cookie_candidates(self._qq_collect_context_cookie_map(context))
                    _merge_cookie_candidates(self._qq_collect_document_cookie_map(page))
                    _merge_cookie_candidates(self._qq_collect_storage_state_map(page, context))
                    continue

                if promoted and not has_music_auth:
                    elapsed = 0.0 if promotion_started_at is None else (time.monotonic() - promotion_started_at)
                    if elapsed >= 30.0:
                        raise RuntimeError('QQ已登录，但未拿到QQ音乐专用凭据，请重新登录后重试')
                    self._publish_qr_status('已扫码成功，正在等待QQ音乐权限补齐...')
                    if elapsed >= 1.5 and int(elapsed * 2) != int(max(0.0, elapsed - 0.5) * 2):
                        self._qq_probe_music_auth_context(page, last_cookie_map)
                        synced_cookie_map = self._qq_sync_login_context(
                            context,
                            page,
                            poll_redirect_url,
                            seed_cookie_map=last_cookie_map,
                        )
                        if synced_cookie_map:
                            _merge_cookie_candidates(synced_cookie_map)
                        _merge_cookie_candidates(self._qq_collect_context_cookie_map(context))
                        _merge_cookie_candidates(self._qq_collect_document_cookie_map(page))
                        _merge_cookie_candidates(self._qq_collect_storage_state_map(page, context))

                if not has_uin:
                    if last_qr_refresh_at is not None and (now - last_qr_refresh_at) >= _QQ_QR_AUTO_REFRESH_INTERVAL:
                        if self._qq_refresh_expired_qrcode(page):
                            last_qr_refresh_at = now
                            self._publish_qr_status('二维码已自动刷新，请重新扫码')
                            if self._qq_publish_qrcode_snapshot(page, '二维码已自动刷新，请重新扫码'):
                                last_qr_signature = self._qq_current_qrcode_signature(page)
                                last_qr_snapshot_at = now
                            time.sleep(0.8)
                            continue

                time.sleep(0.5)

            if self._qq_cookie_map_has_uin(last_cookie_map):
                raise RuntimeError('已登录QQ，但仍未拿到QQ音乐专用Cookie。请确认浏览器停留在QQ音乐页面后重试')
            raise RuntimeError('QQ浏览器登录超时，请重试')
        except Exception as e:
            logger.error('[CloudMusic] QQ浏览器登录失败: %s', e)
            self._publish_qr_status('QQ浏览器登录失败，请稍后重试')
            self._show_error(str(e) or 'QQ浏览器登录失败，请稍后重试')
        finally:
            try:
                if context is not None:
                    context.close()
            except Exception:
                pass
            try:
                if browser is not None:
                    browser.close()
            except Exception:
                pass
            try:
                if playwright is not None:
                    playwright.stop()
            except Exception:
                pass
            self._qq_clear_login_qrcode()
            self._publish_qr_hide()

    def _qq_legacy_login_worker(self):
        """Legacy QQ QR login is intentionally disabled."""
        raise RuntimeError('legacy qq qr login is disabled')

    def _kugou_login_worker(self):
        """后台执行酷狗二维码登录轮询。"""
        should_hide_qr = False
        try:
            client = get_kugou_provider_client()

            def _request_kugou_qr_payload(status_text: str, show_text: str) -> str:
                self._publish_qr_status(status_text)
                qr_payload = client.create_login_qr()
                qr_key = str(qr_payload.get('key') or '').strip()
                qr_png = qr_payload.get('qr_png')
                qr_url = str(qr_payload.get('qr_url') or '').strip()
                if not qr_key:
                    raise RuntimeError('酷狗二维码 key 获取失败')
                if not qr_png and qr_url:
                    qr_png = self._create_qr_png(qr_url)
                if not qr_png:
                    raise RuntimeError('酷狗二维码渲染失败')
                self._publish_qr_show(qr_png, show_text, title='酷狗扫码登录')
                return qr_key

            qr_key = _request_kugou_qr_payload('正在生成酷狗二维码...', '请使用酷狗音乐扫码登录')

            begin_ts = time.time()
            last_refresh_ts = begin_ts
            last_refresh_left: int | None = None
            while not self._qr_login_cancel.is_set():
                now_ts = time.time()
                if now_ts - begin_ts > _QR_LOGIN_TIMEOUT:
                    self._publish_qr_status('酷狗二维码超时，请重新点击登录')
                    self._show_info('酷狗二维码已超时，请重新点击登录音乐')
                    return

                result = client.poll_login_qr(qr_key)
                status = int(result.get('status', -1))
                if status == 1:
                    left = int(math.ceil(max(0.0, _QR_REFRESH_INTERVAL - (now_ts - last_refresh_ts))))
                    if left != last_refresh_left:
                        self._publish_qr_status('等待扫码...', refresh_left=left)
                        last_refresh_left = left
                    if (now_ts - last_refresh_ts) >= _QR_REFRESH_INTERVAL:
                        qr_key = _request_kugou_qr_payload('正在刷新酷狗二维码...', '酷狗二维码已自动刷新，请扫码登录')
                        last_refresh_ts = time.time()
                        last_refresh_left = None
                        continue
                elif status == 2:
                    self._publish_qr_status('已扫码，请在手机端确认登录')
                    last_refresh_left = None
                elif status == 0:
                    qr_key = _request_kugou_qr_payload('二维码已过期，正在自动刷新...', '酷狗二维码已自动刷新，请重新扫码')
                    last_refresh_ts = time.time()
                    last_refresh_left = None
                    continue
                elif status == 4:
                    token = str(result.get('token') or '').strip()
                    userid = str(result.get('userid') or '').strip()
                    if not client.set_login_token(token, userid):
                        self._publish_qr_status('酷狗登录状态异常，请重试')
                        self._show_error('酷狗登录状态异常，请重试')
                        return
                    self._publish_qr_status('酷狗授权成功，正在补齐网页登录态...')
                    login_meta = client.finalize_login()
                    if not bool(login_meta.get('ok')):
                        logger.warning('[CloudMusic] 酷狗登录补齐失败 meta=%s', login_meta)
                        self._publish_qr_status('酷狗登录态补齐失败，请重试')
                        self._show_error('酷狗登录态补齐失败，请重试')
                        return
                    nickname = str(result.get('nickname') or '').strip()
                    if not nickname:
                        nickname = f"酷狗用户({userid[-4:]})" if userid else "酷狗用户"
                    self._set_login_state(True, {'nickname': nickname}, provider='kugou')
                    self._save_kugou_login_cache()
                    self._show_info(self._login_success_message("酷狗平台", nickname))
                    should_hide_qr = True
                    return
                else:
                    msg = str(result.get('message') or '').strip()
                    self._publish_qr_status(msg or f'酷狗登录状态: {status}')
                    last_refresh_left = None

                time.sleep(_QR_POLL_INTERVAL)
        except Exception as e:
            logger.error('[CloudMusic] 酷狗二维码登录失败: %s', e)
            self._publish_qr_status('酷狗二维码登录失败，请稍后重试')
            self._show_error('酷狗二维码登录失败，请稍后重试')
        finally:
            if should_hide_qr or self._qr_login_cancel.is_set():
                self._publish_qr_hide()

    def _qr_login_worker(self):
        """后台执行二维码登录轮询。"""
        try:
            from pyncm.apis.login import (
                LoginQrcodeUnikey,
                GetLoginQRCodeUrl,
                LoginQrcodeCheck,
                GetCurrentLoginStatus,
            )

            self._publish_qr_status('正在生成二维码...')
            unikey, qr_png = self._request_qr_payload(LoginQrcodeUnikey, GetLoginQRCodeUrl)
            self._publish_qr_show(qr_png, '请使用网易云音乐扫码登录', title='网易云扫码登录')

            begin_ts        = time.time()
            last_refresh_ts = begin_ts
            last_code       = None
            last_refresh_left: int | None = None
            while not self._qr_login_cancel.is_set():
                now_ts = time.time()
                if now_ts - begin_ts > _QR_LOGIN_TIMEOUT:
                    self._publish_qr_status('二维码超时，请重新点击登录')
                    self._show_info('二维码已超时，请重新点击登录音乐')
                    return

                check = LoginQrcodeCheck(str(unikey))
                code  = int((check or {}).get('code', -1))
                if code != last_code:
                    if code == 802:
                        self._publish_qr_status('已扫码，请在手机端确认登录')
                    elif code == 803:
                        self._publish_qr_status('登录成功，正在同步...')
                    elif code == 800:
                        self._publish_qr_status('二维码已过期，请重新点击登录')
                    else:
                        self._publish_qr_status(f'登录状态: {code}')
                    last_code = code
                    if code != 801:
                        last_refresh_left = None

                if code == 803:
                    status = self._safe_login_call(GetCurrentLoginStatus)
                    if self._is_account_logged_in(status):
                        profile  = self._profile_from_status(status)
                        self._set_login_state(True, profile, provider='netease')
                        self._save_login_cache()
                        nickname = self._extract_nickname(profile) or '网易云账号'
                        self._show_info(self._login_success_message("网易云平台", nickname))
                        return
                    self._show_error('登录状态异常，请重试')
                    return

                if code == 800:
                    self._publish_qr_status('二维码已过期，正在自动刷新...')
                    unikey, qr_png = self._request_qr_payload(LoginQrcodeUnikey, GetLoginQRCodeUrl)
                    self._publish_qr_show(qr_png, '二维码已自动刷新，请重新扫码', title='网易云扫码登录')
                    last_code         = None
                    last_refresh_left = None
                    last_refresh_ts   = time.time()
                    continue

                # 自动刷新二维码：按 _QR_REFRESH_INTERVAL（默认 30 秒）刷新一次（等待扫码阶段）
                if code == 801 and (now_ts - last_refresh_ts) >= _QR_REFRESH_INTERVAL:
                    unikey, qr_png = self._request_qr_payload(LoginQrcodeUnikey, GetLoginQRCodeUrl)
                    self._publish_qr_show(qr_png, '二维码已自动刷新，请扫码登录', title='网易云扫码登录')
                    last_code         = None
                    last_refresh_left = None
                    last_refresh_ts   = time.time()
                    continue

                # 等待扫码阶段显示刷新倒计时（每秒更新）
                if code == 801:
                    left = int(math.ceil(max(0.0, _QR_REFRESH_INTERVAL - (now_ts - last_refresh_ts))))
                    if left != last_refresh_left:
                        self._publish_qr_status('等待扫码...', refresh_left=left)
                        last_refresh_left = left

                time.sleep(_QR_POLL_INTERVAL)

        except ImportError:
            self._show_error('缺少 pyncm 依赖，无法二维码登录')
        except Exception as e:
            logger.error('[CloudMusic] 二维码登录失败: %s', e)
            self._show_error('二维码登录失败，请稍后重试')
        finally:
            self._publish_qr_hide()

    # ------------------------------------------------------------------
    # 启动登录
    # ------------------------------------------------------------------

    def _login(self):
        """启动时登录：初始化恢复全平台登录态。"""
        t0 = time.monotonic()
        netease_ready = False
        qq_ready = False
        kugou_ready = False
        try:
            # Kugou: 启动时恢复缓存登录，不依赖当前模式。
            try:
                kugou_ready = self._restore_kugou_login_from_cache()
            except Exception as e:
                logger.warning("[CloudMusic] 酷狗启动恢复登录失败: %s", e)
            if not kugou_ready:
                self._set_login_state(False, {}, provider='kugou')

            # QQ: 启动时恢复缓存登录，不依赖当前模式。
            try:
                qq_ready = self._restore_qq_login_from_cache()
            except Exception as e:
                logger.warning("[CloudMusic] QQ 启动恢复登录失败: %s", e)
            if not qq_ready:
                self._set_login_state(False, {}, provider='qq')

            # NetEase: 启动时恢复账号登录，失败回退匿名登录（保证搜索可用）。
            try:
                restored = self._restore_login_from_cache()
                if restored:
                    netease_ready = True
                else:
                    self._anonymous_login()
                    netease_ready = self.provider_logged_in('netease')
            except ImportError:
                logger.warning("[CloudMusic] pyncm 未安装")
                self._set_login_state(False, {}, provider='netease')
            except Exception as e:
                logger.error("[CloudMusic] 网易云启动登录失败: %s", e)
                self._set_login_state(False, {}, provider='netease')
        finally:
            self._login_ready.set()
            self._publish_login_status()
            current_provider = self._current_provider()
            current_logged_in = self.provider_logged_in(current_provider)
            logger.debug(
                "[CloudMusic] 启动登录流程结束: current=%s logged_in=%s netease=%s qq=%s kugou=%s dt=%.3fs",
                current_provider,
                current_logged_in,
                netease_ready,
                qq_ready,
                kugou_ready,
                time.monotonic() - t0,
            )
