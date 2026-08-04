"""启动鸣潮按钮 - 检测并启动鸣潮（含安装应用列表兜底）"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from PyQt5.QtWidgets import QWidget, QGraphicsOpacityEffect
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve, QPoint
from PyQt5.QtGui import QPainter

from config.config import CLOUD_MUSIC, UI
from lib.core.qt_bridge.colors import COLORS
from lib.core.qt_bridge.font import get_ui_font
from config.scale import scale_px
from config.tooltip_config import TOOLTIPS
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.unified_draw import Layer, get_layer_manager
from lib.core.qt_bridge.screen import clamp_rect_position
from lib.core.anchor_utils import apply_ui_opacity
from lib.script.ui.rect_action_button_style import paint_rect_action_button


class LaunchWutheringWavesButton(QWidget):
    """
    启动鸣潮按钮。

    布局规则：
      - 左下锚点对齐 clickthrough_button 左上锚点。
    """

    WIDTH = scale_px(80, min_abs=1)
    HEIGHT = scale_px(32, min_abs=1)
    _EXE_CANDIDATE_NAMES = (
        "Wuthering Waves.exe",
        "launcher.exe",
        "launcher_epic.exe",
        "KRLauncher.exe",
    )
    _EXE_CANDIDATE_NAME_SET = {name.lower() for name in _EXE_CANDIDATE_NAMES}
    _SUPPORTED_LAUNCH_EXTS = {".exe", ".bat", ".lnk"}
    _APP_KEYWORDS = (
        "鸣潮",
        "wuthering waves",
        "wutheringwaves",
        "wuthering",
        "wuwa",
    )

    def __init__(self, clickthrough_button=None):
        super().__init__()
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        get_layer_manager().register(self, Layer.PET_UI)

        self._clickthrough_button = clickthrough_button
        self._visible = False
        self._description = TOOLTIPS.get('launch_wuwa_button', '检测并启动鸣潮')
        self._hovered = False
        self._cached_exe_path: str | None = None
        self._cached_app_id: str | None = None
        self._ui_id = 'launch_wuwa_button'
        self._target_ui_id = 'clickthrough_button'

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._anim = QPropertyAnimation(self._opacity, b'opacity', self)
        self._anim.setDuration(UI['ui_fade_duration'])
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)

        self._font = get_ui_font()
        self._font.setBold(True)

        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.FRAME, self._on_frame)
        self._event_center.subscribe(EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)

    def paintEvent(self, event):
        painter = QPainter(self)
        paint_rect_action_button(painter, self.rect(), self._font, '启动鸣潮', hovered=self._hovered)

    def _on_frame(self, event):
        if self._visible:
            self._update_position()

    def _on_anchor_response(self, event):
        """锚点响应事件处理 - 上游按钮移动时立即跟随，避免可见滞后。"""
        if not self._visible:
            return

        ui_id = event.data.get('ui_id')
        window_id = event.data.get('window_id')
        anchor_id = event.data.get('anchor_id')

        if ui_id == self._ui_id and window_id == self._target_ui_id:
            self._update_position()
        elif ui_id == 'all' and window_id == self._target_ui_id and anchor_id == 'all':
            self._update_position()

    def _update_position(self):
        if not self._clickthrough_button:
            return

        btn_x = self._clickthrough_button.x()
        btn_y = self._clickthrough_button.y()

        # 左下锚点对齐 clickthrough_button 左上锚点
        new_x = btn_x
        new_y = btn_y - self.HEIGHT

        x, y, _ = clamp_rect_position(
            new_x,
            new_y,
            self.WIDTH,
            self.HEIGHT,
            point=QPoint(btn_x, btn_y),
            fallback_widget=self,
        )

        if self.x() != x or self.y() != y:
            self.move(x, y)

    def fade_in(self):
        if self._visible:
            return
        self._visible = True
        self.show()
        self._update_position()
        self._animate(1.0)

    def fade_out(self):
        if not self._visible:
            return
        self._visible = False

        rect = self.geometry()
        self._anim.finished.connect(self._on_fade_out_complete)
        self._animate(0.0)

        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            'particle_id': 'right_fade',
            'area_type': 'rect',
            'area_data': (rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height())
        }))

    def _on_fade_out_complete(self):
        try:
            self._anim.finished.disconnect(self._on_fade_out_complete)
        except TypeError:
            pass
        self.hide()

    def _animate(self, target: float):
        self._anim.stop()
        self._anim.setStartValue(self._opacity.opacity())
        self._anim.setEndValue(apply_ui_opacity(target))
        self._anim.start()

    def _on_clickthrough_toggle(self, event: Event) -> None:
        self.setAttribute(Qt.WA_TransparentForMouseEvents, event.data.get('enabled', False))

    def closeEvent(self, event):
        self._event_center.unsubscribe(EventType.FRAME, self._on_frame)
        self._event_center.unsubscribe(EventType.UI_ANCHOR_RESPONSE, self._on_anchor_response)
        self._event_center.unsubscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_toggle)
        super().closeEvent(event)

    def mousePressEvent(self, event):
        from lib.script.ui._particle_helper import publish_click_particle
        publish_click_particle(self, event)
        if event.button() == Qt.LeftButton:
            self._launch_wuthering_waves()

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    @staticmethod
    def _project_root() -> Path:
        try:
            return Path(__file__).resolve().parents[3]
        except Exception:
            return Path.cwd()

    def _normalize_launch_path(self, raw_path: str) -> str:
        raw = str(raw_path or "").strip().strip('"')
        if not raw:
            return ""
        expanded = os.path.expandvars(os.path.expanduser(raw))
        candidate = Path(expanded)
        if not candidate.is_absolute():
            candidate = self._project_root() / candidate
        return os.path.normpath(str(candidate))

    def _get_configured_launch_path(self) -> str:
        configured = CLOUD_MUSIC.get("launch_wuwa_path", "")
        return self._normalize_launch_path(str(configured or ""))

    def _is_supported_launch_file(self, path: str) -> bool:
        if not path:
            return False
        suffix = Path(path).suffix.lower()
        if suffix not in self._SUPPORTED_LAUNCH_EXTS:
            return False
        return os.path.isfile(path)

    def _launch_via_configured_path(self, path: str) -> None:
        os.startfile(path)  # type: ignore[attr-defined]

    def _launch_wuthering_waves(self) -> None:
        configured_path = self._get_configured_launch_path()
        if configured_path:
            if not self._is_supported_launch_file(configured_path):
                self._event_center.publish(Event(EventType.INFORMATION, {
                    'text': f'启动鸣潮路径无效：{configured_path}（仅支持 .exe/.bat/.lnk）',
                    'min': 0,
                    'max': 120,
                }))
                return
            try:
                self._launch_via_configured_path(configured_path)
                self._event_center.publish(Event(EventType.INFORMATION, {
                    'text': '已通过配置路径启动鸣潮...',
                    'min': 0,
                    'max': 60,
                }))
            except Exception as e:
                self._event_center.publish(Event(EventType.INFORMATION, {
                    'text': f'配置路径启动鸣潮失败: {e}',
                    'min': 0,
                    'max': 120,
                }))
            return

        shortcut_path = self._find_named_desktop_shortcut()
        if shortcut_path:
            try:
                os.startfile(shortcut_path)  # type: ignore[attr-defined]
                self._event_center.publish(Event(EventType.INFORMATION, {
                    'text': '已通过桌面快捷方式启动鸣潮...',
                    'min': 0,
                    'max': 60,
                }))
                return
            except Exception as e:
                self._event_center.publish(Event(EventType.INFORMATION, {
                    'text': f'快捷方式启动失败，尝试直接拉起: {e}',
                    'min': 0,
                    'max': 90,
                }))

        exe_path = self._find_wuthering_waves_exe()
        if exe_path:
            try:
                subprocess.Popen([exe_path], cwd=os.path.dirname(exe_path) or None)
                self._event_center.publish(Event(EventType.INFORMATION, {
                    'text': '正在启动鸣潮...',
                    'min': 0,
                    'max': 60,
                }))
                return
            except Exception as e:
                self._event_center.publish(Event(EventType.INFORMATION, {
                    'text': f'启动鸣潮失败: {e}',
                    'min': 0,
                    'max': 120,
                }))
                return

        app_id = self._find_installed_app_id()
        if app_id:
            if self._launch_via_installed_app_id(app_id):
                self._event_center.publish(Event(EventType.INFORMATION, {
                    'text': '已从应用安装列表启动鸣潮...',
                    'min': 0,
                    'max': 60,
                }))
                return
            self._event_center.publish(Event(EventType.INFORMATION, {
                'text': '检测到鸣潮应用，但通过安装列表启动失败',
                'min': 0,
                'max': 90,
            }))
            return

        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': '未检测到鸣潮：已尝试桌面快捷方式、可执行文件、安装应用列表',
            'min': 0,
            'max': 110,
        }))

    def _find_named_desktop_shortcut(self) -> str | None:
        desktops = [
            os.path.join(os.path.expanduser("~"), "Desktop"),
            os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop"),
        ]
        explicit_names = ("鸣潮.lnk", "Wuthering Waves.lnk", "WutheringWaves.lnk")

        # 优先查找常见命名
        for root in desktops:
            for name in explicit_names:
                p = os.path.join(root, name)
                if os.path.isfile(p):
                    return p

        # 再按文件名关键词兜底
        for root in desktops:
            if not os.path.isdir(root):
                continue
            try:
                for name in os.listdir(root):
                    if not name.lower().endswith(".lnk"):
                        continue
                    base = os.path.splitext(name)[0]
                    if self._name_matches_keywords(base):
                        p = os.path.join(root, name)
                        if os.path.isfile(p):
                            return p
            except Exception:
                continue
        return None

    def _find_wuthering_waves_exe(self) -> str | None:
        if self._cached_exe_path and self._is_supported_launch_exe(self._cached_exe_path):
            return self._cached_exe_path

        for finder in (
            self._find_via_registry,
            self._find_via_desktop_shortcuts,
        ):
            path = finder()
            if path and os.path.isfile(path):
                self._cached_exe_path = path
                return path
        return None

    def _name_matches_keywords(self, value: str) -> bool:
        text = (value or "").strip().lower()
        if not text:
            return False
        return any(keyword in text for keyword in self._APP_KEYWORDS)

    def _is_supported_launch_exe(self, path: str) -> bool:
        if not path:
            return False
        path = path.strip().strip('"')
        if not os.path.isfile(path):
            return False
        file_name = os.path.basename(path).lower()
        return file_name in self._EXE_CANDIDATE_NAME_SET

    def _find_launch_exe_in_dir(self, directory: str) -> str | None:
        if not directory:
            return None
        directory = directory.strip().strip('"')
        if not os.path.isdir(directory):
            return None

        sub_dirs = (
            "",
            "Wuthering Waves Game",
            "launcher",
            os.path.join("Client", "Binaries", "Win64"),
        )
        for sub in sub_dirs:
            root = os.path.join(directory, sub) if sub else directory
            for exe_name in self._EXE_CANDIDATE_NAMES:
                p = os.path.join(root, exe_name)
                if os.path.isfile(p):
                    return p
        return None

    def _find_via_registry(self) -> str | None:
        try:
            import winreg
        except Exception:
            return None

        def _extract_path(raw: str) -> str | None:
            if not raw:
                return None
            raw = os.path.expandvars(raw.strip())
            if not raw:
                return None
            if raw.startswith('"'):
                end = raw.find('"', 1)
                if end > 1:
                    p = raw[1:end].strip()
                    if os.path.isfile(p):
                        return p

            match = re.search(r'([A-Za-z]:\\[^"\r\n]*?\.exe)', raw, flags=re.IGNORECASE)
            if match:
                p = match.group(1).strip().strip('"')
                if os.path.isfile(p):
                    return p
            return None

        def _check_dir(path: str) -> str | None:
            if not path:
                return None
            path = path.strip().strip('"')
            path = os.path.expandvars(path)

            if os.path.isfile(path):
                if self._is_supported_launch_exe(path):
                    return path
                directory = os.path.dirname(path)
                return self._find_launch_exe_in_dir(directory)

            if not os.path.isdir(path):
                return None
            direct = self._find_launch_exe_in_dir(path)
            if direct:
                return direct
            return None

        roots = [
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        keys = ("DisplayName", "DisplayIcon", "InstallLocation", "UninstallString", "QuietUninstallString")

        for hive, base in roots:
            try:
                with winreg.OpenKey(hive, base) as parent:
                    sub_count = winreg.QueryInfoKey(parent)[0]
                    for i in range(sub_count):
                        try:
                            sub = winreg.EnumKey(parent, i)
                            with winreg.OpenKey(hive, f"{base}\\{sub}") as k:
                                display_name = ""
                                try:
                                    display_name = str(winreg.QueryValueEx(k, "DisplayName")[0])
                                except Exception:
                                    pass
                                if not self._name_matches_keywords(display_name):
                                    continue
                                for key in keys:
                                    try:
                                        val = str(winreg.QueryValueEx(k, key)[0])
                                    except Exception:
                                        continue
                                    p = _extract_path(val)
                                    if p and self._is_supported_launch_exe(p):
                                        return p
                                    p = _check_dir(p or "")
                                    if p:
                                        return p
                                    p = _check_dir(val)
                                    if p:
                                        return p
                        except Exception:
                            continue
            except Exception:
                continue
        return None

    def _find_via_desktop_shortcuts(self) -> str | None:
        script = rf"""
$ErrorActionPreference = 'SilentlyContinue'
$exeNames = @({','.join([f"'{name}'" for name in self._EXE_CANDIDATE_NAMES])})
$exeNamesLower = $exeNames | ForEach-Object {{ $_.ToLowerInvariant() }}
$paths = @(
    [Environment]::GetFolderPath('Desktop'),
    "$env:PUBLIC\Desktop"
) | Where-Object {{ $_ -and (Test-Path -LiteralPath $_) }}
$ws = New-Object -ComObject WScript.Shell
$regex = '([A-Za-z]:\\[^""]*?\.exe)'
foreach ($root in $paths) {{
    Get-ChildItem -LiteralPath $root -Filter *.lnk -File | ForEach-Object {{
        $lnk = $ws.CreateShortcut($_.FullName)
        $target = [string]$lnk.TargetPath
        $args = [string]$lnk.Arguments
        $work = [string]$lnk.WorkingDirectory
        $icon = [string]$lnk.IconLocation
        $name = [string]$_.BaseName
        $hay = ($name + ' ' + $target + ' ' + $args + ' ' + $work + ' ' + $icon).ToLower()
        if ($hay -notmatch 'wuthering|waves|鸣潮') {{ return }}
        $candidates = @()
        if ($target) {{ $candidates += $target }}
        if ($work) {{
            foreach ($exe in $exeNames) {{
                $candidates += (Join-Path $work $exe)
            }}
        }}
        if ($icon) {{
            $iconPath = $icon.Split(',')[0].Trim().Trim('"')
            if ($iconPath) {{ $candidates += $iconPath }}
        }}
        foreach ($m in [regex]::Matches(($args + ' ' + $target), $regex)) {{
            $candidates += $m.Groups[1].Value
        }}
        foreach ($c in $candidates) {{
            if (-not $c) {{ continue }}
            $p = $c.Trim().Trim('"')
            if ((Test-Path -LiteralPath $p) -and ($exeNamesLower -contains (Split-Path -Leaf $p).ToLower())) {{
                Write-Output $p
                exit 0
            }}
        }}
    }}
}}
"""
        for shell in ("powershell", "pwsh"):
            try:
                r = subprocess.run(
                    [shell, "-NoProfile", "-Command", script],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    timeout=6,
                )
            except Exception:
                continue
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    p = line.strip().strip('"')
                    if p and self._is_supported_launch_exe(p):
                        return p
        return None

    def _find_installed_app_id(self) -> str | None:
        if self._cached_app_id:
            return self._cached_app_id

        script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$keywords = @('鸣潮', 'wuthering waves', 'wutheringwaves', 'wuthering', 'wuwa')
$apps = Get-StartApps
foreach ($app in $apps) {
    $name = [string]$app.Name
    if (-not $name) { continue }
    $lower = $name.ToLowerInvariant()
    $matched = $false
    foreach ($kw in $keywords) {
        if ($lower.Contains($kw)) {
            $matched = $true
            break
        }
    }
    if (-not $matched) { continue }
    $appId = [string]$app.AppID
    if ($appId) {
        Write-Output $appId
        exit 0
    }
}
exit 1
"""
        for shell in ("powershell", "pwsh"):
            try:
                r = subprocess.run(
                    [shell, "-NoProfile", "-Command", script],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    timeout=8,
                )
            except Exception:
                continue
            if r.returncode != 0:
                continue
            for line in r.stdout.splitlines():
                app_id = line.strip()
                if app_id:
                    self._cached_app_id = app_id
                    return app_id
        return None

    def _launch_via_installed_app_id(self, app_id: str) -> bool:
        app_id = (app_id or "").strip()
        if not app_id:
            return False
        app_uri = f"shell:AppsFolder\\{app_id}"

        launch_commands = [
            ["explorer.exe", app_uri],
            ["powershell", "-NoProfile", "-Command", f'Start-Process "{app_uri}"'],
            ["pwsh", "-NoProfile", "-Command", f'Start-Process "{app_uri}"'],
        ]
        for cmd in launch_commands:
            try:
                subprocess.Popen(cmd)
                return True
            except Exception:
                continue
        return False
