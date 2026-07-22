"""启动早期硬件信息探测。"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

_SYS_INFO_FILE = "sys.txt"
_FALLBACK_CONTROL_PANEL_WATERMARK = ("Aemeath", "AIsetting")
_FALLBACK_HARDWARE_WATERMARK = ("UnKnow GPU 0.00 GB", "RAM 0.00 GB")
_FALLBACK_BUG_TRACKER_TITLE = ("BUG", "TRACKER")
_FALLBACK_BUG_TRACKER_META = ("CPU unknown", "0 Cores")


class _MEMORYSTATUSEX(ctypes.Structure):
    """Windows 内存状态结构。"""

    _fields_ = [
        ('dwLength', ctypes.c_ulong),
        ('dwMemoryLoad', ctypes.c_ulong),
        ('ullTotalPhys', ctypes.c_ulonglong),
        ('ullAvailPhys', ctypes.c_ulonglong),
        ('ullTotalPageFile', ctypes.c_ulonglong),
        ('ullAvailPageFile', ctypes.c_ulonglong),
        ('ullTotalVirtual', ctypes.c_ulonglong),
        ('ullAvailVirtual', ctypes.c_ulonglong),
        ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
    ]

    def __init__(self):
        super().__init__()
        self.dwLength = ctypes.sizeof(self)


def _format_bytes(num_bytes: int) -> str:
    value = float(max(0, int(num_bytes)))
    units = ('B', 'KB', 'MB', 'GB', 'TB')
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == 'B':
                return f'{int(value)}{unit}'
            return f'{value:.2f}{unit}'
        value /= 1024.0
    return '0B'


def _format_gb_text(num_bytes: int | None) -> str:
    value = float(max(0, int(num_bytes or 0))) / (1024 ** 3)
    return f"{value:.2f} GB"


def _decode_process_output(raw: bytes | None) -> str:
    if not raw:
        return ''

    sample = raw[:256]
    has_utf16_bom = raw.startswith((b'\xff\xfe', b'\xfe\xff'))
    looks_utf16 = has_utf16_bom or (sample.count(b'\x00') > max(4, len(sample) // 10))

    if looks_utf16:
        for enc in ('utf-16', 'utf-16-le', 'utf-16-be'):
            try:
                return raw.decode(enc).replace('\x00', '')
            except Exception:
                pass

    for enc in ('utf-8-sig', 'gb18030', 'cp936', 'cp1252'):
        try:
            return raw.decode(enc)
        except Exception:
            pass

    return raw.decode('utf-8', errors='ignore')


def _run_capture_text(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    result = subprocess.run(cmd, capture_output=True, text=False, timeout=timeout)
    stdout = _decode_process_output(result.stdout or b'')
    stderr = _decode_process_output(result.stderr or b'')
    return result.returncode, stdout, stderr


def _get_total_memory_bytes() -> int | None:
    try:
        memory_status = _MEMORYSTATUSEX()
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory_status)):
            total = int(memory_status.ullTotalPhys)
            if total > 0:
                return total
    except Exception:
        pass
    return None


def _get_powershell_executable() -> str:
    for candidate in ('pwsh.exe', 'powershell.exe'):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return 'powershell.exe'


def _to_int(value) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _is_virtual_or_software_gpu(name: str) -> bool:
    text = str(name or '').strip().lower()
    if not text:
        return True
    virtual_keywords = (
        'microsoft basic',
        'basic render',
        'indirect display',
        'idd',
        'displaylink',
        'mirror driver',
        'remote display',
        'virtual',
        'vmware',
        'hyper-v',
        'virtio',
        'citrix',
        'parsec',
        'asklink',
    )
    return any(keyword in text for keyword in virtual_keywords)


def _gpu_pick_score(item: dict) -> tuple[int, int, int]:
    name = str(item.get('name') or '').strip().lower()
    ram = _to_int(item.get('adapter_ram'))
    if _is_virtual_or_software_gpu(name):
        return 0, 0, ram
    if any(keyword in name for keyword in ('nvidia', 'geforce', 'rtx', 'gtx', 'quadro', 'tesla')):
        vendor_rank = 3
    elif any(keyword in name for keyword in ('amd', 'radeon', 'rx ', 'vega', 'firepro')):
        vendor_rank = 2
    elif any(keyword in name for keyword in ('intel', 'arc', 'iris', 'uhd', 'hd graphics')):
        vendor_rank = 1
    else:
        vendor_rank = 1 if name else 0
    return 1, vendor_rank, ram


def _shorten_text(text: str, max_len: int) -> str:
    raw = str(text or '').strip()
    if len(raw) <= max_len:
        return raw
    return raw[: max_len - 3].rstrip() + '...'


def _query_gpu_items() -> list[dict]:
    try:
        cmd = [
            _get_powershell_executable(),
            '-NoProfile',
            '-ExecutionPolicy',
            'Bypass',
            '-Command',
            "$ErrorActionPreference='SilentlyContinue'; "
            "$g=@(Get-CimInstance Win32_VideoController | Where-Object { $_.Name } | "
            "Select-Object Name,@{Name='AdapterRAM';Expression={[UInt64]($_.AdapterRAM)}}); "
            "$g | ConvertTo-Json -Compress",
        ]
        rc, stdout, _stderr = _run_capture_text(cmd, timeout=3)
        if rc != 0:
            return []
        payload = (stdout or '').strip()
        if not payload:
            return []
        parsed = json.loads(payload)
        if isinstance(parsed, dict):
            raw_items = [parsed]
        elif isinstance(parsed, list):
            raw_items = [item for item in parsed if isinstance(item, dict)]
        else:
            raw_items = []
        return [
            {
                'name': str(item.get('Name') or '').strip(),
                'adapter_ram': _to_int(item.get('AdapterRAM')),
            }
            for item in raw_items
            if str(item.get('Name') or '').strip()
        ]
    except Exception:
        return []


def _user_root_path() -> Path:
    override = str(os.environ.get('AEMEATH_DESK_PET_HOME', '') or '').strip()
    if override:
        root = Path(override).expanduser()
    else:
        drive = str(os.environ.get('SystemDrive', 'C:') or 'C:').strip() or 'C:'
        drive = drive.rstrip('\\/')
        if not drive.endswith(':'):
            drive = f'{drive}:'
        root = Path(f'{drive}\\AemeathDeskPet')
    return root / 'user'


def get_user_sys_info_path() -> Path:
    return _user_root_path() / _SYS_INFO_FILE


def _build_watermarks(snapshot: dict) -> dict[str, list[str]]:
    gpu_name = str(snapshot.get('primary_gpu_name') or 'UnKnow GPU').strip() or 'UnKnow GPU'
    gpu_vram_text = str(snapshot.get('primary_gpu_vram_gb_text') or '0.00 GB').strip() or '0.00 GB'
    ram_text = str(snapshot.get('ram_gb_text') or '0.00 GB').strip() or '0.00 GB'
    logical_cores = _to_int(snapshot.get('logical_cores'))
    screen_width = _to_int(snapshot.get('screen_width'))
    screen_height = _to_int(snapshot.get('screen_height'))
    draw_scale = snapshot.get('draw_scale', 1.0)
    cpu_name = _shorten_text(str(snapshot.get('cpu') or 'unknown'), 26) or 'unknown'
    cpu_line = f"CPU {cpu_name}"
    core_line = f"{logical_cores} Cores" if logical_cores > 0 else "0 Cores"
    return {
        'control_panel': list(_FALLBACK_CONTROL_PANEL_WATERMARK),
        'hardware': [
            f"{gpu_name} {gpu_vram_text}",
            f"VRAM {gpu_vram_text}",
        ],
        'bug_tracker_title': list(_FALLBACK_BUG_TRACKER_TITLE),
        'bug_tracker_meta': [
            cpu_line,
            core_line,
        ],
        'bug_tracker_corner': [
            f"RAM {ram_text}",
            f"{screen_width}x{screen_height}  x{draw_scale}",
        ],
    }


def collect_startup_hardware_snapshot(draw_config: dict) -> dict:
    cpu_name = (platform.processor() or os.environ.get('PROCESSOR_IDENTIFIER') or 'unknown').strip()
    logical_cores = os.cpu_count()
    total_memory = _get_total_memory_bytes()
    gpu_items = _query_gpu_items()
    gpu_names = [str(item.get('name') or '').strip() for item in gpu_items if str(item.get('name') or '').strip()]
    filtered_items = [item for item in gpu_items if _gpu_pick_score(item)[0] > 0]
    primary_gpu = max(filtered_items or gpu_items, key=_gpu_pick_score, default={})
    primary_gpu_name = str(primary_gpu.get('name') or (gpu_names[0] if gpu_names else 'UnKnow GPU')).strip() or 'UnKnow GPU'
    primary_gpu_vram = _to_int(primary_gpu.get('adapter_ram'))
    snapshot = {
        'captured_at': datetime.now().isoformat(timespec='seconds'),
        'os': platform.platform(),
        'arch': platform.machine() or 'unknown',
        'cpu': cpu_name or 'unknown',
        'logical_cores': logical_cores if logical_cores is not None else 0,
        'ram_bytes': total_memory or 0,
        'ram_text': _format_bytes(total_memory) if total_memory else 'unknown',
        'ram_gb_text': _format_gb_text(total_memory),
        'gpu_items': gpu_items,
        'gpu_names': gpu_names,
        'primary_gpu_name': primary_gpu_name,
        'primary_gpu_vram_bytes': primary_gpu_vram,
        'primary_gpu_vram_gb_text': _format_gb_text(primary_gpu_vram),
        'screen_width': draw_config.get('screen_width', 'unknown'),
        'screen_height': draw_config.get('screen_height', 'unknown'),
        'draw_scale': draw_config.get('scale', 1.0),
    }
    snapshot['watermarks'] = _build_watermarks(snapshot)
    return snapshot


def save_startup_hardware_snapshot(snapshot: dict) -> Path:
    path = get_user_sys_info_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def load_startup_hardware_snapshot() -> dict:
    path = get_user_sys_info_path()
    try:
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding='utf-8'))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def load_saved_watermark_payload() -> dict[str, tuple[str, ...]]:
    snapshot = load_startup_hardware_snapshot()
    payload = snapshot.get('watermarks') if isinstance(snapshot, dict) else None
    if not isinstance(payload, dict):
        payload = _build_watermarks(snapshot if isinstance(snapshot, dict) else {})

    def normalize(key: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
        raw = payload.get(key)
        if isinstance(raw, (list, tuple)):
            lines = tuple(str(item or '').strip() for item in raw if str(item or '').strip())
            if lines:
                return lines
        return fallback

    return {
        'control_panel': normalize('control_panel', _FALLBACK_CONTROL_PANEL_WATERMARK),
        'hardware': normalize('hardware', _FALLBACK_HARDWARE_WATERMARK),
        'bug_tracker_title': normalize('bug_tracker_title', _FALLBACK_BUG_TRACKER_TITLE),
        'bug_tracker_meta': normalize('bug_tracker_meta', _FALLBACK_BUG_TRACKER_META),
        'bug_tracker_corner': normalize('bug_tracker_corner', ('UnKnow GPU', 'unknown')),
    }


def log_startup_hardware_info(logger, draw_config: dict) -> None:
    """记录启动时的硬件与缩放信息。"""
    try:
        snapshot = collect_startup_hardware_snapshot(draw_config)
        try:
            save_startup_hardware_snapshot(snapshot)
        except Exception as save_exc:
            logger.warning('硬件信息写入 sys.txt 失败: %s (%s)', type(save_exc).__name__, save_exc)

        logger.info('[Hardware] OS: %s', snapshot.get('os', 'unknown'))
        logger.info('[Hardware] Arch: %s', snapshot.get('arch', 'unknown'))
        logger.info('[Hardware] CPU: %s', snapshot.get('cpu', 'unknown'))
        logger.info('[Hardware] CPU Cores(logical): %s', snapshot.get('logical_cores') or 'unknown')
        logger.info('[Hardware] RAM: %s', snapshot.get('ram_text', 'unknown'))
        logger.info('[Hardware] GPU: %s', ' | '.join(snapshot.get('gpu_names') or []) or 'unknown')
        logger.info(
            '[Hardware] Primary Screen: %sx%s, draw_scale=%s',
            snapshot.get('screen_width', 'unknown'),
            snapshot.get('screen_height', 'unknown'),
            snapshot.get('draw_scale', 1.0),
        )
    except Exception as e:
        logger.warning('硬件信息采集失败: %s (%s)', type(e).__name__, e)
