"""PyPI mirror selection and desktop dependency installation."""

import hashlib
import os
import socket
import tempfile
import time
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .bootstrap import (
    _fmt_ver,
    _get_version,
    _pkg_installed,
    _python_module_cmd,
    _run,
)
from .catalog import (
    BINARY_ONLY_PACKAGES,
    DEPENDENCIES,
    JIEBA_FAST_PACKAGE,
    JIEBA_FAST_WHEEL_NAME,
    JIEBA_FAST_WHEEL_SHA256,
    PACKAGE_REQUIREMENTS,
    PYPI_MIRRORS,
    RESOURCE_SOURCE_HOSTS,
    TARGET_PYTHON,
)
from .console import _print_kind, _print_stage, _print_warn
from .progress import (
    _ANSI_ESCAPE_PATTERN,
    _DependencyCheckProgressDisplay,
    _DependencyProgressDisplay,
    _MonotonicProgressReporter,
    _run_pip_requirement_with_progress,
)
from .resources import (
    _resource_urls,
    _stream_download_with_progress,
    _unlink_if_exists,
)


_NOT_FOUND_MARKERS = (
    "no matching distribution found",
    "could not find a version that satisfies",
    "no distributions at all",
)

def _tcp_ms(host, port=443, timeout=4.0):
    """Return TCP connect latency in milliseconds, or inf if unreachable."""
    try:
        start = time.perf_counter()
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return (time.perf_counter() - start) * 1000
    except Exception:
        return float("inf")

def benchmark_mirrors():
    _print_stage(2, "并发测试依赖镜像延迟...")
    scored = []
    # Probe all mirrors at once so one unavailable host cannot add its timeout
    # to every other mirror. Ordering is restored below for deterministic logs.
    with ThreadPoolExecutor(max_workers=min(8, len(PYPI_MIRRORS))) as executor:
        latencies = list(executor.map(lambda mirror: _tcp_ms(mirror["host"], timeout=2.0), PYPI_MIRRORS))

    for mirror, lat in zip(PYPI_MIRRORS, latencies):
        if lat == float("inf"):
            print(f"  {mirror['name']:<10} unreachable")
        else:
            print(f"  {mirror['name']:<10} {lat:>6.0f} ms")
        scored.append((lat, mirror))

    scored.sort(key=lambda x: x[0])
    reachable = [m for lat, m in scored if lat < float("inf")]
    unreachable = [m for lat, m in scored if lat == float("inf")]

    if reachable:
        best_lat = next(lat for lat, m in scored if m is reachable[0])
        _print_kind(f"\n  -> 最优镜像: {reachable[0]['name']} ({best_lat:.0f} ms)", "ok", prefix=False)
    else:
        _print_warn("\n  -> 所有镜像均不可达，将逐一尝试")

    return reachable + unreachable

def _run_pip_install_with_progress(python_exe, pkg, mirror, progress_callback):
    return _run_pip_requirement_with_progress(
        python_exe,
        PACKAGE_REQUIREMENTS.get(pkg, pkg),
        progress_callback,
        mirror=mirror,
        only_binary=pkg if pkg in BINARY_ONLY_PACKAGES else None,
    )

def _summarize_pip_failure(output):
    lines = []
    for raw_line in str(output or "").splitlines():
        line = _ANSI_ESCAPE_PATTERN.sub("", raw_line).strip()
        if line:
            lines.append(line)
    if not lines:
        return "pip 未返回错误详情"

    preferred = [
        line
        for line in lines
        if line.lower().startswith(("error:", "option "))
        or "subprocess-exited-with-error" in line.lower()
        or "failed building wheel" in line.lower()
    ]
    selected = preferred[-3:] if preferred else lines[-3:]
    summary = " | ".join(dict.fromkeys(selected))
    return summary if len(summary) <= 900 else summary[:897] + "..."

def _install_jieba_fast_wheel(python_exe, progress_callback):
    progress_callback = _MonotonicProgressReporter(progress_callback)
    version = _get_version(python_exe)
    architecture = _run(
        [
            python_exe,
            "-c",
            "import struct; print(struct.calcsize('P') * 8)",
        ]
    )
    is_64_bit = (
        architecture is not None
        and architecture.returncode == 0
        and (architecture.stdout or "").strip() == "64"
    )
    if version[:2] != TARGET_PYTHON or not is_64_bit:
        detected = _fmt_ver(version) if version != (0, 0, 0) else "未知版本"
        return (
            False,
            f"预编译 wheel 仅支持 64 位 Python 3.11，当前解释器为 {detected}",
        )

    urls = _resource_urls(JIEBA_FAST_WHEEL_NAME)
    if not urls:
        return False, f"resc.net.txt 中未找到 {JIEBA_FAST_WHEEL_NAME}"

    with tempfile.TemporaryDirectory(prefix="aemeath-jieba-fast-") as temp_dir:
        wheel_path = Path(temp_dir) / JIEBA_FAST_WHEEL_NAME
        part_path = wheel_path.with_name(wheel_path.name + ".part")
        last_failure = "wheel 下载失败"
        for index, url in enumerate(urls, start=1):
            source_name = RESOURCE_SOURCE_HOSTS.get(
                (urllib.parse.urlsplit(url).hostname or "").lower(),
                f"镜像 {index}",
            )
            try:
                _unlink_if_exists(part_path, ignore_errors=True)
                print(
                    f"  下载预编译依赖 [{index}/{len(urls)}]: "
                    f"{JIEBA_FAST_WHEEL_NAME} ({source_name})"
                )
                _stream_download_with_progress(
                    url,
                    part_path,
                    label=JIEBA_FAST_PACKAGE,
                )
                digest = hashlib.sha256(part_path.read_bytes()).hexdigest()
                if digest.lower() != JIEBA_FAST_WHEEL_SHA256:
                    raise ValueError(
                        f"SHA-256 不匹配，期望 {JIEBA_FAST_WHEEL_SHA256}，实际 {digest}"
                    )
                part_path.replace(wheel_path)
                return_code, output = _run_pip_requirement_with_progress(
                    python_exe,
                    wheel_path,
                    progress_callback,
                )
                if return_code == 0:
                    progress_callback(100)
                    return True, ""
                last_failure = f"{source_name}：{_summarize_pip_failure(output)}"
            except (urllib.error.URLError, OSError, ValueError) as exc:
                last_failure = f"{source_name}：{exc}"
            finally:
                _unlink_if_exists(part_path, ignore_errors=True)
                _unlink_if_exists(wheel_path, ignore_errors=True)

        return False, last_failure

def _install_one(python_exe, pkg, mirrors, progress_callback):
    """Install one package with mirror fallback and return a concise failure."""
    if pkg == JIEBA_FAST_PACKAGE:
        return _install_jieba_fast_wheel(python_exe, progress_callback)
    if not mirrors:
        return False, "没有可用的 pip 镜像"

    progress_callback = _MonotonicProgressReporter(progress_callback)
    last_failure = "pip 安装失败"
    for mirror in mirrors:
        return_code, output = _run_pip_install_with_progress(
            python_exe,
            pkg,
            mirror,
            progress_callback,
        )
        if return_code == 0:
            progress_callback(100)
            return True, ""

        last_failure = f"{mirror['name']}：{_summarize_pip_failure(output)}"
        combined = output.lower()
        if any(marker in combined for marker in _NOT_FOUND_MARKERS):
            continue
    return False, last_failure

def install_all(python_exe, mirrors):
    _print_stage(3, "检查并安装桌宠运行依赖...")
    existing = []
    missing = []
    total_checks = len(DEPENDENCIES)
    check_display = _DependencyCheckProgressDisplay()
    check_display.update("准备检查", 0, total_checks, force=True)
    for index, (pkg, desc, import_checks) in enumerate(DEPENDENCIES, start=1):
        check_display.update(pkg, index - 1, total_checks)
        if _pkg_installed(python_exe, pkg, import_checks=import_checks):
            existing.append(pkg)
        else:
            missing.append((pkg, desc, import_checks))
        check_display.update(pkg, index, total_checks)

    try:
        from lib.script.gsvmove.rar_backend import is_bundled_unrar_ready

        unrar_ready = is_bundled_unrar_ready()
    except Exception:
        unrar_ready = False
    if unrar_ready:
        existing.append("UnRAR后端")

    check_display.clear()
    missing_names = [item[0] for item in missing]
    if not unrar_ready:
        missing_names.append("UnRAR后端")
    print("  已有依赖：" + (", ".join(existing) if existing else "无"))
    print("  未安装依赖：" + (", ".join(missing_names) if missing_names else "无"))

    total_jobs = len(missing_names)
    display = _DependencyProgressDisplay()
    if total_jobs == 0:
        display.update("无需安装", 100, 0, 0, force=True)
        _print_kind("\n  所有依赖已安装", "ok", prefix=False)
        return True

    failed = []
    failure_details = {}
    completed_jobs = 0
    for pkg, _desc, _import_checks in missing:
        job_completed = completed_jobs
        display.update(
            pkg,
            5,
            job_completed,
            total_jobs,
            force=True,
            reset=True,
        )

        def report(percent, package=pkg, overall=job_completed):
            display.update(package, percent, overall, total_jobs)

        installed, failure_detail = _install_one(
            python_exe,
            pkg,
            mirrors,
            report,
        )
        if not installed:
            failed.append(pkg)
            failure_details[pkg] = failure_detail
        completed_jobs += 1
        display.update(
            pkg,
            100 if installed else 0,
            completed_jobs,
            total_jobs,
            force=True,
        )

    if not unrar_ready:
        job_completed = completed_jobs
        display.update(
            "UnRAR后端",
            5,
            job_completed,
            total_jobs,
            force=True,
            reset=True,
        )
        unrar_progress = _MonotonicProgressReporter(
            lambda percent: display.update(
                "UnRAR后端",
                percent,
                job_completed,
                total_jobs,
            )
        )
        try:
            from lib.script.gsvmove.rar_backend import ensure_bundled_unrar

            def report_unrar(current, total):
                percent = 0 if total <= 0 else int((current / total) * 100)
                unrar_progress(max(5, percent))

            ensure_bundled_unrar(report_unrar)
            completed_jobs += 1
            display.update("UnRAR后端", 100, completed_jobs, total_jobs, force=True)
        except Exception:
            failed.append("UnRAR后端")
            completed_jobs += 1
            display.update(
                "UnRAR后端",
                0,
                completed_jobs,
                total_jobs,
                force=True,
            )

    if not failed:
        _print_kind("\n  所有依赖已安装", "ok", prefix=False)
        return True

    _print_warn(f"\n  以下依赖安装失败: {', '.join(failed)}")
    if failure_details:
        print("  失败原因：")
        for name in failed:
            detail = failure_details.get(name)
            if detail:
                print(f"    - {name}: {detail}")
    pip_failed = [name for name in failed if name != "UnRAR后端"]
    if pip_failed:
        binary_only = [name for name in pip_failed if name in BINARY_ONLY_PACKAGES]
        manual_args = ["install"]
        if binary_only:
            manual_args.extend(("--only-binary", ",".join(binary_only)))
        manual_args.extend(PACKAGE_REQUIREMENTS.get(name, name) for name in pip_failed)
        print("  可手动执行以下命令：")
        print("    " + " ".join(_python_module_cmd(python_exe, "pip", *manual_args)))
    if "UnRAR后端" in failed:
        print("  随程序提供的 UnRAR 后端缺失，请重新解压完整桌宠程序包。")
    if os.environ.get("FLYING_SNOW_INSTALLER") == "1":
        print("  安装器模式：依赖存在失败项，继续准备 DSH、语音和资源阶段", flush=True)
        return True
    ans = input("\n仍要继续启动吗? (y/n): ").strip().lower()
    return ans == "y"


__all__ = (
    '_NOT_FOUND_MARKERS',
    '_tcp_ms',
    'benchmark_mirrors',
    '_run_pip_install_with_progress',
    '_summarize_pip_failure',
    '_install_jieba_fast_wheel',
    '_install_one',
    'install_all',
)
