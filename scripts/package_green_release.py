#!/usr/bin/env python3
"""
Create a green distribution archive that keeps generated runtime state out,
but bundles the external resource archives expected by install_deps.py.
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release_common import (
    FileEntry,
    build_generated_payloads,
    build_inline_payloads,
    build_placeholder_entries,
    configure_console_output,
    format_size,
    iter_files,
    read_app_version,
    write_manifest,
)

DEFAULT_VERSION = read_app_version(ROOT)
DIST_DIR = ROOT / "dist"
RESOURCE_LINKS_FILE = ROOT / "resc.net.txt"

ALLOWED_TOP_LEVEL_DIRS = {
    "config",
    "doc",
    "gamepack",
    "lib",
    "pyncm",
    "resc",
    "services",
}

ALLOWED_TOP_LEVEL_FILES = {
    "CHANGELOG.md",
    "LICENSE-ASSETS",
    "LICENSE-CODE",
    "README.md",
    "install_deps.py",
    "requirements.txt",
    "requirements-service.txt",
    "resc.net.txt",
    "启动程序.bat",
    "安装依赖.bat",
    "调试模式.bat",
}

EXCLUDE_PART_NAMES = {
    ".claude",
    ".git",
    ".github",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "dist",
    "logs",
    "tmp",
    ".vscode",
}

EXCLUDE_PATH_PREFIXES = {
    Path("config") / ".shared_pending",
    Path("lib") / "script" / "gemes" / "packages" / "official",
    Path("resc") / "models",
    Path("resc") / "playwright",
    Path("resc") / "GIF" / "SEanima",
    Path("resc") / "user",
    Path("resc") / "gsvmove_update",
    Path("tests"),
    Path("scripts"),
    Path(".oprate"),
    Path("用户反馈"),
}

EXCLUDE_EXACT_PATHS = {
    Path("config") / "user_scale.json",
    Path("config") / "music" / "volume.json",
    Path("resc") / "python-3.11.6-amd64.exe",
    Path("resc") / "GIF" / "SEanima.zip",
    Path("resc") / "chrome-runtime.zip",
    Path("resc") / "chrome-runtime.z01",
    Path("resc") / "chrome-runtime.z02",
    Path("services") / "storage_state.json",
    Path("ASYNC_COMPUTE_PLAN.txt"),
    Path("CONTRIBUTING.md"),
    Path("RELEASING.md"),
    Path("services") / "yuanbao-free-api" / "Dockerfile",
    Path("services") / "yuanbao-free-api" / "test.py",
    Path("pyncm") / "__main__.py",
}

EXCLUDE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".log",
    ".tmp",
    ".part",
    ".bak",
}

ROOT_ARCHIVE_SUFFIXES = {
    ".zip",
    ".7z",
    ".tar",
    ".gz",
}

EXCLUDE_FILE_NAMES = {
    "py.ini",
}

PLACEHOLDER_DIRS = (
    Path("logs"),
    Path("resc") / "models",
    Path("resc") / "user",
)

GREEN_BUNDLED_RESOURCE_PATHS = {
    Path("resc") / "models" / "vosk-model-small-cn-0.22.zip",
    Path("resc") / "models" / "vosk-model-small-en-us-0.15.zip",
    Path("resc") / "GIF" / "SEanima.zip",
    Path("resc") / "chrome-runtime.z01",
    Path("resc") / "chrome-runtime.z02",
    Path("resc") / "chrome-runtime.zip",
}

GREEN_BUNDLED_RESOURCE_LABELS = {
    Path("resc") / "models" / "vosk-model-small-cn-0.22.zip": "Vosk Chinese 模型",
    Path("resc") / "models" / "vosk-model-small-en-us-0.15.zip": "Vosk English 模型",
    Path("resc") / "GIF" / "SEanima.zip": "启动动画资源",
    Path("resc") / "chrome-runtime.z01": "浏览器运行时分卷",
    Path("resc") / "chrome-runtime.z02": "浏览器运行时分卷",
    Path("resc") / "chrome-runtime.zip": "浏览器运行时分卷",
}


def _is_under(path: Path, prefix: Path) -> bool:
    prefix_parts = prefix.parts
    parts = path.parts
    if len(parts) < len(prefix_parts):
        return False
    return parts[: len(prefix_parts)] == prefix_parts


def _is_allowed_top_level(rel: Path) -> bool:
    if not rel.parts:
        return False
    top_level = rel.parts[0]
    if len(rel.parts) == 1:
        return top_level in ALLOWED_TOP_LEVEL_FILES
    return top_level in ALLOWED_TOP_LEVEL_DIRS


def _bundled_resource_status() -> Tuple[List[Path], List[Path]]:
    present: List[Path] = []
    missing: List[Path] = []
    for relative in sorted(GREEN_BUNDLED_RESOURCE_PATHS, key=lambda p: p.as_posix()):
        if (ROOT / relative).exists():
            present.append(relative)
        else:
            missing.append(relative)
    return present, missing


def _load_resource_links(path: Path = RESOURCE_LINKS_FILE) -> Dict[str, Tuple[str, ...]]:
    if not path.exists():
        return {}

    links: Dict[str, List[str]] = {}
    base_urls: List[str] = []
    resource_names: List[str] = []
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


def _format_bytes(num_bytes: float) -> str:
    size = float(max(0.0, num_bytes))
    units = ("B", "KB", "MB", "GB")
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)}{unit}"
            return f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}GB"


def _render_transfer_progress(prefix: str, current: int, total: int, start_time: float) -> str:
    elapsed = max(time.perf_counter() - start_time, 1e-6)
    speed = current / elapsed
    speed_text = f"{_format_bytes(speed)}/s"
    current_text = _format_bytes(current)
    if total:
        percent = min(100.0, (current * 100.0) / total)
        total_text = _format_bytes(total)
        bar_width = 24
        filled = max(0, min(bar_width, int(percent / 100.0 * bar_width)))
        bar = "#" * filled + "-" * (bar_width - filled)
        return f"{prefix} [{bar}] {percent:6.2f}% {current_text}/{total_text} {speed_text}"
    return f"{prefix} {current_text} {speed_text}"


def _render_archive_progress(prefix: str, current: int, total: int, written_bytes: int, total_bytes: int) -> str:
    count_text = f"{current}/{total}"
    if total_bytes > 0:
        percent = min(100.0, (written_bytes * 100.0) / total_bytes)
        return f"{prefix} [{count_text}] {percent:6.2f}% {_format_bytes(written_bytes)}/{_format_bytes(total_bytes)}"
    return f"{prefix} [{count_text}]"


def _write_progress_line(text: str, *, finish: bool = False) -> None:
    suffix = "\n" if finish else ""
    sys.stdout.write("\r" + text.ljust(120) + suffix)
    sys.stdout.flush()


def _unlink_if_exists(path: Path, *, ignore_errors: bool = False) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        if not ignore_errors:
            raise


def _stream_download_with_progress(
    url: str,
    dest_path: Path,
    *,
    label: str,
    timeout: int = 30,
    chunk_size: int = 256 * 1024,
) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    _unlink_if_exists(dest_path, ignore_errors=True)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "FlyingSnowVelvetPackager/1.0",
            "Accept": "application/zip, application/octet-stream, */*",
        },
    )

    start_time = time.perf_counter()
    last_draw = 0.0
    with urllib.request.urlopen(request, timeout=timeout) as response, open(dest_path, "wb") as fp:
        total_header = response.headers.get("Content-Length")
        total = int(total_header) if total_header and total_header.isdigit() else 0
        current = 0
        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                break
            fp.write(chunk)
            current += len(chunk)
            now = time.perf_counter()
            if now - last_draw >= 0.12:
                _write_progress_line(_render_transfer_progress(f"    downloading {label}", current, total, start_time))
                last_draw = now

        _write_progress_line(
            _render_transfer_progress(f"    downloading {label}", current, total, start_time),
            finish=True,
        )

    final_size = dest_path.stat().st_size if dest_path.exists() else 0
    if total and final_size != total:
        raise IOError(f"download incomplete: {final_size}/{total} bytes")


def _download_missing_resource(relative: Path, sequence: Tuple[int, int], resource_links: Dict[str, Tuple[str, ...]]) -> bool:
    resource_name = relative.name
    label = GREEN_BUNDLED_RESOURCE_LABELS.get(relative, resource_name)
    urls = resource_links.get(resource_name, ())
    if not urls:
        print(f"  [download {sequence[0]}/{sequence[1]}] missing link in resc.net.txt: {resource_name}")
        return False

    dest_path = ROOT / relative
    part_path = dest_path.with_name(dest_path.name + ".part")
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    for index, url in enumerate(urls, start=1):
        try:
            _unlink_if_exists(part_path, ignore_errors=True)
            print(f"  [download {sequence[0]}/{sequence[1]}] {label}: {resource_name} (source {index}/{len(urls)})")
            _stream_download_with_progress(url, part_path, label=resource_name)
            part_path.replace(dest_path)
            return True
        except (urllib.error.URLError, OSError) as exc:
            print(f"    failed: {exc}")
        finally:
            _unlink_if_exists(part_path, ignore_errors=True)
    return False


def _ensure_bundled_resources(*, allow_download: bool) -> Tuple[List[Path], List[Path]]:
    present, missing = _bundled_resource_status()
    if not missing or not allow_download:
        return present, missing

    print(f"[green-package] missing resource archives detected: {len(missing)}")
    resource_links = _load_resource_links()
    for index, relative in enumerate(missing, start=1):
        _download_missing_resource(relative, (index, len(missing)), resource_links)
    return _bundled_resource_status()


def _should_exclude(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if rel in GREEN_BUNDLED_RESOURCE_PATHS:
        return False
    if not _is_allowed_top_level(rel):
        return True
    if rel in EXCLUDE_EXACT_PATHS:
        return True
    if any(part.startswith(".") for part in rel.parts):
        return True
    for part in rel.parts:
        if part in EXCLUDE_PART_NAMES:
            return True
    for prefix in EXCLUDE_PATH_PREFIXES:
        if _is_under(rel, prefix):
            return True
    if rel.name in EXCLUDE_FILE_NAMES:
        return True
    if rel.parent == Path('.') and path.suffix.lower() in ROOT_ARCHIVE_SUFFIXES:
        return True
    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return True
    return False


def _write_archive(
    zip_path: Path,
    file_entries: List[FileEntry],
    placeholder_entries: List[FileEntry],
    placeholder_payloads: Dict[Path, str],
    inline_payloads: Dict[Path, bytes],
) -> None:
    if zip_path.exists():
        zip_path.unlink()
    total_entries = len(file_entries) + len(placeholder_entries)
    total_bytes = sum(entry.size for entry in file_entries) + sum(entry.size for entry in placeholder_entries)
    written_bytes = 0
    written_entries = 0
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for entry in file_entries:
            payload = inline_payloads.get(entry.relative)
            if payload is not None:
                zf.writestr(entry.relative.as_posix(), payload)
            else:
                src = ROOT / entry.relative
                zf.write(src, arcname=entry.relative.as_posix())
            written_entries += 1
            written_bytes += entry.size
            _write_progress_line(
                _render_archive_progress("[green-package] archiving", written_entries, total_entries, written_bytes, total_bytes)
            )
        for entry in placeholder_entries:
            payload = placeholder_payloads.get(entry.relative, "Generated at runtime.\n")
            zf.writestr(entry.relative.as_posix(), payload)
            written_entries += 1
            written_bytes += entry.size
            _write_progress_line(
                _render_archive_progress("[green-package] archiving", written_entries, total_entries, written_bytes, total_bytes)
            )
    _write_progress_line(
        _render_archive_progress("[green-package] archiving", written_entries, total_entries, written_bytes, total_bytes),
        finish=True,
    )


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Package Flying Snow Velvet green release bundle.")
    parser.add_argument("--version", default=DEFAULT_VERSION, help="Version tag (default: %(default)s)")
    parser.add_argument("--output", type=Path, default=DIST_DIR, help="Output directory (default: dist/)")
    parser.add_argument("--dry-run", action="store_true", help="List files without creating archives")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    configure_console_output()
    args = parse_args(argv or sys.argv[1:])
    inline_payloads = build_inline_payloads(ROOT)
    generated_payloads = build_generated_payloads(ROOT)
    archive_payloads = dict(inline_payloads)
    archive_payloads.update(generated_payloads)
    bundled_present, bundled_missing = _ensure_bundled_resources(allow_download=not args.dry_run)
    entries = sorted(
        iter_files(ROOT, _should_exclude, inline_payloads, generated_payloads),
        key=lambda e: e.relative.as_posix(),
    )
    placeholder_entries, placeholder_payloads = build_placeholder_entries(args.version, PLACEHOLDER_DIRS)
    all_entries = entries + placeholder_entries
    total_size = sum(entry.size for entry in entries)
    print(
        f"[green-package] files: {len(entries)} (+{len(placeholder_entries)} placeholders) | "
        f"generated bundles: {len(generated_payloads)} | size: {format_size(total_size)}"
    )
    print(f"[green-package] bundled resource archives: {len(bundled_present)} present, {len(bundled_missing)} missing")
    for relative in bundled_present:
        print(f"  [bundled] {relative.as_posix()}")
    for relative in bundled_missing:
        print(f"  [missing] {relative.as_posix()}")
    if bundled_missing and not args.dry_run:
        print("[green-package] missing resource downloads remain unresolved; aborting package.")
        return 1
    for entry in all_entries:
        hint = " [placeholder]" if entry in placeholder_entries else ""
        print(f"  {entry.relative.as_posix()} ({format_size(entry.size)}){hint}")
    if args.dry_run:
        print("[green-package] dry-run complete; no artifacts produced.")
        return 0

    args.output.mkdir(parents=True, exist_ok=True)
    zip_path = args.output / f"FlyingSnowVelvet-{args.version}-green.zip"
    manifest_path = args.output / f"FlyingSnowVelvet-{args.version}-green-manifest.json"

    _write_archive(zip_path, entries, placeholder_entries, placeholder_payloads, archive_payloads)
    write_manifest(manifest_path, all_entries)

    print(f"[green-package] wrote {zip_path.relative_to(ROOT)} ({format_size(zip_path.stat().st_size)})")
    print(f"[green-package] wrote {manifest_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
