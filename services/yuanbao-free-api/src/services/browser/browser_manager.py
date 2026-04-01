"""浏览器管理器模块"""

import asyncio
import logging
import re
from pathlib import Path
from typing import Dict, Optional

from playwright.async_api import Browser, BrowserContext, Locator, Page, TimeoutError as PlaywrightTimeoutError, async_playwright

from src.config import settings
from src.utils.qr_utils import decode_qr_from_image, extract_qr_region_from_image, print_qr_to_terminal

logger = logging.getLogger(__name__)


def _service_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _project_root_candidates() -> tuple[Path, ...]:
    service_root = _service_root()
    return (
        service_root,
        service_root.parent,
    )


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


def _find_local_playwright_executable() -> Optional[Path]:
    for project_root in _project_root_candidates():
        for root in (
            project_root / "resc" / "playwright" / "browsers" / "ms-playwright",
            project_root / "resc" / "playwright",
            project_root / "resc",
        ):
            for executable in _iter_local_playwright_executables(root):
                return executable
    return None


def _detect_windows_default_chromium_channel() -> Optional[str]:
    try:
        import winreg
    except Exception:
        return None

    try:
        key_path = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            prog_id = str(winreg.QueryValueEx(key, "ProgId")[0] or "").strip().lower()
    except Exception:
        return None

    if not prog_id:
        return None
    if prog_id.startswith("msedgehtm") or "edge" in prog_id:
        return "msedge"
    if prog_id.startswith("chromehtml") or "chrome" in prog_id:
        return "chrome"
    return None


def _preferred_chromium_channels() -> tuple[str, ...]:
    preferred = _detect_windows_default_chromium_channel()
    ordered: list[str] = []
    if preferred:
        ordered.append(preferred)
    for channel in ("msedge", "chrome"):
        if channel not in ordered:
            ordered.append(channel)
    return tuple(ordered)


def _parse_cookie_header(raw: str) -> Dict[str, str]:
    cookies: Dict[str, str] = {}
    for part in str(raw or "").split(";"):
        segment = part.strip()
        if not segment or "=" not in segment:
            continue
        key, value = segment.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            cookies[key] = value
    return cookies


def _storage_state_path() -> Path:
    raw = str(getattr(settings, "storage_state_path", "") or "").strip()
    if not raw:
        raw = "storage_state.json"
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = _service_root() / path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return path


def _remove_storage_state_file() -> None:
    try:
        path = _storage_state_path()
        if path.exists():
            path.unlink()
    except Exception:
        pass


def _has_basic_login_cookies(cookies: Dict[str, str]) -> bool:
    hy_user = str(cookies.get("hy_user") or "").strip()
    hy_token = str(cookies.get("hy_token") or "").strip()
    return bool(hy_user and hy_token)


_AUTH_COOKIE_MARKERS = (
    "uin",
    "skey",
    "p_uin",
    "p_skey",
    "wxuin",
    "wx_skey",
    "wx_sid",
    "pt4_token",
    "ptcz",
    "pt_login_sig",
)


def _has_login_cookies(cookies: Dict[str, str]) -> bool:
    if not _has_basic_login_cookies(cookies):
        return False

    names = {str(name).lower() for name in cookies.keys()}
    if any(marker in names for marker in _AUTH_COOKIE_MARKERS):
        return True
    # 兼容类似 xxx_uin / xxx_skey 的命名
    for name in names:
        if name.endswith("_uin") or name.endswith("_skey"):
            return True
    return False


class BrowserManager:
    """浏览器管理器 - 单例模式"""

    _instance = None
    _lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, "_initialized"):
            self.browser: Optional[Browser] = None
            self.context: Optional[BrowserContext] = None
            self.page: Optional[Page] = None
            self.playwright = None
            self._route_handler = None
            self._is_logged_in = False
            self._login_in_progress = False
            self._last_error = ""
            self._last_message = ""
            self._cookie_marker_warned = False
            self._login_confirmed_via_ui = False
            self._storage_state_saved = False
            self._initialized = True

    def status(self) -> Dict:
        qrcode_path = Path(str(settings.qrcode_path or "")).expanduser()
        return {
            "browser_initialized": bool(self.browser is not None and self.page is not None),
            "logged_in": bool(self._is_logged_in),
            "login_in_progress": bool(self._login_in_progress),
            "qrcode_path": settings.qrcode_path,
            "qrcode_exists": bool(qrcode_path and qrcode_path.exists()),
            "last_error": str(self._last_error or ""),
            "last_message": str(self._last_message or ""),
            "page_url": settings.page_url,
        }

    async def _dismiss_blocking_dialogs(self) -> None:
        if not self.page:
            return

        dialog_root = self.page.locator(".t-dialog__position, .t-portal-wrapper .t-dialog, [role='dialog']").first
        close_selectors = (
            ".t-dialog__close",
            ".t-dialog__close-btn",
            "[aria-label*='close' i]",
            "[title*='close' i]",
            "button:has-text('关闭')",
            "button:has-text('取消')",
            "button:has-text('稍后')",
            "button:has-text('跳过')",
            "button:has-text('我知道了')",
            "button:has-text('知道了')",
        )

        for _ in range(4):
            try:
                if await dialog_root.count() <= 0 or not await dialog_root.is_visible():
                    return
            except Exception:
                return

            self._last_message = "dismissing_dialog"
            closed = False
            for selector in close_selectors:
                locator = self.page.locator(selector).first
                try:
                    if await locator.count() > 0 and await locator.is_visible():
                        await locator.click(force=True, timeout=1500)
                        await self.page.wait_for_timeout(600)
                        closed = True
                        break
                except Exception:
                    continue

            if not closed:
                try:
                    await self.page.keyboard.press("Escape")
                    await self.page.wait_for_timeout(600)
                except Exception:
                    return

    async def _first_visible(self, candidates, timeout: int = 1200) -> Optional[Locator]:
        for locator in candidates:
            try:
                await locator.wait_for(state="visible", timeout=timeout)
                box = await locator.bounding_box()
                if box and float(box.get("width") or 0) >= 12 and float(box.get("height") or 0) >= 12:
                    return locator
            except Exception:
                continue
        return None

    async def _find_login_entry(self) -> Optional[Locator]:
        if not self.page:
            return None

        text_regex = re.compile(r"登录|扫码登录|微信登录|立即登录|去登录|login|sign in|scan", re.I)
        primary_candidates = [
            self.page.get_by_role("img").first,
            self.page.locator("header img, nav img, .header img, .navbar img").first,
            self.page.get_by_role("button", name=text_regex).first,
            self.page.get_by_text(text_regex).first,
        ]
        primary = await self._first_visible(primary_candidates, timeout=1500)
        if primary is not None:
            return primary

        group = self.page.locator("button, [role='button'], img")
        try:
            count = min(await group.count(), 16)
        except Exception:
            return None

        for idx in range(count):
            locator = group.nth(idx)
            try:
                await locator.wait_for(state="visible", timeout=300)
                text_content = (await locator.text_content() or "").strip()
                aria_label = (await locator.get_attribute("aria-label") or "").strip()
                title = (await locator.get_attribute("title") or "").strip()
                class_name = (await locator.get_attribute("class") or "").strip().lower()
                hint = " ".join(part for part in (text_content, aria_label, title, class_name) if part)
                if text_regex.search(hint) or "login" in class_name or "scan" in class_name:
                    return locator
            except Exception:
                continue

        return None

    async def _choose_best_qrcode_locator(self, group) -> Optional[Locator]:
        best_locator: Optional[Locator] = None
        best_score = -1.0
        try:
            count = min(await group.count(), 16)
        except Exception:
            return None

        for idx in range(count):
            locator = group.nth(idx)
            try:
                await locator.wait_for(state="visible", timeout=500)
                box = await locator.bounding_box()
                if not box:
                    continue
                width = float(box.get("width") or 0.0)
                height = float(box.get("height") or 0.0)
                if width < 100 or height < 100:
                    continue
                ratio = max(width, height) / max(1.0, min(width, height))
                if ratio > 1.25:
                    continue

                text_content = (await locator.text_content() or "").strip().lower()
                class_name = ((await locator.get_attribute("class")) or "").strip().lower()
                alt_text = ((await locator.get_attribute("alt")) or "").strip().lower()
                src = ((await locator.get_attribute("src")) or "").strip().lower()
                hint = " ".join(part for part in (text_content, class_name, alt_text, src) if part)
                if any(token in hint for token in ("logo", "icon", "avatar", "close", "anno", "hunyuan")):
                    continue

                score = width * height
                if any(token in hint for token in ("qr", "qrcode", "code", "scan", "二维码", "扫码")):
                    score += 1_000_000
                if score > best_score:
                    best_score = score
                    best_locator = locator
            except Exception:
                continue

        return best_locator

    async def _find_qrcode_locator(self) -> Optional[Locator]:
        if not self.page:
            return None

        iframe = self.page.frame_locator("iframe")
        upstream_candidates = [
            iframe.get_by_role("img").first,
            iframe.locator("img").first,
            iframe.locator("canvas").first,
        ]

        locator = await self._first_visible(upstream_candidates, timeout=1500)
        if locator is not None:
            box = await locator.bounding_box()
            if box and float(box.get("width") or 0) >= 100 and float(box.get("height") or 0) >= 100:
                logger.info("[Browser] QR locator found by upstream path")
                return locator

        groups = (
            iframe.locator("img, canvas, svg, [class*='qr'], [class*='code'], [data-testid*='qr'], [data-testid*='code']"),
            self.page.locator(
                ".t-dialog__position img, .t-dialog__position canvas, .t-dialog__position svg, "
                ".t-dialog__position [class*='qr'], .t-dialog__position [class*='code'], "
                ".t-portal-wrapper img, .t-portal-wrapper canvas, .t-portal-wrapper svg, "
                ".t-portal-wrapper [class*='qr'], .t-portal-wrapper [class*='code'], "
                "img, canvas, svg, [class*='qr'], [class*='code'], [data-testid*='qr'], [data-testid*='code']"
            ),
        )
        for group in groups:
            best = await self._choose_best_qrcode_locator(group)
            if best is not None:
                logger.info("[Browser] QR locator selected by fallback scoring")
                return best

        return None

    async def _wait_for_qrcode_locator(self, timeout_ms: int = 10000) -> Optional[Locator]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(1.0, timeout_ms / 1000.0)
        while loop.time() < deadline:
            locator = await self._find_qrcode_locator()
            if locator is not None:
                return locator
            await asyncio.sleep(0.4)
        return None

    async def _save_qrcode_snapshot(self, locator: Optional[Locator]) -> bool:
        if not self.page:
            return False

        qrcode_file = Path(str(settings.qrcode_path or "")).expanduser()
        qrcode_file.parent.mkdir(parents=True, exist_ok=True)
        temp_candidates: list[Path] = []

        def _finalize_if_valid(temp_path: Path) -> bool:
            try:
                if decode_qr_from_image(str(temp_path)):
                    temp_path.replace(qrcode_file)
                    self._last_message = "qrcode_ready"
                    self._last_error = ""
                    logger.info("[Browser] QR image saved from %s", temp_path.name)
                    return True
            except Exception as exc:
                logger.debug("[Browser] QR decode validate failed for %s: %s", temp_path.name, exc)
            try:
                if extract_qr_region_from_image(str(temp_path), str(qrcode_file)):
                    self._last_message = "qrcode_ready"
                    self._last_error = ""
                    logger.info("[Browser] QR region extracted from %s", temp_path.name)
                    return True
            except Exception as exc:
                logger.debug("[Browser] QR crop validate failed for %s: %s", temp_path.name, exc)
            return False

        try:
            if locator is not None:
                direct_tmp = qrcode_file.with_name(f"{qrcode_file.stem}_direct_tmp.png")
                try:
                    await locator.screenshot(path=str(direct_tmp))
                    temp_candidates.append(direct_tmp)
                    if _finalize_if_valid(direct_tmp):
                        return True
                except Exception as exc:
                    logger.debug("[Browser] Direct QR screenshot failed: %s", exc)

            dialog_locator = self.page.locator(".t-dialog__position, .t-portal-wrapper .t-dialog, [role='dialog']").first
            dialog_tmp = qrcode_file.with_name(f"{qrcode_file.stem}_dialog_tmp.png")
            try:
                await dialog_locator.wait_for(state="visible", timeout=1200)
                await dialog_locator.screenshot(path=str(dialog_tmp))
                temp_candidates.append(dialog_tmp)
                if _finalize_if_valid(dialog_tmp):
                    return True
            except Exception as exc:
                logger.debug("[Browser] Dialog QR screenshot failed: %s", exc)

            page_tmp = qrcode_file.with_name(f"{qrcode_file.stem}_page_tmp.png")
            try:
                await self.page.screenshot(path=str(page_tmp), full_page=False)
                temp_candidates.append(page_tmp)
                if _finalize_if_valid(page_tmp):
                    return True
            except Exception as exc:
                logger.debug("[Browser] Page QR screenshot failed: %s", exc)

            self._last_error = "qrcode_extract_failed"
            logger.warning("[Browser] Failed to capture a valid QR image")
            return False
        finally:
            for temp_path in temp_candidates:
                try:
                    if temp_path.exists() and temp_path != qrcode_file:
                        temp_path.unlink()
                except Exception:
                    pass

    async def _capture_qrcode_fallback(self) -> bool:
        return await self._save_qrcode_snapshot(None)

    async def _refresh_expired_qrcode(self) -> bool:
        if not self.page:
            return False

        refresh_regex = re.compile(r"二维码已失效|二维码过期|已过期|刷新|重新获取|重试|retry|refresh|expired", re.I)
        candidates = [
            self.page.get_by_role("button", name=refresh_regex).first,
            self.page.get_by_text(refresh_regex).first,
            self.page.frame_locator("iframe").get_by_role("button", name=refresh_regex).first,
            self.page.frame_locator("iframe").get_by_text(refresh_regex).first,
            self.page.locator("[class*='refresh'], [aria-label*='refresh' i], [title*='refresh' i], [aria-label*='刷新'], [title*='刷新']").first,
            self.page.frame_locator("iframe").locator("[class*='refresh'], [aria-label*='refresh' i], [title*='refresh' i], [aria-label*='刷新'], [title*='刷新']").first,
        ]
        for locator in candidates:
            try:
                await locator.wait_for(state="visible", timeout=600)
                await locator.click(force=True, timeout=1500)
                self._last_message = "refreshing_qrcode"
                logger.info("[Browser] Expired QR code detected, refresh click sent")
                await self.page.wait_for_timeout(1000)
                return True
            except Exception:
                continue
        return False

    async def _is_login_confirmed(self, login_button: Locator) -> bool:
        try:
            if await self._has_authenticated_session():
                return True
        except Exception:
            return False
        # 登录按钮消失或 DOM 变化并不等价于已登录；避免误判游客态为成功。
        try:
            await login_button.wait_for(state="hidden", timeout=800)
        except PlaywrightTimeoutError:
            pass
        except Exception:
            pass
        return False


    async def _page_login_markers(self) -> Dict[str, bool]:
        if not self.page:
            return {}
        try:
            return await self.page.evaluate(
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

    async def _is_page_logged_in(self) -> bool:
        markers = await self._page_login_markers()
        if not markers:
            return False
        if markers.get("hasLoggedInEntry"):
            return True
        return not bool(markers.get("hasLoginEntry"))

    async def _persist_storage_state(self, reason: str = "") -> bool:
        if self._storage_state_saved:
            return True
        ctx = self.context or (self.page.context if self.page is not None else None)
        if ctx is None:
            return False
        path = _storage_state_path()
        try:
            await ctx.storage_state(path=str(path))
            self._storage_state_saved = True
            suffix = f"({reason}) " if reason else ""
            logger.info("[Browser] 持久化登录态完成 %s%s", suffix, path)
            return True
        except Exception as exc:
            logger.debug("[Browser] 持久化登录态失败: %s", exc)
            return False

    async def _has_authenticated_session(self) -> bool:
        try:
            cookies = await self.get_cookies()
        except Exception as exc:
            logger.debug('[Browser] Cookie confirmation failed: %s', exc)
            return False

        if _has_login_cookies(cookies):
            self._login_confirmed_via_ui = True
            logger.info('[Browser] Auth cookies detected (hy_user/hy_token + account markers), treat as logged in')
            return True

        if _has_basic_login_cookies(cookies):
            if await self._is_page_logged_in():
                self._login_confirmed_via_ui = True
                logger.info('[Browser] UI indicates logged in with hy_user/hy_token present')
                return True
            logger.debug('[Browser] hy_user/hy_token present but UI still shows login entry')

        if cookies:
            cookie_names = sorted({str(name).lower() for name in cookies.keys()})
            if not self._cookie_marker_warned:
                self._cookie_marker_warned = True
                logger.info('[Browser] Cookies present but no confirmed account markers: %s', cookie_names)
        return False

    async def _wait_for_login_or_refresh(self, login_button: Locator) -> bool:
        if not self.page:
            return False

        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(5.0, settings.login_timeout / 1000.0)
        next_snapshot_at = 0.0
        next_auth_check_at = 2.5

        while loop.time() < deadline:
            self._last_message = "waiting_scan_confirm"
            if await self._is_login_confirmed(login_button):
                logger.info("[Browser] Login confirmed by UI state")
                return True

            now = loop.time()
            if now >= next_snapshot_at:
                qrcode_locator = await self._find_qrcode_locator()
                if qrcode_locator is not None:
                    await self._save_qrcode_snapshot(qrcode_locator)
                else:
                    await self._capture_qrcode_fallback()
                next_snapshot_at = now + 5.0

            if now >= next_auth_check_at and await self._has_authenticated_session():
                self._last_error = ""
                self._last_message = "login_success"
                return True
            if now >= next_auth_check_at:
                next_auth_check_at = now + 4.0

            if await self._refresh_expired_qrcode():
                next_snapshot_at = 0.0

            await asyncio.sleep(1.0)

        return False

    async def ensure_browser(self):
        """确保浏览器已初始化"""
        async with self._lock:
            if self.browser is None or self.page is None:
                await self._init_browser()

    async def _init_browser(self):
        """初始化浏览器"""
        self._last_message = "starting_playwright"
        if self.playwright is None:
            self.playwright = await async_playwright().start()

        self._last_message = "launching_browser"
        if self.browser is None:
            launch_errors: list[str] = []
            local_executable = _find_local_playwright_executable()
            if local_executable is not None:
                try:
                    self.browser = await self.playwright.chromium.launch(
                        executable_path=str(local_executable),
                        headless=True,
                    )
                    logger.info("[Browser] 已使用本地 Chromium 资源启动: %s", local_executable)
                except Exception as exc:
                    launch_errors.append(f"local:{local_executable}: {exc}")

            if self.browser is None:
                for channel in _preferred_chromium_channels():
                    try:
                        self.browser = await self.playwright.chromium.launch(
                            channel=channel,
                            headless=True,
                        )
                        logger.info("[Browser] Launched system browser channel: %s", channel)
                        break
                    except Exception as exc:
                        launch_errors.append(f"{channel}: {exc}")

            if self.browser is None:
                for headless in (False, True):
                    try:
                        self.browser = await self.playwright.chromium.launch(headless=headless)
                        logger.info("[Browser] 已使用 Playwright 默认 Chromium 启动: headless=%s", headless)
                        break
                    except Exception as exc:
                        launch_errors.append(f"default(headless={headless}): {exc}")

            if self.browser is None:
                raise RuntimeError("无法启动可用浏览器: " + " | ".join(launch_errors))

        self._last_message = "creating_page"
        if self.page is None:
            storage_path = _storage_state_path()
            context_kwargs: Dict[str, object] = {}
            if storage_path.exists():
                context_kwargs["storage_state"] = str(storage_path)
                logger.info("[Browser] 使用持久化登录态: %s", storage_path)
            self.context = await self.browser.new_context(**context_kwargs)
            self.page = await self.context.new_page()
            await self._load_page()
        self._last_error = ""
        self._last_message = "browser_initialized"

    async def _load_page(self):
        """预加载页面"""
        logger.info("[Browser] 预加载 Yuanbao 页面...")
        try:
            self._last_message = "page_loading"
            await self.page.goto(settings.page_url, timeout=settings.page_timeout)
            await self.page.wait_for_timeout(3000)
            self._last_message = "page_loaded"
            logger.info("[Browser] 页面加载完成")
        except Exception as e:
            logger.error(f"[Browser] 页面加载失败: {e}")
            raise

    async def login(self, force: bool = False) -> Dict:
        """执行登录流程，返回二维码信息。"""
        self._login_in_progress = True
        self._last_error = ""
        self._last_message = "starting_login"
        self._storage_state_saved = False

        try:
            await self.ensure_browser()
        except Exception as e:
            self._last_error = str(e)
            self._last_message = "browser_init_failed"
            self._login_in_progress = False
            return {
                "success": False,
                "message": f"browser_init_failed: {e}",
            }

        if self._is_logged_in and not force:
            self._login_in_progress = False
            self._last_message = "already_logged_in"
            return {
                "success": True,
                "message": "already_logged_in",
                "qrcode_path": settings.qrcode_path,
            }

        if not force:
            try:
                if await self._has_authenticated_session():
                    self._is_logged_in = True
                    self._login_in_progress = False
                    self._last_message = "already_logged_in"
                    return {
                        "success": True,
                        "message": "already_logged_in",
                        "qrcode_path": settings.qrcode_path,
                    }
            except Exception:
                pass

        try:
            if force and self.page is not None:
                await self._load_page()
            await self._dismiss_blocking_dialogs()

            self._last_message = "resolving_login_button"
            login_button = await self._find_login_entry()
            if login_button is None:
                raise RuntimeError("login_entry_not_found")

            self._last_message = "clicking_login_button"
            try:
                await login_button.scroll_into_view_if_needed()
            except Exception:
                pass
            try:
                await login_button.click(timeout=5000)
            except Exception:
                await self._dismiss_blocking_dialogs()
                await login_button.click(force=True, timeout=3000)

            self._last_message = "waiting_qrcode"
            await self.page.wait_for_timeout(1200)
            qrcode_locator = await self._wait_for_qrcode_locator(timeout_ms=15000)
            if qrcode_locator is not None:
                if not await self._save_qrcode_snapshot(qrcode_locator) and not await self._capture_qrcode_fallback():
                    raise RuntimeError("qrcode_extract_failed")
            elif not await self._capture_qrcode_fallback():
                raise RuntimeError("qrcode_container_not_found")

            try:
                print_qr_to_terminal(settings.qrcode_path)
            except Exception as exc:
                logger.warning("[Browser] Failed to print QR in terminal: %s", exc)

            logger.info("[Browser] Waiting for scan confirmation and QR refresh...")
            logged_in = await self._wait_for_login_or_refresh(login_button)
            if logged_in:
                await self._persist_storage_state("login_success")
                self._is_logged_in = True
                self._login_in_progress = False
                self._last_message = "login_success"
                return {
                    "success": True,
                    "message": "login_success",
                    "qrcode_path": settings.qrcode_path,
                }

            logger.warning("[Browser] Scan timeout or login not detected")
            self._login_in_progress = False
            self._last_message = "login_timeout"
            return {
                "success": False,
                "message": "login_timeout",
                "qrcode_path": settings.qrcode_path,
            }
        except Exception as e:
            logger.error("[Browser] Login failed: %s", e)
            self._last_error = str(e)
            self._last_message = "login_failed"
            self._login_in_progress = False
            return {
                "success": False,
                "message": f"login_failed: {str(e)}",
            }

    async def get_headers(self) -> Optional[Dict]:
        """获取请求头。"""
        await self.ensure_browser()
        captured_headers = {}

        async def handle_route(route, request):
            nonlocal captured_headers
            url = request.url
            headers = request.headers

            if settings.header_api_pattern in url:
                if "x-uskey" in headers and not captured_headers.get("x-uskey"):
                    cookie_map = _parse_cookie_header(headers.get("cookie", ""))
                    if _has_login_cookies(cookie_map) or (
                        self._login_confirmed_via_ui and _has_basic_login_cookies(cookie_map)
                    ):
                        captured_headers = headers
                        logger.info(f"[Browser] 捕获到请求头 from {url}")
                    else:
                        logger.debug("[Browser] x-uskey 已出现但未检测到账户标记，判定为游客态")

            await route.continue_()

        if self._route_handler:
            try:
                self.page.remove_route("**/*")
            except Exception:
                pass

        await self.page.route("**/*", handle_route)
        self._route_handler = handle_route

        try:
            reload_task = asyncio.create_task(self.page.reload(timeout=10000))

            start_time = asyncio.get_event_loop().time()
            while (asyncio.get_event_loop().time() - start_time) < settings.header_timeout:
                if captured_headers.get("x-uskey"):
                    break
                await asyncio.sleep(0.05)

            if captured_headers.get("x-uskey"):
                reload_task.cancel()
                try:
                    await reload_task
                except asyncio.CancelledError:
                    pass

        except Exception as e:
            logger.error(f"[Browser] 获取请求头失败: {e}")
        finally:
            if self._route_handler:
                try:
                    self.page.remove_route("**/*")
                    self._route_handler = None
                except Exception:
                    pass

        return captured_headers if captured_headers.get("x-uskey") else None

    async def get_cookies(self) -> Dict[str, str]:
        """获取 Cookie。"""
        await self.ensure_browser()

        if not self.page:
            return {}

        cookies = await self.page.context.cookies()
        return {c["name"]: c["value"] for c in cookies}

    async def logout(self):
        """退出登录并清理持久化状态。"""
        _remove_storage_state_file()
        await self.close()

    async def close(self):
        """关闭浏览器。"""
        async with self._lock:
            tasks = []
            self._is_logged_in = False
            self._login_in_progress = False
            self._login_confirmed_via_ui = False
            self._storage_state_saved = False
            if self.context:
                tasks.append(self.context.close())
                self.context = None
            if self.page:
                tasks.append(self.page.close())
                self.page = None
            if self.browser:
                tasks.append(self.browser.close())
                self.browser = None
            if self.playwright:
                tasks.append(self.playwright.stop())
                self.playwright = None
            self._last_message = "browser_closed"
            try:
                qrcode_file = Path(str(settings.qrcode_path or "")).expanduser()
                if qrcode_file.exists():
                    qrcode_file.unlink()
            except Exception:
                pass
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)


browser_manager = BrowserManager()

