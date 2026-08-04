"""Bundled yuanbao-free-api local service bootstrap."""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
import zipfile
from concurrent.futures import Future
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import config.ollama_config as oc
from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import Event, EventType
from lib.core.logger import get_logger
from lib.script.local_hosted_service import LocalHostedServiceBase

logger = get_logger(__name__)

_STARTUP_WAIT_SECS = 165.0
_POLL_INTERVAL_SECS = 0.5
_LOGIN_MONITOR_SECS = 300.0
_SERVICE_REQUIRED_FILES = ('app.py', 'requirements.txt')
_BUNDLED_ARCHIVE_NAME = 'yuanbao-free-api-main.zip'
_STATUS_ENDPOINT = '/fsv/status'
_LOGIN_ENDPOINT = '/fsv/login'
_LOGOUT_ENDPOINT = '/fsv/logout'
_REQUIRED_MODULES = (
    'fastapi',
    'uvicorn',
    'openai',
    'httpx',
    'pydantic_settings',
    'sse_starlette',
    'playwright',
)


def _hidden_console_kwargs() -> dict[str, object]:
    if os.name != 'nt':
        return {}
    kwargs: dict[str, object] = {}
    creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    if creationflags:
        kwargs['creationflags'] = creationflags
    startupinfo_cls = getattr(subprocess, 'STARTUPINFO', None)
    if startupinfo_cls is not None:
        startupinfo = startupinfo_cls()
        startupinfo.dwFlags |= getattr(subprocess, 'STARTF_USESHOWWINDOW', 0)
        startupinfo.wShowWindow = getattr(subprocess, 'SW_HIDE', 0)
        kwargs['startupinfo'] = startupinfo
    return kwargs


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _service_dir() -> Path:
    return _project_root() / 'services' / 'yuanbao-free-api'


def _service_entry() -> Path:
    return _service_dir() / 'app.py'


def _bundled_archive_path() -> Path:
    return _project_root() / 'services' / 'bundles' / _BUNDLED_ARCHIVE_NAME


def _log_path() -> Path:
    path = _project_root() / 'logs' / 'yuanbao_free_api_launcher.log'
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _qrcode_path() -> Path:
    path = _project_root() / 'logs' / 'yuanbao_free_api_qrcode.png'
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _find_bundle_root(extract_root: Path) -> Optional[Path]:
    candidates = [extract_root]
    candidates.extend(path for path in extract_root.iterdir() if path.is_dir())
    for candidate in candidates:
        if all((candidate / name).exists() for name in _SERVICE_REQUIRED_FILES):
            return candidate
    for candidate in extract_root.rglob('*'):
        if candidate.is_dir() and all((candidate / name).exists() for name in _SERVICE_REQUIRED_FILES):
            return candidate
    return None


def _ensure_service_bundle_extracted() -> bool:
    if all((_service_dir() / name).exists() for name in _SERVICE_REQUIRED_FILES):
        return True

    archive_path = _bundled_archive_path()
    if not archive_path.exists():
        logger.warning('[YuanbaoFreeApiService] 未找到内置服务压缩包: %s', archive_path)
        return False

    temp_root = _project_root() / 'services' / '.yuanbao_extract_tmp'
    extract_root = temp_root / 'extract'
    try:
        if temp_root.exists():
            import shutil
            shutil.rmtree(temp_root, ignore_errors=True)
        extract_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path, 'r') as zf:
            zf.extractall(extract_root)
        bundle_root = _find_bundle_root(extract_root)
        if bundle_root is None:
            raise RuntimeError('服务压缩包内缺少 app.py / requirements.txt')
        if _service_dir().exists():
            import shutil
            shutil.rmtree(_service_dir(), ignore_errors=True)
        _service_dir().parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.move(str(bundle_root), str(_service_dir()))
        logger.info('[YuanbaoFreeApiService] 已从内置压缩包解压服务目录: %s', _service_dir())
        return True
    except Exception as exc:
        logger.error('[YuanbaoFreeApiService] 解压内置服务包失败: %s', exc)
        return False
    finally:
        if temp_root.exists():
            import shutil
            shutil.rmtree(temp_root, ignore_errors=True)


def _launcher_python() -> str:
    executable = Path(sys.executable)
    if executable.name.lower() == 'pythonw.exe':
        python_exe = executable.with_name('python.exe')
        if python_exe.exists():
            return str(python_exe)
    return str(executable)


def _build_page_url(agent_id: str, login_url: str) -> str:
    text = str(login_url or '').strip()
    if text.startswith('http://') or text.startswith('https://'):
        if '/chat/' in text:
            return text
        return f"{text.rstrip('/')}/chat/{agent_id}"
    return f'https://yuanbao.tencent.com/chat/{agent_id}'


def _build_service_env() -> Dict[str, str]:
    options = getattr(oc, 'YUANBAO_FREE_API', {}) or {}
    api_key = str(oc.get_yuanbao_local_api_key() if hasattr(oc, 'get_yuanbao_local_api_key') else 'sk-yuanbao-local').strip()
    agent_id = str(options.get('agent_id', '') or 'naQivTmsDa').strip() or 'naQivTmsDa'
    page_url = _build_page_url(agent_id, str(options.get('login_url', '') or '').strip())

    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env.setdefault('PYTHONUNBUFFERED', '1')
    env['API_KEYS'] = api_key or 'sk-local-placeholder'
    env['AGENT_ID'] = agent_id
    env['PAGE_URL'] = page_url
    env['QRCODE_PATH'] = str(_qrcode_path())
    return env


def _remove_qrcode_if_exists() -> None:
    try:
        if _qrcode_path().exists():
            _qrcode_path().unlink()
    except Exception:
        pass


def _parse_local_target() -> Optional[Tuple[str, int]]:
    base_url = str(oc.get_yuanbao_local_base_url() if hasattr(oc, 'get_yuanbao_local_base_url') else 'http://127.0.0.1:8000/v1').strip()
    if not base_url:
        return None
    parsed = urlparse(base_url)
    host = (parsed.hostname or '').strip().lower()
    if host not in ('127.0.0.1', 'localhost'):
        return None
    port = int(parsed.port or (443 if parsed.scheme == 'https' else 80))
    return host, port


def _find_free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _build_local_base_url_for_port(port: int) -> str:
    return f'http://127.0.0.1:{int(port)}/v1'


def _should_manage_local_service() -> bool:
    force_mode = str(getattr(oc, 'FORCE_REPLY_MODE', '') or '').strip()
    if force_mode != '4':
        return False
    return _parse_local_target() is not None


def _can_connect(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _status_url(host: str, port: int) -> str:
    return f'http://{host}:{port}{_STATUS_ENDPOINT}'


def _login_url(host: str, port: int) -> str:
    return f'http://{host}:{port}{_LOGIN_ENDPOINT}'


def _logout_url(host: str, port: int) -> str:
    return f'http://{host}:{port}{_LOGOUT_ENDPOINT}'


def _normalize_login_provider(provider: str | None) -> str:
    value = str(provider or '').strip().lower()
    if value == 'qq':
        return 'qq'
    return 'wechat'


def _login_provider_label(provider: str | None) -> str:
    return '手机QQ' if _normalize_login_provider(provider) == 'qq' else '微信'


def _login_dialog_title(provider: str | None) -> str:
    return f'{_login_provider_label(provider)}登录元宝'


def _http_json(
    url: str,
    *,
    method: str = 'GET',
    timeout: float = 5.0,
    payload: Optional[Dict[str, object]] = None,
) -> Optional[Dict[str, object]]:
    request = Request(url, method=method.upper())
    if method.upper() == 'POST':
        request.add_header('Content-Type', 'application/json')
        request.data = json.dumps(payload or {}, ensure_ascii=False).encode('utf-8')
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or 'utf-8'
            payload = response.read().decode(charset, errors='ignore').strip()
            if not payload:
                return {}
            data = json.loads(payload)
            return data if isinstance(data, dict) else {'value': data}
    except (OSError, URLError, ValueError) as exc:
        logger.debug('[YuanbaoFreeApiService] HTTP %s %s failed: %s', method, url, exc)
        return None


def _probe_status_endpoint(host: str, port: int, timeout: float = 3.0) -> Tuple[str, Optional[Dict[str, object]]]:
    if not _can_connect(host, port, timeout=min(timeout, 1.0)):
        return 'offline', None

    request = Request(_status_url(host, port), method='GET')
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or 'utf-8'
            payload = response.read().decode(charset, errors='ignore').strip()
            if not payload:
                return 'ok', {}
            data = json.loads(payload)
            if isinstance(data, dict):
                return 'ok', data
            return 'invalid', {'value': data}
    except HTTPError as exc:
        logger.debug('[YuanbaoFreeApiService] HTTP GET %s failed: %s', _status_url(host, port), exc)
        if exc.code == 404:
            return 'missing', None
        return 'http_error', None
    except (OSError, URLError, ValueError) as exc:
        logger.debug('[YuanbaoFreeApiService] HTTP GET %s failed: %s', _status_url(host, port), exc)
        return 'error', None


def _fetch_service_status(host: str, port: int, timeout: float = 3.0) -> Optional[Dict[str, object]]:
    state, status = _probe_status_endpoint(host, port, timeout=timeout)
    return status if state == 'ok' else None


def _request_service_login(
    host: str,
    port: int,
    provider: str = 'wechat',
    timeout: float = 10.0,
) -> Optional[Dict[str, object]]:
    provider_name = _normalize_login_provider(provider)
    return _http_json(
        _login_url(host, port),
        method='POST',
        timeout=timeout,
        payload={'provider': provider_name},
    )


def _request_service_logout(host: str, port: int, timeout: float = 10.0) -> Optional[Dict[str, object]]:
    return _http_json(_logout_url(host, port), method='POST', timeout=timeout)


def _status_bool(status: Optional[Dict[str, object]], key: str) -> bool:
    return bool((status or {}).get(key, False))


def _status_text(status: Optional[Dict[str, object]], key: str) -> str:
    return str((status or {}).get(key, '') or '').strip()


def _read_qrcode_bytes() -> Optional[bytes]:
    try:
        path = _qrcode_path()
        if path.exists():
            return path.read_bytes()
    except Exception:
        pass
    return None


def _describe_status_message(status: Optional[Dict[str, object]], provider: str | None = None) -> str:
    provider_name = _normalize_login_provider((status or {}).get('login_provider') or provider)
    provider_label = _login_provider_label(provider_name)
    stage = _status_text(status, 'last_message')
    if _status_bool(status, 'logged_in'):
        return '元宝已登录，可直接使用。'
    if _status_bool(status, 'qrcode_exists'):
        return f'请使用{provider_label}扫码登录元宝。'
    if _status_text(status, 'last_error'):
        return _status_text(status, 'last_error')
    mapping = {
        'starting_login': '正在初始化登录流程',
        'starting_playwright': '正在启动浏览器驱动',
        'launching_browser': '正在启动浏览器',
        'creating_page': '正在创建页面',
        'page_loading': '正在打开元宝页面',
        'page_loaded': '元宝页面已打开，正在继续登录',
        'browser_initialized': '浏览器已就绪，正在继续登录',
        'dismissing_dialog': '正在关闭页面弹窗',
        'resolving_login_button': '正在定位登录入口',
        'waiting_login_button': '正在等待登录入口出现',
        'clicking_login_button': '正在点击登录入口',
        'login_button_clicked': '登录入口已点击，正在等待二维码',
        'login_button_not_found': '未找到登录入口',
        'selecting_login_provider': f'正在切换到{provider_label}登录',
        'login_provider_selected': f'已切换到{provider_label}登录，正在等待二维码',
        'login_provider_not_found': f'未找到{provider_label}登录入口，将继续尝试默认扫码入口',
        'waiting_qrcode': '正在等待二维码出现',
        'qrcode_ready': f'二维码已生成，请使用{provider_label}扫码登录元宝。',
        'waiting_scan_confirm': '二维码已生成，正在等待扫码确认',
        'login_success': '元宝登录成功',
        'login_timeout': '扫码超时，请重新扫码',
        'browser_init_failed': '浏览器初始化失败',
        'login_failed': '元宝登录失败',
    }
    return mapping.get(stage, '正在准备元宝扫码登录...')


def _missing_runtime_modules() -> list[str]:
    missing: list[str] = []
    for name in _REQUIRED_MODULES:
        if importlib.util.find_spec(name) is None:
            missing.append(name)
    return missing


def _find_listener_pids(host: str, port: int) -> list[int]:
    target_suffixes = {f'{host}:{port}', f'127.0.0.1:{port}', f'localhost:{port}', f'0.0.0.0:{port}'}
    try:
        result = subprocess.run(
            ['netstat', '-ano', '-p', 'tcp'],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding='utf-8',
            errors='ignore',
            timeout=8,
            check=False,
            **_hidden_console_kwargs(),
        )
    except Exception as exc:
        logger.debug('[YuanbaoFreeApiService] 查询监听端口失败: %s', exc)
        return []

    pids: list[int] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if 'LISTENING' not in line.upper():
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        local_addr = parts[1].strip().lower()
        if not any(local_addr.endswith(suffix.lower()) for suffix in target_suffixes):
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid > 0 and pid not in pids:
            pids.append(pid)
    return pids


def _kill_process_by_pid(pid: int) -> bool:
    try:
        result = subprocess.run(
            ['taskkill', '/PID', str(pid), '/T', '/F'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
            check=False,
            **_hidden_console_kwargs(),
        )
        return result.returncode == 0
    except Exception as exc:
        logger.debug('[YuanbaoFreeApiService] 结束进程失败 pid=%s err=%s', pid, exc)
        return False


class YuanbaoFreeApiService(LocalHostedServiceBase):
    def __init__(self):
        super().__init__('YuanbaoFreeApiService', 'yuanbao_prestart_ready')
        self._login_monitor_lock = threading.RLock()
        self._login_monitor_thread: Optional[Future] = None
        self._active_login_provider = 'wechat'
        self._login_dialog_initializer: Optional[Callable[[], object]] = None
        self._subscribe_app_pre_start()

    def configure_login_dialog_initializer(
        self,
        initializer: Optional[Callable[[], object]],
    ) -> None:
        """Inject the desktop UI initializer at the application boundary."""
        self._login_dialog_initializer = initializer

    def _should_prestart(self) -> bool:
        return _should_manage_local_service()

    def _prestart_worker(self) -> bool:
        return self.ensure_service_process_ready()

    def _ensure_login_dialog(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            logger.debug('[YuanbaoFreeApiService] Skip login dialog init in background thread; wait for main thread to create it')
            return
        initializer = self._login_dialog_initializer
        if initializer is None:
            return
        try:
            initializer()
        except Exception as exc:
            logger.debug('[YuanbaoFreeApiService] ???????????: %s', exc)

    def _publish_login_dialog_show(self, status: Optional[Dict[str, object]] = None) -> None:
        self._ensure_login_dialog()
        provider = (status or {}).get('login_provider') or self._active_login_provider
        payload = {
            'title': _login_dialog_title(str(provider or 'wechat')),
            'status': _describe_status_message(status, str(provider or 'wechat')),
            'qr_png': _read_qrcode_bytes(),
        }
        self._ec.publish(Event(EventType.YUANBAO_LOGIN_QR_SHOW, payload))

    def _publish_login_dialog_status(self, status: Optional[Dict[str, object]] = None) -> None:
        self._ensure_login_dialog()
        provider = (status or {}).get('login_provider') or self._active_login_provider
        payload = {
            'title': _login_dialog_title(str(provider or 'wechat')),
            'status': _describe_status_message(status, str(provider or 'wechat')),
            'qr_png': _read_qrcode_bytes(),
            'logged_in': _status_bool(status, 'logged_in'),
        }
        self._ec.publish(Event(EventType.YUANBAO_LOGIN_QR_STATUS, payload))

    def _publish_login_dialog_hide(self) -> None:
        self._ec.publish(Event(EventType.YUANBAO_LOGIN_QR_HIDE, {}))

    def _start_login_monitor(self, host: str, port: int) -> None:
        with self._login_monitor_lock:
            future = self._login_monitor_thread
            if future is not None and not future.done():
                return
            future = get_compute_hub().submit_latest(
                "yuanbao_login_monitor",
                self._run_login_monitor,
                host,
                port,
                executor="io",
            )
            if future is not None:
                self._login_monitor_thread = future

    def _run_login_monitor(self, host: str, port: int) -> None:
        deadline = time.monotonic() + _LOGIN_MONITOR_SECS
        last_status: Optional[Dict[str, object]] = None
        try:
            while time.monotonic() < deadline:
                status = _fetch_service_status(host, port, timeout=2.0)
                if status is not None:
                    last_status = status
                    self._publish_login_dialog_status(status)
                    if _status_bool(status, 'logged_in'):
                        self._publish_status_hint(status)
                        self._publish_login_dialog_hide()
                        return
                time.sleep(_POLL_INTERVAL_SECS)
        finally:
            with self._login_monitor_lock:
                self._login_monitor_thread = None
        if last_status is not None:
            self._publish_login_dialog_status(last_status)

    def get_service_status(self) -> Optional[Dict[str, object]]:
        target = _parse_local_target()
        if target is None:
            return None
        host, port = target
        status, _host, _port = self._ensure_status_endpoint(host, port)
        return status

    def peek_service_status(self) -> Optional[Dict[str, object]]:
        """读取已有本地服务状态，不启动服务、也不改变端口或登录流程。"""
        target = _parse_local_target()
        if target is None:
            return None
        host, port = target
        return _fetch_service_status(host, port, timeout=1.0)

    def ensure_service_process_ready(self) -> bool:
        """仅确保 YuanBao-Free-API 本地服务可访问，不触发登录或二维码获取。"""
        target = _parse_local_target()
        if target is None:
            return False
        host, port = target
        status, host, port = self._ensure_status_endpoint(host, port)
        if status is None:
            return False
        logger.info('[YuanbaoFreeApiService] 元宝本地服务已预热: %s:%s status=%s', host, port, status)
        return True

    def ensure_service_ready(self) -> bool:
        target = _parse_local_target()
        if target is None:
            return False
        host, port = target
        status, host, port = self._ensure_status_endpoint(host, port)
        if status is None:
            return False
        if _status_bool(status, 'logged_in'):
            logger.info('[YuanbaoFreeApiService] 元宝服务已登录并就绪: %s:%s', host, port)
            return True
        if (not _status_bool(status, 'qrcode_exists')
                and not _status_bool(status, 'login_in_progress')
                and not _status_text(status, 'last_error')):
            login_result = _request_service_login(host, port, provider=self._active_login_provider)
            if login_result:
                logger.info('[YuanbaoFreeApiService] 已触发元宝登录流程: %s', login_result)
                status = self._wait_for_login_state(host, port, allow_logged_out=True) or status
        self._publish_status_hint(status)
        return _status_bool(status, 'logged_in')

    def stop_login_flow(self) -> Dict[str, object]:
        self._publish_login_dialog_hide()
        target = _parse_local_target()
        result: Dict[str, object] = {'success': True, 'message': 'stopped'}
        if target is not None:
            host, port = target
            logout_result = _request_service_logout(host, port, timeout=8.0)
            if isinstance(logout_result, dict):
                result.update(logout_result)
        with self._login_monitor_lock:
            self._login_monitor_thread = None
        proc, started = self._take_tracked_process()
        if started and proc is not None:
            self._terminate_process_tree(proc)
        if target is not None:
            host, port = target
            for pid in _find_listener_pids(host, port):
                if proc is not None and pid == proc.pid:
                    continue
                if pid == os.getpid():
                    continue
                _kill_process_by_pid(pid)
        return result

    def begin_login_flow(self, provider: str = 'wechat') -> Dict[str, object]:
        self._active_login_provider = _normalize_login_provider(provider)
        _remove_qrcode_if_exists()
        self._publish_login_dialog_show({
            'last_message': 'starting_login',
            'login_provider': self._active_login_provider,
        })
        target = _parse_local_target()
        if target is None:
            self._publish_login_dialog_status({
                'last_error': '当前接口地址不是本地 YuanBao-Free-API。',
                'login_provider': self._active_login_provider,
            })
            return {
                'success': False,
                'message': '当前接口地址不是本地 YuanBao-Free-API，无法启动登录流程。',
                'provider': self._active_login_provider,
            }

        host, port = target
        status, host, port = self._ensure_status_endpoint(host, port)
        if status is None:
            self._publish_login_dialog_status({
                'last_error': f'元宝服务未能启动，请查看 {_log_path().name}。',
                'login_provider': self._active_login_provider,
            })
            return {
                'success': False,
                'message': f'元宝服务未能启动，请查看 {_log_path().name}。',
                'provider': self._active_login_provider,
            }

        if _status_bool(status, 'logged_in'):
            self._publish_login_dialog_hide()
            return {
                'success': True,
                'logged_in': True,
                'qrcode_exists': _status_bool(status, 'qrcode_exists'),
                'status': status,
                'message': '元宝服务已登录，可直接使用。',
                'provider': self._active_login_provider,
            }

        login_result = _request_service_login(host, port, provider=self._active_login_provider)
        if login_result is None:
            refreshed = self._wait_for_login_state(host, port, allow_logged_out=True) or status
            self._publish_login_dialog_status(refreshed)
            if _status_bool(refreshed, 'login_in_progress'):
                self._start_login_monitor(host, port)
                return {
                    'success': True,
                    'logged_in': False,
                    'login_in_progress': True,
                    'qrcode_exists': _status_bool(refreshed, 'qrcode_exists'),
                    'status': refreshed,
                    'message': '元宝登录流程已启动，正在等待二维码生成。',
                    'provider': self._active_login_provider,
                }
            error_text = _status_text(refreshed, 'last_error') or '无法调用元宝登录接口。'
            return {
                'success': False,
                'logged_in': _status_bool(refreshed, 'logged_in'),
                'qrcode_exists': _status_bool(refreshed, 'qrcode_exists'),
                'status': refreshed,
                'message': error_text,
                'provider': self._active_login_provider,
            }

        refreshed = self._wait_for_login_state(host, port, allow_logged_out=True) or self.get_service_status() or status
        self._publish_status_hint(refreshed)
        self._publish_login_dialog_status(refreshed)
        last_error = _status_text(refreshed, 'last_error')
        if _status_bool(refreshed, 'logged_in'):
            self._publish_login_dialog_hide()
            return {
                'success': True,
                'logged_in': True,
                'qrcode_exists': _status_bool(refreshed, 'qrcode_exists'),
                'status': refreshed,
                'message': '元宝登录已完成，可直接使用。',
                'provider': self._active_login_provider,
            }
        if _status_bool(refreshed, 'qrcode_exists'):
            self._start_login_monitor(host, port)
            return {
                'success': True,
                'logged_in': False,
                'qrcode_exists': _status_bool(refreshed, 'qrcode_exists'),
                'status': refreshed,
                'message': '元宝二维码已就绪，请扫码完成登录。',
                'provider': self._active_login_provider,
            }
        if _status_bool(refreshed, 'login_in_progress'):
            self._start_login_monitor(host, port)
            return {
                'success': True,
                'logged_in': False,
                'login_in_progress': True,
                'qrcode_exists': False,
                'status': refreshed,
                'message': '元宝登录流程已启动，但二维码尚未生成，请稍候再试。',
                'provider': self._active_login_provider,
            }
        return {
            'success': False,
            'logged_in': False,
            'qrcode_exists': False,
            'status': refreshed,
            'message': last_error or _status_text(refreshed, 'last_message') or '元宝登录未能启动。',
            'provider': self._active_login_provider,
        }

    def _ensure_status_endpoint(self, host: str, port: int) -> Tuple[Optional[Dict[str, object]], str, int]:
        state, status = _probe_status_endpoint(host, port)
        if state == 'ok' and status is not None:
            logger.info('[YuanbaoFreeApiService] 检测到元宝服务已在运行: %s:%s status=%s', host, port, status)
            return status, host, port
        if state != 'offline':
            logger.warning(
                '[YuanbaoFreeApiService] %s:%s 已被其他服务占用或响应异常（state=%s），准备切换元宝本地中转端口',
                host,
                port,
                state,
            )
        if state == 'missing':
            if self._terminate_conflicting_listener(host, port):
                time.sleep(1.0)
            else:
                switched = self._switch_to_random_local_port(host, port)
                if switched is not None:
                    host, port = switched
        elif state != 'offline':
            switched = self._switch_to_random_local_port(host, port)
            if switched is not None:
                host, port = switched
        if not self._start_service_process(host, port):
            return None, host, port
        return self._wait_for_status_endpoint(host, port), host, port

    def _switch_to_random_local_port(self, host: str, port: int) -> Optional[Tuple[str, int]]:
        try:
            new_port = _find_free_local_port()
        except OSError as exc:
            logger.error('[YuanbaoFreeApiService] 为元宝服务分配随机端口失败: %s', exc)
            return None
        if new_port == port:
            return host, port

        new_base_url = _build_local_base_url_for_port(new_port)
        try:
            oc.YUANBAO_FREE_API_LOCAL['base_url'] = new_base_url
        except Exception:
            logger.error('[YuanbaoFreeApiService] 更新运行时元宝 base_url 失败，无法切换到随机端口')
            return None

        logger.warning(
            '[YuanbaoFreeApiService] %s:%s 被其他服务占用，已切换元宝本地中转到随机端口 %s',
            host,
            port,
            new_port,
        )
        self._ec.publish(Event(EventType.INFORMATION, {
            'text': f'元宝服务默认端口 {port} 被占用，已自动切换到 {new_port} 端口。',
            'min': 18,
            'max': 220,
            'particle': False,
        }))
        return '127.0.0.1', new_port

    def _terminate_conflicting_listener(self, host: str, port: int) -> bool:
        pids = _find_listener_pids(host, port)
        if not pids:
            return False

        current_pid = os.getpid()
        killed = False
        for pid in pids:
            if pid == current_pid:
                continue
            if _kill_process_by_pid(pid):
                killed = True
                logger.warning('[YuanbaoFreeApiService] 已结束占用 %s:%s 的旧进程 pid=%s', host, port, pid)

        if killed:
            self._ec.publish(Event(EventType.INFORMATION, {
                'text': f'检测到旧版元宝服务占用 {port} 端口，已自动清理并准备重启。',
                'min': 18,
                'max': 200,
                'particle': False,
            }))
        return killed

    def _wait_for_status_endpoint(self, host: str, port: int) -> Optional[Dict[str, object]]:
        deadline = time.monotonic() + _STARTUP_WAIT_SECS
        restarted_conflict = False
        while time.monotonic() < deadline:
            state, status = _probe_status_endpoint(host, port, timeout=2.0)
            if state == 'ok' and status is not None:
                logger.info('[YuanbaoFreeApiService] 元宝服务状态接口已就绪: %s', status)
                return status
            if state == 'missing' and not restarted_conflict:
                restarted_conflict = True
                if self._terminate_conflicting_listener(host, port):
                    if not self._start_service_process(host, port):
                        break
                    time.sleep(1.0)
                    continue
            proc = self._tracked_process()
            if proc is not None and proc.poll() is not None:
                break
            time.sleep(_POLL_INTERVAL_SECS)
        logger.error('[YuanbaoFreeApiService] 元宝服务状态接口启动失败，目标=%s:%s 日志=%s', host, port, _log_path())
        self._ec.publish(Event(EventType.INFORMATION, {
            'text': '元宝服务未能正常启动，请检查 logs/yuanbao_free_api_launcher.log。',
            'min': 18,
            'max': 220,
            'particle': False,
        }))
        return None

    def _wait_for_login_state(self, host: str, port: int, *, allow_logged_out: bool = False) -> Optional[Dict[str, object]]:
        deadline = time.monotonic() + 45.0
        last_status: Optional[Dict[str, object]] = None
        while time.monotonic() < deadline:
            status = _fetch_service_status(host, port, timeout=2.0)
            if status is not None:
                last_status = status
                self._publish_login_dialog_status(status)
                if _status_bool(status, 'logged_in'):
                    return status
                if _status_bool(status, 'qrcode_exists'):
                    return status
                if allow_logged_out and _status_text(status, 'last_error'):
                    return status
            time.sleep(_POLL_INTERVAL_SECS)
        return last_status

    def _publish_status_hint(self, status: Optional[Dict[str, object]]) -> None:
        if not status:
            return
        if _status_bool(status, 'logged_in'):
            self._ec.publish(Event(EventType.INFORMATION, {
                'text': '元宝服务已登录并就绪。',
                'min': 10,
                'max': 100,
                'particle': False,
            }))
            return
        if _status_bool(status, 'qrcode_exists'):
            qr_rel = _qrcode_path().relative_to(_project_root())
            self._ec.publish(Event(EventType.INFORMATION, {
                'text': f'元宝登录二维码已生成：{qr_rel}，请扫码完成登录。',
                'min': 18,
                'max': 220,
                'particle': False,
            }))
            return
        error_text = _status_text(status, 'last_error')
        if error_text:
            self._ec.publish(Event(EventType.INFORMATION, {
                'text': f'元宝登录初始化失败：{error_text}',
                'min': 20,
                'max': 260,
                'particle': False,
            }))

    def _start_service_process(self, host: str, port: int) -> bool:
        _remove_qrcode_if_exists()
        missing_modules = _missing_runtime_modules()
        if missing_modules:
            text = '元宝本地中转缺少依赖：' + ', '.join(missing_modules) + '；请先运行“安装依赖.bat”或重新执行 install_deps.py。'
            logger.warning('[YuanbaoFreeApiService] %s', text)
            self._ec.publish(Event(EventType.INFORMATION, {
                'text': text,
                'min': 18,
                'max': 260,
                'particle': False,
            }))
            return False
        entry = _service_entry()
        if not entry.exists():
            _ensure_service_bundle_extracted()
            entry = _service_entry()
        if not entry.exists():
            logger.warning('[YuanbaoFreeApiService] 未找到本地中转入口: %s', entry)
            self._ec.publish(Event(EventType.INFORMATION, {
                'text': '未找到 yuanbao-free-api 服务目录或内置压缩包，请先检查 services/bundles。',
                'min': 16,
                'max': 160,
                'particle': False,
            }))
            return False

        with self._proc_lock:
            if self._has_running_tracked_process():
                return True
            log_handle = _log_path().open('a', encoding='utf-8', errors='ignore')
            env = _build_service_env()
            create_no_window = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            try:
                proc = subprocess.Popen(
                    [_launcher_python(), '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', str(port)],
                    cwd=str(_service_dir()),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    creationflags=create_no_window,
                )
                self._set_started_process(proc)
                log_handle.close()
            except Exception as exc:
                log_handle.close()
                logger.error('[YuanbaoFreeApiService] 启动本地中转失败: %s', exc)
                self._ec.publish(Event(EventType.INFORMATION, {
                    'text': f'元宝本地中转启动失败: {exc}',
                    'min': 16,
                    'max': 180,
                    'particle': False,
                }))
                self._mark_process_start_failed()
                return False

        deadline = time.monotonic() + _STARTUP_WAIT_SECS
        while time.monotonic() < deadline:
            if _can_connect(host, port):
                logger.info('[YuanbaoFreeApiService] 元宝服务端口已启动: %s:%s', host, port)
                return True
            proc = self._tracked_process()
            if proc is not None and proc.poll() is not None:
                break
            time.sleep(_POLL_INTERVAL_SECS)

        logger.error('[YuanbaoFreeApiService] 元宝服务启动超时或已退出，目标=%s:%s 日志=%s', host, port, _log_path())
        self._ec.publish(Event(EventType.INFORMATION, {
            'text': '元宝服务未能成功启动，请检查 logs/yuanbao_free_api_launcher.log。',
            'min': 18,
            'max': 220,
            'particle': False,
        }))
        return False

    def cleanup(self):
        self._unsubscribe_app_pre_start()
        self._publish_login_dialog_hide()

        with self._login_monitor_lock:
            self._login_monitor_thread = None

        proc, started = self._take_tracked_process()

        if started and proc is not None:
            self._terminate_process_tree(proc)

        target = _parse_local_target()
        if target is None:
            return

        host, port = target
        state, status = _probe_status_endpoint(host, port, timeout=2.0)
        if state != 'ok' or status is None:
            return

        for pid in _find_listener_pids(host, port):
            if proc is not None and pid == proc.pid:
                continue
            if pid == os.getpid():
                continue
            if _kill_process_by_pid(pid):
                logger.info('[YuanbaoFreeApiService] ???????????? pid=%s', pid)

    @staticmethod
    def _terminate_process_tree(proc: subprocess.Popen) -> bool:
        if proc.poll() is not None:
            return True
        try:
            result = subprocess.run(
                ['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                check=False,
                **_hidden_console_kwargs(),
            )
            return result.returncode == 0
        except Exception as exc:
            logger.debug('[YuanbaoFreeApiService] 结束本地中转进程失败: %s', exc)
            return False


_instance: Optional[YuanbaoFreeApiService] = None


def get_yuanbao_free_api_service() -> YuanbaoFreeApiService:
    global _instance
    if _instance is None:
        _instance = YuanbaoFreeApiService()
    return _instance


def cleanup_yuanbao_free_api_service():
    global _instance
    if _instance is not None:
        _instance.cleanup()
        _instance = None


def get_yuanbao_free_api_qrcode_path() -> Path:
    return _qrcode_path()


def get_yuanbao_free_api_log_path() -> Path:
    return _log_path()
