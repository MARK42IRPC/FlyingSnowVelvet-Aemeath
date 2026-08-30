"""Monotonic terminal progress rendering for installer commands."""

import os
import queue
import re
import subprocess
import sys
import threading
import time

from .bootstrap import _python_module_cmd
from .console import _COLOR_ENABLED, _COLOR_MAP, _COLOR_RESET, _fmt_color


_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

def _render_dependency_bar(current, total, width=26, *, color_kind=None):
    if total <= 0:
        percent = 100
    else:
        percent = max(0, min(100, int(round((current / total) * 100))))
    filled = int(round((percent / 100) * width))
    complete = "━" * filled
    remaining = "─" * (width - filled)
    if not _COLOR_ENABLED or not color_kind:
        return f"[{complete}{remaining}]"
    complete_color = _COLOR_MAP.get(color_kind, _COLOR_MAP["info"])
    track_color = _COLOR_MAP["progress_track"]
    return (
        f"[{complete_color}{complete}{_COLOR_RESET}"
        f"{track_color}{remaining}{_COLOR_RESET}]"
    )

class _DependencyCheckProgressDisplay:
    """One in-place line for the potentially slow dependency availability scan."""

    def __init__(self):
        self._drawn = False
        self._last_payload = None
        self._line_width = 0

    def update(self, package, current, total, *, force=False):
        checked = max(0, min(int(current), max(0, int(total))))
        total = max(0, int(total))
        payload = (str(package), checked, total)
        if payload == self._last_payload and not force:
            return
        self._last_payload = payload
        percent = 100 if total <= 0 else int(round((checked / total) * 100))
        label = _fmt_color("正在检查依赖", "info")
        bar = _render_dependency_bar(
            checked,
            total,
            color_kind="progress_current",
        )
        value = _fmt_color(f"{percent:>3}%", "progress_value")
        count = _fmt_color(f"{checked}/{total}", "progress_value")
        package_name = _fmt_color(str(package), "progress_current")
        line = f"  {label} {bar} {value}  {count}  {package_name}"
        self._line_width = max(self._line_width, len(line))
        if _COLOR_ENABLED:
            sys.stdout.write(f"\r\033[2K{line}")
        else:
            sys.stdout.write("\r" + line)
        sys.stdout.flush()
        self._drawn = True

    def clear(self):
        if not self._drawn:
            return
        if _COLOR_ENABLED:
            sys.stdout.write("\r\033[2K")
        else:
            sys.stdout.write("\r" + " " * self._line_width + "\r")
        sys.stdout.flush()
        self._drawn = False

class _DependencyProgressDisplay:
    def __init__(self):
        self._drawn = False
        self._last_payload = None
        self._active_package = None
        self._active_percent = 0
        self._overall_total = None
        self._overall_current = 0

    def update(
        self,
        package,
        package_percent,
        overall_current,
        overall_total,
        *,
        force=False,
        reset=False,
    ):
        package = str(package)
        percent = max(0, min(100, int(package_percent)))
        total = max(0, int(overall_total))
        if reset or package != self._active_package:
            self._active_package = package
            self._active_percent = 0
        percent = max(self._active_percent, percent)
        self._active_percent = percent
        if self._overall_total != total:
            self._overall_total = total
            self._overall_current = 0
        current = max(0, min(total, int(overall_current)))
        current = max(self._overall_current, current)
        self._overall_current = current
        payload = (package, percent, current, total)
        if payload == self._last_payload and not force:
            return
        self._last_payload = payload
        package_bar = _render_dependency_bar(
            percent,
            100,
            color_kind="progress_current",
        )
        overall_bar = _render_dependency_bar(
            current,
            total,
            color_kind="progress_overall",
        )
        current_label = _fmt_color("当前依赖", "info")
        overall_label = _fmt_color("整体进度", "stage")
        package_value = _fmt_color(f"{percent:>3}%", "progress_value")
        overall_value = _fmt_color(
            f"{current}/{total}",
            "progress_value",
        )
        package_name = _fmt_color(package, "progress_current")
        first = f"  {current_label} {package_bar} {package_value}  {package_name}"
        second = f"  {overall_label} {overall_bar} {overall_value}"

        # The GUI installer consumes explicit UTF-8 progress records. Keep the
        # regular non-interactive console output compact, while allowing the
        # installer to receive every monotonic update instead of only 5%.
        if os.environ.get("FLYING_SNOW_INSTALLER") == "1":
            print(first, flush=True)
            print(second, flush=True)
            self._drawn = True
            return

        if _COLOR_ENABLED:
            if self._drawn:
                sys.stdout.write("\033[2F")
            sys.stdout.write(f"\033[2K{first}\n\033[2K{second}\n")
            sys.stdout.flush()
            self._drawn = True
            return

        if not self._drawn or force:
            print(first)
            print(second)
            self._drawn = True

_PIP_PROGRESS_STAGES = (
    (95, ("successfully installed", "already satisfied")),
    (78, (
        "installing collected",
        "installing build dependencies",
        "building wheel",
        "running setup.py",
        "running bdist_wheel",
    )),
    (48, ("downloading", "using cached", "using cache", "fetching")),
    (22, (
        "collecting",
        "preparing metadata",
        "getting requirements to build wheel",
        "checking if the build backend supports a build_editable",
    )),
)

def _pip_progress_from_output(line, current):
    """Map pip's coarse log phases to a monotonic, honest progress value."""
    text = _ANSI_ESCAPE_PATTERN.sub("", str(line or "")).replace("\r", " ").strip().lower()
    current = max(0, min(100, int(current)))
    if not text:
        return current
    for value, markers in _PIP_PROGRESS_STAGES:
        if any(marker in text for marker in markers):
            return max(current, value)
    return max(current, 5)

class _MonotonicProgressReporter:
    """Keep retries and noisy callbacks from making one job appear to regress."""

    def __init__(self, callback):
        self._callback = callback
        self._value = 0

    @property
    def value(self):
        return self._value

    def __call__(self, value):
        value = max(0, min(100, int(value)))
        if value < self._value:
            value = self._value
        if value == self._value:
            return
        self._value = value
        self._callback(value)

def _run_pip_requirement_with_progress(
    python_exe,
    requirement,
    progress_callback,
    *,
    mirror=None,
    only_binary=None,
):
    command = _python_module_cmd(
        python_exe,
        "pip",
        "install",
        str(requirement),
        "--no-warn-script-location",
        "--disable-pip-version-check",
        "--progress-bar",
        "off",
    )
    if only_binary:
        command.extend(("--only-binary", str(only_binary)))
    if mirror is not None:
        command.extend(
            (
                "-i",
                mirror["url"],
                "--trusted-host",
                mirror["host"],
            )
        )
    try:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        return 127, f"无法启动 pip：{exc}"
    output_queue = queue.Queue()

    def read_output():
        stream = proc.stdout
        if stream is None:
            output_queue.put(None)
            return
        try:
            for line in stream:
                output_queue.put(line)
        finally:
            output_queue.put(None)

    reader = threading.Thread(target=read_output, daemon=True, name="pip-progress-reader")
    reader.start()
    output_tail = []
    percent = 5
    reader_done = False
    progress_callback(percent)
    while proc.poll() is None or not reader_done:
        try:
            line = output_queue.get(timeout=0.12)
        except queue.Empty:
            line = ""
        if line is None:
            reader_done = True
        elif line:
            output_tail.append(line)
            if len(output_tail) > 160:
                del output_tail[:-160]
            next_percent = _pip_progress_from_output(line, percent)
            if next_percent != percent:
                percent = next_percent
                progress_callback(percent)

    if proc.returncode == 0 and percent < 95:
        progress_callback(95)

    reader.join(timeout=1.0)
    return proc.returncode, "".join(output_tail)

class _RuntimeInstallProgress:
    """Render live stage progress using the same bar as dependency installs."""

    def __init__(self, label, *, width=26):
        self._label = str(label)
        self._width = max(10, int(width))
        self._started = time.monotonic()
        self._percent = 0
        self._last_detail = ""
        self._last_percent = -1
        self._last_draw = 0.0
        self._drawn = False
        self._line_width = 0

    @property
    def percent(self):
        return self._percent

    def update(self, percent=None, detail="", *, force=False):
        now = time.monotonic()
        if percent is not None:
            self._percent = max(self._percent, min(100, int(percent)))
        detail = " ".join(str(detail or "").split())
        if not detail:
            detail = "处理中"
        if (
            not force
            and detail == self._last_detail
            and self._percent == self._last_percent
            and now - self._last_draw < 1.0
        ):
            return
        self._last_detail = detail
        self._last_percent = self._percent
        self._last_draw = now
        elapsed = int(max(0, now - self._started))
        minutes, seconds = divmod(elapsed, 60)
        bar = _render_dependency_bar(
            self._percent,
            100,
            width=self._width,
            color_kind="progress_current",
        )
        value = _fmt_color(f"{self._percent:>3}%", "progress_value")
        label = _fmt_color(self._label, "info")
        line = f"  {label} {bar} {value}  {detail}  [{minutes:02d}:{seconds:02d}]"
        self._line_width = max(self._line_width, len(line))
        # The GUI installer consumes a pipe rather than a terminal. Emit one
        # complete UTF-8 record per update so DSH/npm progress is observable
        # immediately instead of being hidden behind carriage-return redraws.
        if os.environ.get("FLYING_SNOW_INSTALLER") == "1":
            print(line, flush=True)
            self._drawn = True
            return
        if _COLOR_ENABLED:
            sys.stdout.write(f"\r\033[2K{line}")
        else:
            sys.stdout.write("\r" + line)
        sys.stdout.flush()
        self._drawn = True

    def finish(self, detail, *, success=False):
        if success:
            self._percent = 100
        if not self._drawn:
            self.update(self._percent, detail, force=True)
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._drawn = False
            return
        self.update(self._percent, detail, force=True)
        sys.stdout.write("\n")
        sys.stdout.flush()
        self._drawn = False

def _runtime_install_stage(line, *, kind):
    """Translate npm/pip output into monotonic stage percentages and labels."""
    text = _ANSI_ESCAPE_PATTERN.sub("", str(line or ""))
    lowered = text.replace("\r", " ").strip().lower()
    if not lowered:
        return None
    if kind == "npm":
        markers = (
            (12, "准备 lockfile", ("ideal tree", "loadideal", "sill ideal")),
            (28, "解析依赖树", ("reify", "place")),
            (48, "下载依赖", ("http fetch", "fetch manifest", "fetch metadata")),
            (72, "安装依赖", ("extract", "tarball", "unpack")),
            (90, "整理 node_modules", ("reify:load", "reify:save")),
            (96, "依赖安装完成", ("added ", "up to date", "audited ")),
        )
    else:
        markers = (
            (96, "依赖安装完成", ("successfully installed", "already satisfied")),
            (76, "构建/安装依赖", ("installing collected", "building wheel", "running setup.py")),
            (48, "下载 CUDA 依赖", ("downloading", "using cached", "using cache", "fetching")),
            (22, "解析 CUDA 依赖", ("collecting", "preparing metadata", "getting requirements")),
        )
    for percent, label, candidates in markers:
        if any(candidate in lowered for candidate in candidates):
            return percent, label
    return None

def _run_command_with_progress(command, *, label, kind, timeout, cwd=None):
    """Run a long installer command while forwarding useful live status."""
    try:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=str(cwd) if cwd is not None else None,
        )
    except OSError as exc:
        return 127, f"无法启动安装命令：{exc}"

    output_queue = queue.Queue()

    def read_output():
        stream = proc.stdout
        if stream is None:
            output_queue.put(None)
            return
        try:
            for line in stream:
                output_queue.put(line)
        finally:
            output_queue.put(None)

    reader = threading.Thread(
        target=read_output,
        daemon=True,
        name="runtime-install-progress-reader",
    )
    reader.start()
    display = _RuntimeInstallProgress(label)
    display.update(5, "启动安装", force=True)
    output_tail = []
    reader_done = False
    deadline = time.monotonic() + max(1, int(timeout))
    while proc.poll() is None or not reader_done:
        if proc.poll() is None and time.monotonic() >= deadline:
            try:
                proc.kill()
            except OSError:
                pass
            display.finish("超时")
            reader_done = True
            return 124, "安装命令超时"
        try:
            line = output_queue.get(timeout=0.25)
        except queue.Empty:
            display.update(detail="处理中")
            continue
        if line is None:
            reader_done = True
            continue
        output_tail.append(line)
        if len(output_tail) > 160:
            del output_tail[:-160]
        stage = _runtime_install_stage(line, kind=kind)
        if stage is not None:
            percent, detail = stage
            display.update(percent, detail)
        else:
            display.update(detail="处理中")
    reader.join(timeout=1.0)
    return_code = proc.returncode
    display.finish("完成" if return_code == 0 else "失败", success=return_code == 0)
    return return_code, "".join(output_tail)

def _format_bytes(num_bytes):
    size = float(max(0, int(num_bytes or 0)))
    units = ("B", "KB", "MB", "GB")
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)}{unit}"
            return f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}GB"

def _render_transfer_progress(prefix, current, total, start_time):
    elapsed = max(time.perf_counter() - start_time, 1e-6)
    speed = current / elapsed
    speed_text = f"{_format_bytes(speed)}/s"
    current_text = _format_bytes(current)
    if total:
        percent = min(100.0, (current * 100.0) / total)
        total_text = _format_bytes(total)
        bar = _render_dependency_bar(
            current,
            total,
            width=26,
            color_kind="progress_current",
        )
        value = _fmt_color(f"{percent:>6.2f}%", "progress_value")
        return f"{prefix} {bar} {value} {current_text}/{total_text} {speed_text}"
    return f"{prefix} {current_text} {speed_text}"

def _write_progress_line(text: str, *, finish: bool = False) -> None:
    if os.environ.get("FLYING_SNOW_INSTALLER") == "1":
        # Pipe consumers cannot render carriage-return updates reliably.
        # Preserve every transfer sample as a flushed record for the GUI.
        print(text.strip(), flush=True)
        return
    suffix = "\n" if finish else ""
    sys.stdout.write("\r" + text.ljust(120) + suffix)
    sys.stdout.flush()


__all__ = (
    '_ANSI_ESCAPE_PATTERN',
    '_render_dependency_bar',
    '_DependencyCheckProgressDisplay',
    '_DependencyProgressDisplay',
    '_PIP_PROGRESS_STAGES',
    '_pip_progress_from_output',
    '_MonotonicProgressReporter',
    '_run_pip_requirement_with_progress',
    '_RuntimeInstallProgress',
    '_runtime_install_stage',
    '_run_command_with_progress',
    '_format_bytes',
    '_render_transfer_progress',
    '_write_progress_line',
)
