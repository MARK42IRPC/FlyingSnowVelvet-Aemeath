"""桌面快捷方式同步。"""

from __future__ import annotations

import os
import subprocess
import tempfile

from config.config import STARTUP
from lib.core.logger import get_logger

logger = get_logger(__name__)


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


def _get_powershell_executable() -> str:
    system_root = os.environ.get('SystemRoot', r'C:\Windows')
    ps_exe = os.path.join(system_root, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    return ps_exe if os.path.exists(ps_exe) else 'powershell'


def _normalize_existing_dir(path: str) -> str | None:
    if not path:
        return None
    try:
        expanded = os.path.expandvars(path).strip().strip('"')
        if not expanded:
            return None
        if os.path.isdir(expanded):
            return os.path.normpath(expanded)
    except Exception:
        pass
    return None


def _collect_desktop_paths() -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        norm = _normalize_existing_dir(path)
        if not norm:
            return
        key = norm.lower()
        if key in seen:
            return
        seen.add(key)
        candidates.append(norm)

    try:
        cmd = [
            _get_powershell_executable(),
            '-NoProfile',
            '-ExecutionPolicy',
            'Bypass',
            '-Command',
            "[Environment]::GetFolderPath('Desktop');[Environment]::GetFolderPath('CommonDesktopDirectory')",
        ]
        rc, stdout, _stderr = _run_capture_text(cmd, timeout=6)
        if rc == 0:
            for line in (stdout or '').splitlines():
                _add(line)
        else:
            logger.debug('PowerShell 获取桌面路径失败: rc=%s', rc)
    except Exception as e:
        logger.debug('PowerShell 获取桌面路径异常: %s', e)

    try:
        import winreg

        reg_keys = [
            r'Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders',
            r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders',
        ]
        for key_path in reg_keys:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                    desktop = winreg.QueryValueEx(key, 'Desktop')[0]
                _add(desktop)
            except Exception as e:
                logger.debug('注册表读取桌面路径失败: %s (%s)', key_path, e)
    except Exception as e:
        logger.debug('winreg 不可用: %s', e)

    user_profile = os.environ.get('USERPROFILE', '')
    if user_profile:
        _add(os.path.join(user_profile, 'Desktop'))

    public_dir = os.environ.get('PUBLIC', r'C:\Users\Public')
    _add(os.path.join(public_dir, 'Desktop'))
    return candidates


def _create_shortcut_via_powershell(
    shortcut_path: str,
    target_path: str,
    working_dir: str,
    description: str,
    icon_path: str,
) -> tuple[bool, str]:
    ps_script = (
        "param(\n"
        "  [string]$ShortcutPath,\n"
        "  [string]$TargetPath,\n"
        "  [string]$WorkingDir,\n"
        "  [string]$Description,\n"
        "  [string]$IconPath\n"
        ")\n"
        "$ErrorActionPreference = 'Stop'\n"
        "$shell = New-Object -ComObject WScript.Shell\n"
        "$lnk = $shell.CreateShortcut($ShortcutPath)\n"
        "$lnk.TargetPath = $TargetPath\n"
        "$lnk.WorkingDirectory = $WorkingDir\n"
        "$lnk.Description = $Description\n"
        "if ($IconPath -and (Test-Path -LiteralPath $IconPath)) { $lnk.IconLocation = $IconPath }\n"
        "$lnk.Save()\n"
    )

    script_file = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.ps1',
            delete=False,
            encoding='utf-8',
            newline='\n',
        ) as f:
            f.write(ps_script)
            script_file = f.name

        cmd = [
            _get_powershell_executable(),
            '-NoProfile',
            '-ExecutionPolicy',
            'Bypass',
            '-File',
            script_file,
            '-ShortcutPath',
            shortcut_path,
            '-TargetPath',
            target_path,
            '-WorkingDir',
            working_dir,
            '-Description',
            description,
            '-IconPath',
            icon_path or '',
        ]
        rc, stdout, stderr = _run_capture_text(cmd, timeout=20)
        if rc == 0 and os.path.exists(shortcut_path):
            return True, ''
        detail = (stderr or stdout or '').strip()
        return False, detail or f'return_code={rc}'
    except Exception as e:
        return False, f'{type(e).__name__}: {e}'
    finally:
        if script_file:
            try:
                os.remove(script_file)
            except OSError:
                pass


def _create_shortcut_via_pywin32(
    shortcut_path: str,
    target_path: str,
    working_dir: str,
    description: str,
    icon_path: str,
) -> tuple[bool, str]:
    try:
        import win32com.client

        shell = win32com.client.Dispatch('WScript.Shell')
        lnk = shell.CreateShortcut(shortcut_path)
        lnk.TargetPath = target_path
        lnk.WorkingDirectory = working_dir
        lnk.Description = description
        if icon_path and os.path.exists(icon_path):
            lnk.IconLocation = icon_path
        lnk.Save()
        if os.path.exists(shortcut_path):
            return True, ''
        return False, 'shortcut_not_created'
    except Exception as e:
        return False, f'{type(e).__name__}: {e}'


def _normalize_file_path_for_compare(path: str) -> str:
    expanded = os.path.expandvars(path or '').strip().strip('"')
    if not expanded:
        return ''
    return os.path.normcase(os.path.normpath(expanded))


def _paths_refer_same_file(path_a: str, path_b: str) -> bool:
    try:
        if path_a and path_b and os.path.exists(path_a) and os.path.exists(path_b):
            return os.path.samefile(path_a, path_b)
    except Exception:
        pass
    return _normalize_file_path_for_compare(path_a) == _normalize_file_path_for_compare(path_b)


def _get_shortcut_target_via_powershell(shortcut_path: str) -> tuple[str | None, str]:
    ps_script = (
        "param([string]$ShortcutPath)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "$shell = New-Object -ComObject WScript.Shell\n"
        "$lnk = $shell.CreateShortcut($ShortcutPath)\n"
        "$lnk.TargetPath\n"
    )

    script_file = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.ps1',
            delete=False,
            encoding='utf-8',
            newline='\n',
        ) as f:
            f.write(ps_script)
            script_file = f.name

        cmd = [
            _get_powershell_executable(),
            '-NoProfile',
            '-ExecutionPolicy',
            'Bypass',
            '-File',
            script_file,
            '-ShortcutPath',
            shortcut_path,
        ]
        rc, stdout, stderr = _run_capture_text(cmd, timeout=10)
        if rc != 0:
            return None, (stderr or stdout or '').strip() or f'return_code={rc}'
        target = (stdout or '').strip()
        return (target or None), ''
    except Exception as e:
        return None, f'{type(e).__name__}: {e}'
    finally:
        if script_file:
            try:
                os.remove(script_file)
            except OSError:
                pass


def _get_shortcut_target_via_pywin32(shortcut_path: str) -> tuple[str | None, str]:
    try:
        import win32com.client

        shell = win32com.client.Dispatch('WScript.Shell')
        lnk = shell.CreateShortcut(shortcut_path)
        target = str(getattr(lnk, 'TargetPath', '') or '').strip()
        return (target or None), ''
    except Exception as e:
        return None, f'{type(e).__name__}: {e}'


def _get_shortcut_target(shortcut_path: str) -> tuple[str | None, str]:
    target, msg = _get_shortcut_target_via_powershell(shortcut_path)
    if target:
        return target, msg
    target2, msg2 = _get_shortcut_target_via_pywin32(shortcut_path)
    if target2:
        return target2, msg2
    return None, msg or msg2


def ensure_desktop_shortcut(script_dir: str) -> None:
    try:
        if not bool(STARTUP.get('ensure_desktop_shortcut', True)):
            logger.info('已关闭启动时快捷方式检查与创建，跳过桌面快捷方式同步')
            return

        script_dir = os.path.abspath(script_dir)
        bat_path = os.path.join(script_dir, '启动程序.bat')
        ico_path = os.path.join(script_dir, 'resc', 'icon.ico')
        if not os.path.exists(bat_path):
            logger.debug('启动脚本不存在，跳过桌面快捷方式同步: %s', bat_path)
            return

        desktop_paths = _collect_desktop_paths()
        if not desktop_paths:
            logger.warning('未找到可用的桌面路径')
            return

        user_desktop = None
        public_desktop = None
        for desktop in desktop_paths:
            desktop_lower = desktop.lower()
            if 'public' in desktop_lower or '公共' in desktop:
                public_desktop = desktop
            else:
                user_desktop = desktop

        icon_for_shortcut = ico_path if os.path.exists(ico_path) else ''
        errors: list[str] = []

        def _try_create_shortcut(desktop_path: str) -> bool:
            shortcut_path = os.path.join(desktop_path, '飞行雪绒.lnk')
            expected_target = _normalize_file_path_for_compare(bat_path)

            if os.path.exists(shortcut_path):
                current_target, read_msg = _get_shortcut_target(shortcut_path)
                current_target_norm = _normalize_file_path_for_compare(current_target or '')
                if current_target and _paths_refer_same_file(current_target, bat_path):
                    logger.info('桌面快捷方式目标已正确（samefile），跳过重建: %s', shortcut_path)
                    return True
                if current_target_norm and current_target_norm == expected_target:
                    logger.info('桌面快捷方式目标已正确，跳过重建: %s', shortcut_path)
                    return True

                if current_target:
                    logger.info(
                        '桌面快捷方式目标不匹配，准备重建: %s (current=%s, expected=%s)',
                        shortcut_path,
                        current_target,
                        bat_path,
                    )
                else:
                    logger.debug(
                        '读取桌面快捷方式目标失败，按不匹配处理: %s (%s)',
                        shortcut_path,
                        read_msg,
                    )

                try:
                    os.remove(shortcut_path)
                    logger.info('已删除旧桌面快捷方式: %s', shortcut_path)
                except OSError as e:
                    errors.append(f'delete@{desktop_path}: {type(e).__name__}({e})')
                    logger.warning('删除旧桌面快捷方式失败: %s (%s)', shortcut_path, e)
                    return False

            ok, msg = _create_shortcut_via_powershell(
                shortcut_path=shortcut_path,
                target_path=bat_path,
                working_dir=script_dir,
                description='飞行雪绒桌面宠物',
                icon_path=icon_for_shortcut,
            )
            if ok:
                logger.info('桌面快捷方式已创建: %s', shortcut_path)
                return True
            errors.append(f'PowerShell@{desktop_path}: {msg}')
            logger.debug('PowerShell 创建快捷方式失败: %s (%s)', desktop_path, msg)

            ok, msg = _create_shortcut_via_pywin32(
                shortcut_path=shortcut_path,
                target_path=bat_path,
                working_dir=script_dir,
                description='飞行雪绒桌面宠物',
                icon_path=icon_for_shortcut,
            )
            if ok:
                logger.info('桌面快捷方式已创建(pywin32): %s', shortcut_path)
                return True
            errors.append(f'pywin32@{desktop_path}: {msg}')
            logger.debug('pywin32 创建快捷方式失败: %s (%s)', desktop_path, msg)

            return False

        if user_desktop and _try_create_shortcut(user_desktop):
            logger.debug('用户桌面快捷方式处理完成')
            return

        if public_desktop and _try_create_shortcut(public_desktop):
            logger.debug('公共桌面快捷方式处理完成')
            return

        if errors:
            logger.warning('创建桌面快捷方式失败: %s', ' | '.join(errors[-4:]))
        else:
            logger.warning('未找到可用的桌面路径')
    except PermissionError as e:
        logger.warning('无权限创建桌面快捷方式: %s', e)
    except Exception as e:
        logger.warning('创建桌面快捷方式失败: %s (%s)', type(e).__name__, e)
