"""Network source selection, downloads, extraction, and resource assets."""

import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .bootstrap import _pkg_installed
from .catalog import (
    PROJECT_ROOT,
    RESOURCE_LINKS_FILE,
    RESOURCE_PING_ATTEMPTS,
    RESOURCE_PING_TIMEOUT_SECONDS,
    RESOURCE_SOURCE_HOSTS,
    SEANIMA_ARCHIVE,
    SEANIMA_RESOURCE_NAME,
    SEANIMA_TARGET_DIR,
    VOSK_MODELS_DIR,
    VOSK_MODEL_MARKERS,
    VOSK_MODEL_SPECS,
)
from .console import _print_stage, _print_warn
from .progress import _render_transfer_progress, _write_progress_line


_RESOURCE_SOURCE_ORDER: tuple[str, ...] | None = None

_NODE_SOURCE_ORDER: tuple[str, ...] | None = None

def _ping_once_ms(host: str, timeout: float = RESOURCE_PING_TIMEOUT_SECONDS) -> float | None:
    timeout = max(0.1, float(timeout))
    if os.name == "nt":
        command = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), host]
    else:
        command = ["ping", "-c", "1", "-W", str(max(1, int(round(timeout)))), host]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="ignore",
            timeout=timeout + 1.0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = result.stdout or ""
    matches = re.findall(r"(?:time|\u65f6\u95f4)?\s*[=<]\s*(\d+(?:\.\d+)?)\s*ms", output, flags=re.IGNORECASE)
    if not matches:
        return None
    try:
        latency = float(matches[-1])
    except (TypeError, ValueError):
        return None
    if "<" in output and latency <= 1.0:
        return 0.5
    return max(0.0, latency)

def _ping_host_average_ms(
    host: str,
    *,
    attempts: int = RESOURCE_PING_ATTEMPTS,
    timeout: float = RESOURCE_PING_TIMEOUT_SECONDS,
) -> float | None:
    attempts = max(1, int(attempts))
    timeout = max(0.1, float(timeout))
    samples = [_ping_once_ms(host, timeout=timeout) for _ in range(attempts)]
    if all(sample is None for sample in samples):
        return None
    timeout_penalty_ms = timeout * 1000.0
    normalized = [timeout_penalty_ms if sample is None else sample for sample in samples]
    return sum(normalized) / attempts

def _benchmark_resource_sources() -> tuple[str, ...]:
    global _RESOURCE_SOURCE_ORDER
    if _RESOURCE_SOURCE_ORDER is not None:
        return _RESOURCE_SOURCE_ORDER

    hosts = tuple(RESOURCE_SOURCE_HOSTS)
    print("\n  正在测速资源下载源（各 3 次，单次超时 5 秒）...")
    with ThreadPoolExecutor(max_workers=len(hosts), thread_name_prefix="resource-ping") as executor:
        futures = {host: executor.submit(_ping_host_average_ms, host) for host in hosts}
        scores = {host: futures[host].result() for host in hosts}

    for host in hosts:
        label = RESOURCE_SOURCE_HOSTS[host]
        latency = scores[host]
        if latency is None:
            print(f"    {label:<6} unreachable")
        else:
            print(f"    {label:<6} {latency:>7.1f} ms average")

    _RESOURCE_SOURCE_ORDER = tuple(
        sorted(
            hosts,
            key=lambda host: (
                scores[host] is None,
                float("inf") if scores[host] is None else scores[host],
                hosts.index(host),
            ),
        )
    )
    selected_host = _RESOURCE_SOURCE_ORDER[0]
    selected_latency = scores[selected_host]
    if selected_latency is None:
        _print_warn("  Gitee 与 GitHub 均不可达，将按清单顺序尝试下载")
        _RESOURCE_SOURCE_ORDER = hosts
    else:
        print(f"  资源下载优先源: {RESOURCE_SOURCE_HOSTS[selected_host]}")
    return _RESOURCE_SOURCE_ORDER

def load_resource_links(path: Path = RESOURCE_LINKS_FILE) -> dict[str, tuple[str, ...]]:
    """读取资源清单，兼容完整 URL 以及“基础 URL + 文件名”格式。"""
    if not path.exists():
        return {}

    links: dict[str, list[str]] = {}
    base_urls: list[str] = []
    resource_names: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return {}

    for raw_line in lines:
        value = raw_line.strip()
        if not value or value.startswith("#"):
            continue
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme in {"http", "https"}:
            resource_name = urllib.parse.unquote(Path(parsed.path).name)
            if not resource_name or parsed.path.endswith("/"):
                base_urls.append(value.rstrip("/") + "/")
            else:
                links.setdefault(resource_name, []).append(value)
            continue
        if "/" in value or "\\" in value:
            continue
        resource_names.append(value)

    for resource_name in resource_names:
        encoded_name = urllib.parse.quote(resource_name)
        for base_url in base_urls:
            links.setdefault(resource_name, []).append(urllib.parse.urljoin(base_url, encoded_name))
    return {name: tuple(urls) for name, urls in links.items()}

def _order_resource_urls(urls: tuple[str, ...]) -> tuple[str, ...]:
    source_hosts = {
        (urllib.parse.urlsplit(url).hostname or "").lower()
        for url in urls
    }
    if not all(host in source_hosts for host in RESOURCE_SOURCE_HOSTS):
        return urls
    source_order = _benchmark_resource_sources()
    host_rank = {host: index for index, host in enumerate(source_order)}
    return tuple(
        sorted(
            urls,
            key=lambda url: host_rank.get((urllib.parse.urlsplit(url).hostname or "").lower(), len(host_rank)),
        )
    )

def _resource_urls(resource_name: str) -> tuple[str, ...]:
    urls = load_resource_links().get(resource_name, ())
    return _order_resource_urls(urls)

def _order_node_urls(urls: tuple[str, ...]) -> tuple[str, ...]:
    """Ping Node mirrors concurrently and prefer the lowest-latency host."""
    global _NODE_SOURCE_ORDER
    if not urls:
        return urls
    hosts = tuple(dict.fromkeys(
        (urllib.parse.urlsplit(url).hostname or "").lower()
        for url in urls
        if urllib.parse.urlsplit(url).hostname
    ))
    if len(hosts) <= 1:
        return urls
    if _NODE_SOURCE_ORDER is None:
        print("\n  正在并发测速 Node 下载镜像（各 3 次，单次超时 5 秒）...")
        with ThreadPoolExecutor(max_workers=len(hosts), thread_name_prefix="node-ping") as executor:
            futures = {host: executor.submit(_ping_host_average_ms, host) for host in hosts}
            scores = {host: futures[host].result() for host in hosts}
        for host in hosts:
            latency = scores[host]
            label = host
            if latency is None:
                print(f"    {label:<36} unreachable")
            else:
                print(f"    {label:<36} {latency:>7.1f} ms average")
        _NODE_SOURCE_ORDER = tuple(sorted(
            hosts,
            key=lambda host: (
                scores[host] is None,
                float("inf") if scores[host] is None else scores[host],
                hosts.index(host),
            ),
        ))
        selected = _NODE_SOURCE_ORDER[0]
        if scores[selected] is not None:
            print(f"  Node 下载优先源: {selected}")
        else:
            _NODE_SOURCE_ORDER = hosts
            _print_warn("  Node 镜像均不可达，将按清单顺序尝试下载")
    host_rank = {host: index for index, host in enumerate(_NODE_SOURCE_ORDER)}
    return tuple(sorted(
        urls,
        key=lambda url: host_rank.get(
            (urllib.parse.urlsplit(url).hostname or "").lower(),
            len(host_rank),
        ),
    ))

def _unlink_if_exists(path, *, ignore_errors=False):
    if not path.exists():
        return
    try:
        path.unlink()
    except Exception:
        if not ignore_errors:
            raise

def _rmtree_if_exists(path, *, ignore_errors=True):
    if path.exists():
        shutil.rmtree(path, ignore_errors=ignore_errors)

def _cleanup_vosk_temp_artifacts(
    archive_path,
    part_path,
    extract_root,
    *,
    ignore_errors=False,
    preserve_part=False,
):
    _rmtree_if_exists(extract_root, ignore_errors=ignore_errors)
    if not preserve_part:
        _unlink_if_exists(part_path, ignore_errors=ignore_errors)
    _unlink_if_exists(archive_path, ignore_errors=ignore_errors)

def _service_bundle_ready(service_dir: Path, required_files) -> bool:
    if not service_dir.exists() or not service_dir.is_dir():
        return False
    for name in required_files:
        if not (service_dir / name).exists():
            return False
    return True

def _stream_download_with_progress(url, dest_path, *, label, timeout=30, chunk_size=256 * 1024, use_env_proxy=True):
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    existing_size = dest_path.stat().st_size if dest_path.exists() else 0

    headers = {
        "User-Agent": "FlyingSnowVelvetInstaller/1.0",
        "Accept": "application/zip, application/octet-stream, */*",
    }
    if existing_size > 0:
        headers["Range"] = f"bytes={existing_size}-"
    request = urllib.request.Request(
        url,
        headers=headers,
    )
    proxy_text = "env-proxy" if use_env_proxy else "direct"
    print(f"    source: {label} ({proxy_text})")

    start_time = time.perf_counter()
    last_draw = 0.0
    opener = urllib.request.build_opener() if use_env_proxy else urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        content_range = exc.headers.get("Content-Range", "") if exc.headers else ""
        if exc.code == 416 and existing_size > 0 and content_range.endswith(f"/{existing_size}"):
            return
        raise

    with response:
        status = getattr(response, "status", response.getcode())
        append = existing_size > 0 and status == 206
        total_header = response.headers.get("Content-Length")
        response_size = int(total_header) if total_header and total_header.isdigit() else 0
        total = existing_size + response_size if append and response_size else response_size
        current = existing_size if append else 0
        mode = "ab" if append else "wb"
        with open(dest_path, mode) as fp:
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                fp.write(chunk)
                current += len(chunk)
                now = time.perf_counter()
                if now - last_draw >= 0.12:
                    _write_progress_line(_render_transfer_progress("    downloading", current, total, start_time))
                    last_draw = now

            _write_progress_line(
                _render_transfer_progress("    downloading", current, total, start_time),
                finish=True,
            )

    final_size = dest_path.stat().st_size if dest_path.exists() else 0
    if total and final_size != total:
        raise IOError(f"download incomplete: {final_size}/{total} bytes")

def _validate_downloaded_archive(path: Path, resource_name: str) -> None:
    suffix = Path(resource_name).suffix.lower()
    if suffix not in {".zip", ".whl"}:
        return
    with zipfile.ZipFile(path, "r") as archive:
        bad_member = archive.testzip()
    if bad_member is not None:
        raise zipfile.BadZipFile(f"archive contains a damaged member: {bad_member}")

def _download_resource_file(
    resource_name: str,
    dest_path: Path,
    *,
    label: str,
    display_sequence: tuple[int, int] | None = None,
) -> bool:
    """资源缺失时按 resc.net.txt 中的同名链接下载。"""
    if dest_path.exists() and dest_path.stat().st_size > 0:
        try:
            _validate_downloaded_archive(dest_path, resource_name)
            return True
        except (OSError, zipfile.BadZipFile):
            _print_warn(f"  已有资源损坏，将重新下载: {resource_name}")
            _unlink_if_exists(dest_path, ignore_errors=True)

    urls = _resource_urls(resource_name)
    if not urls:
        _print_warn(f"  resc.net.txt 中未找到资源链接: {resource_name}")
        return False

    part_path = dest_path.with_name(dest_path.name + ".part")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    for index, url in enumerate(urls, start=1):
        try:
            if display_sequence is None:
                sequence_text = f"[{index}/{len(urls)}]"
            else:
                sequence_text = f"[{display_sequence[0]}/{display_sequence[1]}]"
            print(f"  下载 {label} {sequence_text}: {resource_name}")
            _stream_download_with_progress(url, part_path, label=label)
            _validate_downloaded_archive(part_path, resource_name)
            part_path.replace(dest_path)
            return True
        except zipfile.BadZipFile as exc:
            _print_warn(f"  下载内容损坏 [{resource_name}]: {exc}")
            _unlink_if_exists(part_path, ignore_errors=True)
        except (urllib.error.URLError, OSError) as exc:
            _print_warn(f"  下载失败 [{resource_name}]: {exc}")

    return False

def _extract_zip_with_progress(zip_path, extract_root):
    _rmtree_if_exists(extract_root)
    extract_root.mkdir(parents=True, exist_ok=True)
    resolved_root = extract_root.resolve()

    with zipfile.ZipFile(zip_path, "r") as zf:
        members = zf.infolist()
        total = sum(max(0, item.file_size) for item in members if not item.is_dir())
        current = 0
        start_time = time.perf_counter()
        last_draw = 0.0

        for item in members:
            member_name = item.filename.replace("\\", "/")
            if not item.flag_bits & 0x800:
                try:
                    member_name = member_name.encode("cp437").decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
            relative_path = Path(*[part for part in member_name.split("/") if part not in {"", "."}])
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise ValueError(f"unsafe zip member path: {item.filename}")
            target_path = (extract_root / relative_path).resolve()
            if target_path != resolved_root and resolved_root not in target_path.parents:
                raise ValueError(f"unsafe zip member path: {item.filename}")

            if item.is_dir():
                target_path.mkdir(parents=True, exist_ok=True)
            else:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(item, "r") as source, open(target_path, "wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                current += max(0, item.file_size)
            now = time.perf_counter()
            if now - last_draw >= 0.12:
                _write_progress_line(_render_transfer_progress("    extracting ", current, total, start_time))
                last_draw = now

        _write_progress_line(
            _render_transfer_progress("    extracting ", current, total, start_time),
            finish=True,
        )

def _seanima_ready() -> bool:
    return SEANIMA_TARGET_DIR.is_dir() and any(SEANIMA_TARGET_DIR.rglob("*.webp"))

def ensure_seanima_assets() -> bool:
    """确保启动/退出动画序列帧存在。"""
    _print_stage(7, "准备启动/退出动画资源...")
    if _seanima_ready():
        print(f"  动画资源已存在: {SEANIMA_TARGET_DIR.relative_to(PROJECT_ROOT)}")
        return True

    if not _download_resource_file(SEANIMA_RESOURCE_NAME, SEANIMA_ARCHIVE, label="启动动画资源"):
        return False

    temp_root = Path(os.environ.get("TEMP", "C:\\Temp")) / "fsv_seanima"
    extract_root = temp_root / "extract"
    _rmtree_if_exists(temp_root, ignore_errors=True)
    try:
        _extract_zip_with_progress(SEANIMA_ARCHIVE, extract_root)
        source_root = extract_root / "SEanima"
        if not source_root.is_dir():
            directories = [path for path in extract_root.iterdir() if path.is_dir()]
            if len(directories) == 1:
                source_root = directories[0]
        if not source_root.is_dir() or not any(source_root.rglob("*.webp")):
            raise FileNotFoundError("动画资源包中未找到 SEanima 序列帧目录")
        _rmtree_if_exists(SEANIMA_TARGET_DIR, ignore_errors=True)
        SEANIMA_TARGET_DIR.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_root), str(SEANIMA_TARGET_DIR))
        print(f"  动画资源已安装: {SEANIMA_TARGET_DIR.relative_to(PROJECT_ROOT)}")
        return True
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        _print_warn(f"  安装动画资源失败: {exc}")
        return False
    finally:
        _rmtree_if_exists(temp_root, ignore_errors=True)
        _unlink_if_exists(SEANIMA_ARCHIVE, ignore_errors=True)

def _resolve_vosk_model_source_dir(extract_root):
    if all((extract_root / marker).exists() for marker in ("am", "conf")):
        return extract_root

    children = [item for item in extract_root.iterdir() if item.is_dir()]
    for child in children:
        if all((child / marker).exists() for marker in ("am", "conf")):
            return child

    if len(children) == 1:
        return children[0]

    raise FileNotFoundError("extracted model folder not found")

def _microphone_runtime_ready(python_exe):
    return (
        _pkg_installed(python_exe, "sounddevice", import_checks=("sounddevice",))
        and _pkg_installed(python_exe, "vosk", import_checks=("vosk",))
        and _pkg_installed(python_exe, "webrtcvad-wheels", import_checks=("webrtcvad",))
    )

def _ensure_single_vosk_model(spec: dict) -> bool:
    label = spec.get("label") or spec["name"]
    resource_name = spec["resource_name"]
    target_dir = VOSK_MODELS_DIR / spec["name"]
    rel_target = target_dir.relative_to(PROJECT_ROOT)

    if all((target_dir / marker).exists() for marker in VOSK_MODEL_MARKERS):
        print(f"  model already installed ({label}): {rel_target}")
        return True

    archive_path = VOSK_MODELS_DIR / f"{spec['name']}.zip"
    part_path = VOSK_MODELS_DIR / f"{spec['name']}.zip.part"
    extract_root = VOSK_MODELS_DIR / f"_{spec['name']}_extract"
    VOSK_MODELS_DIR.mkdir(parents=True, exist_ok=True)

    for leftover in VOSK_MODELS_DIR.glob("BIT*.tmp"):
        _unlink_if_exists(leftover, ignore_errors=True)

    try:
        _cleanup_vosk_temp_artifacts(
            archive_path,
            part_path,
            extract_root,
            preserve_part=True,
        )
        if not _download_resource_file(resource_name, archive_path, label=f"Vosk {label} 模型"):
            return False
        _extract_zip_with_progress(archive_path, extract_root)
        source_dir = _resolve_vosk_model_source_dir(extract_root)

        _rmtree_if_exists(target_dir)
        shutil.move(str(source_dir), str(target_dir))
        print(f"    model installed: {rel_target}")
        return True
    except (OSError, ValueError, zipfile.BadZipFile, FileNotFoundError) as exc:
        print(f"    failed: {exc}")
        print(f"  warning: {label} model auto download failed")
        print(f"  extract target: {rel_target}")
        return False
    finally:
        _cleanup_vosk_temp_artifacts(
            archive_path,
            part_path,
            extract_root,
            ignore_errors=True,
            preserve_part=True,
        )

def ensure_vosk_models():
    _print_stage(6, "准备 Vosk 语音模型...")
    all_ok = True
    for spec in VOSK_MODEL_SPECS:
        if not _ensure_single_vosk_model(spec):
            all_ok = False
    return all_ok


__all__ = (
    '_RESOURCE_SOURCE_ORDER',
    '_NODE_SOURCE_ORDER',
    '_ping_once_ms',
    '_ping_host_average_ms',
    '_benchmark_resource_sources',
    'load_resource_links',
    '_order_resource_urls',
    '_resource_urls',
    '_order_node_urls',
    '_unlink_if_exists',
    '_rmtree_if_exists',
    '_cleanup_vosk_temp_artifacts',
    '_service_bundle_ready',
    '_stream_download_with_progress',
    '_validate_downloaded_archive',
    '_download_resource_file',
    '_extract_zip_with_progress',
    '_seanima_ready',
    'ensure_seanima_assets',
    '_resolve_vosk_model_source_dir',
    '_microphone_runtime_ready',
    '_ensure_single_vosk_model',
    'ensure_vosk_models',
)
