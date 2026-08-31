"""Toolkit-free Wuthering Waves discovery and launch service."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from config.config import CLOUD_MUSIC
from lib.core.event.center import Event, EventType, get_event_center


class WutheringWavesLauncher:
    """Resolve the configured or installed game and launch it on Windows."""

    _EXE_NAMES = (
        "Wuthering Waves.exe",
        "launcher.exe",
        "launcher_epic.exe",
        "KRLauncher.exe",
    )
    _EXE_NAME_SET = {name.lower() for name in _EXE_NAMES}
    _SUPPORTED_EXTENSIONS = {".exe", ".bat", ".lnk"}
    _KEYWORDS = ("鸣潮", "wuthering waves", "wutheringwaves", "wuthering", "wuwa")

    def __init__(self) -> None:
        self._cached_executable = ""
        self._cached_app_id = ""

    @staticmethod
    def _project_root() -> Path:
        return Path(__file__).resolve().parents[3]

    @staticmethod
    def _publish(text: str, maximum: int) -> None:
        get_event_center().publish(Event(EventType.INFORMATION, {
            "text": text,
            "min": 0,
            "max": maximum,
        }))

    def normalize_path(self, raw_path: object) -> str:
        raw = str(raw_path or "").strip().strip('"')
        if not raw:
            return ""
        candidate = Path(os.path.expandvars(os.path.expanduser(raw)))
        if not candidate.is_absolute():
            candidate = self._project_root() / candidate
        return os.path.normpath(str(candidate))

    def launch(self) -> bool:
        configured = self.normalize_path(CLOUD_MUSIC.get("launch_wuwa_path", ""))
        if configured:
            if not self._is_supported_launch_file(configured):
                self._publish(
                    f"启动鸣潮路径无效：{configured}（仅支持 .exe/.bat/.lnk）",
                    120,
                )
                return False
            try:
                os.startfile(configured)  # type: ignore[attr-defined]
            except Exception as exc:
                self._publish(f"配置路径启动鸣潮失败: {exc}", 120)
                return False
            self._publish("已通过配置路径启动鸣潮...", 60)
            return True

        shortcut = self._find_named_desktop_shortcut()
        if shortcut:
            try:
                os.startfile(shortcut)  # type: ignore[attr-defined]
            except Exception as exc:
                self._publish(f"快捷方式启动失败，尝试直接拉起: {exc}", 90)
            else:
                self._publish("已通过桌面快捷方式启动鸣潮...", 60)
                return True

        executable = self._find_executable()
        if executable:
            try:
                subprocess.Popen(
                    [executable],
                    cwd=os.path.dirname(executable) or None,
                )
            except Exception as exc:
                self._publish(f"启动鸣潮失败: {exc}", 120)
                return False
            self._publish("正在启动鸣潮...", 60)
            return True

        app_id = self._find_installed_app_id()
        if app_id:
            if self._launch_app_id(app_id):
                self._publish("已从应用安装列表启动鸣潮...", 60)
                return True
            self._publish("检测到鸣潮应用，但通过安装列表启动失败", 90)
            return False

        self._publish("未检测到鸣潮：已尝试桌面快捷方式、可执行文件、安装应用列表", 110)
        return False

    def _is_supported_launch_file(self, path: str) -> bool:
        return Path(path).suffix.lower() in self._SUPPORTED_EXTENSIONS and os.path.isfile(path)

    def _is_supported_executable(self, path: str) -> bool:
        return (
            bool(path)
            and os.path.isfile(path)
            and os.path.basename(path).lower() in self._EXE_NAME_SET
        )

    @classmethod
    def _matches(cls, value: object) -> bool:
        text = str(value or "").strip().lower()
        return bool(text) and any(keyword in text for keyword in cls._KEYWORDS)

    @staticmethod
    def _desktop_directories() -> tuple[str, ...]:
        return (
            os.path.join(os.path.expanduser("~"), "Desktop"),
            os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop"),
        )

    def _find_named_desktop_shortcut(self) -> str:
        explicit_names = ("鸣潮.lnk", "Wuthering Waves.lnk", "WutheringWaves.lnk")
        for root in self._desktop_directories():
            for name in explicit_names:
                path = os.path.join(root, name)
                if os.path.isfile(path):
                    return path
            if not os.path.isdir(root):
                continue
            try:
                for name in os.listdir(root):
                    if name.lower().endswith(".lnk") and self._matches(Path(name).stem):
                        path = os.path.join(root, name)
                        if os.path.isfile(path):
                            return path
            except OSError:
                continue
        return ""

    def _find_executable(self) -> str:
        if self._is_supported_executable(self._cached_executable):
            return self._cached_executable
        path = self._find_from_registry() or self._resolve_desktop_shortcuts()
        if self._is_supported_executable(path):
            self._cached_executable = path
            return path
        return ""

    def _find_in_directory(self, directory: str) -> str:
        if not os.path.isdir(directory):
            return ""
        for relative in ("", "Wuthering Waves Game", "launcher", r"Client\Binaries\Win64"):
            root = os.path.join(directory, relative) if relative else directory
            for name in self._EXE_NAMES:
                candidate = os.path.join(root, name)
                if os.path.isfile(candidate):
                    return candidate
        return ""

    def _candidate_from_registry_value(self, value: object) -> str:
        raw = os.path.expandvars(str(value or "").strip())
        if not raw:
            return ""
        candidates = []
        quoted = re.match(r'^"([^"\r\n]+\.exe)"', raw, flags=re.IGNORECASE)
        if quoted:
            candidates.append(quoted.group(1))
        match = re.search(r'([A-Za-z]:\\[^"\r\n]*?\.exe)', raw, flags=re.IGNORECASE)
        if match:
            candidates.append(match.group(1).strip())
        candidates.append(raw.strip('"'))
        for candidate in candidates:
            if self._is_supported_executable(candidate):
                return candidate
            directory = candidate if os.path.isdir(candidate) else os.path.dirname(candidate)
            found = self._find_in_directory(directory)
            if found:
                return found
        return ""

    def _find_from_registry(self) -> str:
        try:
            import winreg
        except ImportError:
            return ""
        roots = (
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        )
        value_names = ("DisplayIcon", "InstallLocation", "UninstallString", "QuietUninstallString")
        for hive, root_name in roots:
            try:
                with winreg.OpenKey(hive, root_name) as parent:
                    count = winreg.QueryInfoKey(parent)[0]
                    for index in range(count):
                        try:
                            key_name = winreg.EnumKey(parent, index)
                            with winreg.OpenKey(parent, key_name) as key:
                                display = str(winreg.QueryValueEx(key, "DisplayName")[0])
                                if not self._matches(display):
                                    continue
                                for value_name in value_names:
                                    try:
                                        value = winreg.QueryValueEx(key, value_name)[0]
                                    except OSError:
                                        continue
                                    candidate = self._candidate_from_registry_value(value)
                                    if candidate:
                                        return candidate
                        except OSError:
                            continue
            except OSError:
                continue
        return ""

    def _run_powershell(self, script: str, timeout: int) -> tuple[str, ...]:
        for shell in ("powershell", "pwsh"):
            try:
                result = subprocess.run(
                    [shell, "-NoProfile", "-Command", script],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    timeout=timeout,
                )
            except Exception:
                continue
            if result.returncode == 0:
                return tuple(line.strip().strip('"') for line in result.stdout.splitlines() if line.strip())
        return ()

    def _resolve_desktop_shortcuts(self) -> str:
        names = ",".join(f"'{name}'" for name in self._EXE_NAMES)
        script = rf"""
$ErrorActionPreference = 'SilentlyContinue'
$names = @({names})
$shell = New-Object -ComObject WScript.Shell
foreach ($root in @([Environment]::GetFolderPath('Desktop'), "$env:PUBLIC\Desktop")) {{
  if (-not $root -or -not (Test-Path -LiteralPath $root)) {{ continue }}
  foreach ($item in Get-ChildItem -LiteralPath $root -Filter *.lnk -File) {{
    $link = $shell.CreateShortcut($item.FullName)
    $hay = ($item.BaseName + ' ' + $link.TargetPath + ' ' + $link.Arguments).ToLowerInvariant()
    if ($hay -notmatch 'wuthering|waves|鸣潮|wuwa') {{ continue }}
    foreach ($candidate in @($link.TargetPath, $link.WorkingDirectory)) {{
      if (-not $candidate) {{ continue }}
      if (Test-Path -LiteralPath $candidate -PathType Leaf) {{ Write-Output $candidate; exit 0 }}
      foreach ($name in $names) {{
        $path = Join-Path $candidate $name
        if (Test-Path -LiteralPath $path -PathType Leaf) {{ Write-Output $path; exit 0 }}
      }}
    }}
  }}
}}
exit 1
"""
        for path in self._run_powershell(script, 6):
            if self._is_supported_executable(path):
                return path
        return ""

    def _find_installed_app_id(self) -> str:
        if self._cached_app_id:
            return self._cached_app_id
        script = r"""
$ErrorActionPreference = 'SilentlyContinue'
foreach ($app in Get-StartApps) {
  $name = [string]$app.Name
  if ($name -and $name.ToLowerInvariant() -match 'wuthering|waves|鸣潮|wuwa') {
    if ($app.AppID) { Write-Output ([string]$app.AppID); exit 0 }
  }
}
exit 1
"""
        values = self._run_powershell(script, 8)
        self._cached_app_id = values[0] if values else ""
        return self._cached_app_id

    @staticmethod
    def _launch_app_id(app_id: str) -> bool:
        uri = f"shell:AppsFolder\\{str(app_id or '').strip()}"
        if uri.endswith("\\"):
            return False
        for command in (
            ["explorer.exe", uri],
            ["powershell", "-NoProfile", "-Command", f'Start-Process "{uri}"'],
            ["pwsh", "-NoProfile", "-Command", f'Start-Process "{uri}"'],
        ):
            try:
                subprocess.Popen(command)
                return True
            except Exception:
                continue
        return False


_launcher: WutheringWavesLauncher | None = None


def get_wuthering_waves_launcher() -> WutheringWavesLauncher:
    global _launcher
    if _launcher is None:
        _launcher = WutheringWavesLauncher()
    return _launcher


__all__ = ["WutheringWavesLauncher", "get_wuthering_waves_launcher"]
