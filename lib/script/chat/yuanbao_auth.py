"""YuanBao-Free-API 登录态抓取辅助。"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse


class YuanBaoAuthError(RuntimeError):
    """元宝登录态抓取失败。"""


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _iter_local_playwright_executables(root_dir: Path):
    if not root_dir.exists() or not root_dir.is_dir():
        return
    for candidate in root_dir.rglob("chromium-*"):
        if not candidate.is_dir():
            continue
        for relative in (
            Path("chrome-win") / "chrome.exe",
            Path("chrome-win64") / "chrome.exe",
        ):
            executable = candidate / relative
            if executable.exists():
                yield executable
                break


def _find_local_playwright_executable() -> Path | None:
    roots = (
        _project_root() / "resc" / "playwright" / "browsers" / "ms-playwright",
        _project_root() / "resc" / "playwright",
        _project_root() / "resc",
    )
    for root in roots:
        for executable in _iter_local_playwright_executables(root):
            return executable
    return None


def _parse_cookie_header(raw: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in str(raw or '').split(';'):
        segment = part.strip()
        if not segment or '=' not in segment:
            continue
        key, value = segment.split('=', 1)
        key = key.strip()
        value = value.strip()
        if key:
            cookies[key] = value
    return cookies


def _extract_agent_id_from_url(url: str) -> str:
    text = str(url or '').strip()
    if not text:
        return ''
    parsed = urlparse(text)
    path_parts = [part for part in parsed.path.split('/') if part]
    if 'chat' in path_parts:
        idx = path_parts.index('chat')
        if idx + 1 < len(path_parts):
            return path_parts[idx + 1].strip()
    query = parse_qs(parsed.query)
    for key in ('agent_id', 'agentId', 'bot_id', 'botId'):
        value = query.get(key)
        if value and value[0].strip():
            return value[0].strip()
    return ''


def _walk_json_payload(value, state: dict[str, str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).strip()
            if key_text in ('agent_id', 'agentId', 'bot_id', 'botId') and not state.get('agent_id'):
                text = str(item or '').strip()
                if text:
                    state['agent_id'] = text
            elif key_text in ('chat_id', 'chatId') and not state.get('chat_id'):
                text = str(item or '').strip()
                if text:
                    state['chat_id'] = text
            elif key_text == 'hy_source' and not state.get('hy_source'):
                text = str(item or '').strip()
                if text:
                    state['hy_source'] = text
            elif key_text in ('x_uskey', 'xUskey') and not state.get('x_uskey'):
                text = str(item or '').strip()
                if text:
                    state['x_uskey'] = text
            _walk_json_payload(item, state)
    elif isinstance(value, list):
        for item in value:
            _walk_json_payload(item, state)


def _page_login_markers(page) -> dict[str, bool]:
    try:
        return page.evaluate(
            """
            () => {
              const isVisible = (el) => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (!style || style.visibility === 'hidden' || style.display === 'none') return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
              };
              const texts = Array.from(document.querySelectorAll('button,a,[role="button"],span,div'))
                .filter(isVisible)
                .map((el) => (el.innerText || el.textContent || '').trim())
                .filter(Boolean)
                .slice(0, 400);
              const hasLoginEntry = texts.some((text) => /登录|注册|扫码登录/.test(text));
              const hasLoggedInEntry = texts.some((text) => /退出登录|个人中心|我的智能体|帐号设置|账号设置/.test(text));
              return { hasLoginEntry, hasLoggedInEntry };
            }
            """
        ) or {}
    except Exception:
        return {}


def _is_confirmed_logged_in(page) -> bool:
    markers = _page_login_markers(page)
    if not markers:
        return False
    if markers.get('hasLoggedInEntry'):
        return True
    return not bool(markers.get('hasLoginEntry'))


def capture_yuanbao_login_state(
    login_url: str,
    *,
    timeout_secs: int = 240,
    progress_callback=None,
) -> dict[str, str]:
    """
    使用 Playwright 打开元宝网页登录页，并从实际请求中抓取登录态参数。

    返回字段可能包含：hy_user / hy_token / x_uskey / agent_id / chat_id / hy_source。
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - 依赖缺失
        raise YuanBaoAuthError(
            "未安装 Playwright。请先执行 `pip install playwright`，再执行 `playwright install chromium`。"
        ) from exc

    state: dict[str, str] = {'hy_source': 'web'}
    initial_agent_id = _extract_agent_id_from_url(login_url)
    if initial_agent_id:
        state['agent_id'] = initial_agent_id
    done = threading.Event()
    page = None
    guest_hint_emitted = False

    def emit(message: str) -> None:
        if callable(progress_callback):
            try:
                progress_callback(message)
            except Exception:
                pass

    def maybe_complete() -> None:
        nonlocal guest_hint_emitted
        has_auth = (
            bool(state.get('hy_user'))
            and bool(state.get('hy_token'))
            and bool(state.get('agent_id'))
        )
        if not has_auth:
            return
        if page is not None and _is_confirmed_logged_in(page):
            done.set()
            return
        if not guest_hint_emitted:
            guest_hint_emitted = True
            emit('检测到当前更像游客态；请先完成账号登录，并进入目标元宝会话后再试。')

    def on_request(request) -> None:
        try:
            request_url = str(getattr(request, 'url', '') or '')
            headers = {
                str(key).strip().lower(): str(value or '').strip()
                for key, value in (getattr(request, 'headers', {}) or {}).items()
            }
            if 'yuanbao.tencent.com' not in request_url and 'yuanbao.tencent.com' not in headers.get('referer', ''):
                return

            cookie_map = _parse_cookie_header(headers.get('cookie', ''))
            hy_user = str(cookie_map.get('hy_user') or '').strip()
            hy_token = str(cookie_map.get('hy_token') or '').strip()
            x_uskey = str(headers.get('x-uskey') or headers.get('x_uskey') or '').strip()
            if hy_user:
                state['hy_user'] = hy_user
            if hy_token:
                state['hy_token'] = hy_token
            if x_uskey:
                state['x_uskey'] = x_uskey

            agent_id = _extract_agent_id_from_url(request_url) or _extract_agent_id_from_url(headers.get('referer', ''))
            if agent_id:
                state['agent_id'] = agent_id

            post_data = getattr(request, 'post_data', None)
            if post_data:
                try:
                    _walk_json_payload(json.loads(post_data), state)
                except Exception:
                    pass

            maybe_complete()
        except Exception:
            return

    browser = None
    context = None
    playwright = None
    try:
        emit('正在启动浏览器并打开元宝登录页...')
        playwright = sync_playwright().start()
        launch_errors: list[str] = []
        local_executable = _find_local_playwright_executable()
        if local_executable is not None:
            try:
                browser = playwright.chromium.launch(executable_path=str(local_executable), headless=False)
            except Exception as exc:
                launch_errors.append(f'local:{local_executable}: {exc}')
        for channel in ('msedge', 'chrome', None):
            if browser is not None:
                break
            try:
                kwargs = {'headless': False}
                if channel:
                    kwargs['channel'] = channel
                browser = playwright.chromium.launch(**kwargs)
                break
            except Exception as exc:
                launch_errors.append(f'{channel or "chromium"}: {exc}')
        if browser is None:
            raise YuanBaoAuthError('无法启动可用浏览器：' + ' | '.join(launch_errors))

        context = browser.new_context(locale='zh-CN')
        context.on('request', on_request)
        page = context.new_page()
        page.goto(login_url, wait_until='domcontentloaded', timeout=30000)

        emit('请在打开的浏览器里完成腾讯元宝登录，并进入你要使用的元宝会话页面。')
        deadline = time.monotonic() + max(30, int(timeout_secs or 240))
        while time.monotonic() < deadline:
            try:
                for cookie in context.cookies('https://yuanbao.tencent.com'):
                    name = str(cookie.get('name') or '').strip()
                    value = str(cookie.get('value') or '').strip()
                    if not name or not value:
                        continue
                    if name == 'hy_user':
                        state['hy_user'] = value
                    elif name == 'hy_token':
                        state['hy_token'] = value
                maybe_complete()
            except Exception:
                pass
            if done.wait(timeout=0.5):
                break

        if not done.is_set():
            missing = []
            for key in ('hy_user', 'agent_id'):
                if not state.get(key):
                    missing.append(key)
            for key in ('hy_token',):
                if not state.get(key):
                    missing.append(key)
            raise YuanBaoAuthError('登录态抓取超时，缺少字段：' + ', '.join(missing))

        emit('已抓取到登录态，准备回填到桌宠配置。')
        return dict(state)
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
