#!/usr/bin/env python3
"""
Create a trimmed release archive that excludes runtime artifacts
and produces a manifest for verification.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

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

ALLOWED_TOP_LEVEL_DIRS = {
    "config",
    "doc",
    "gamepack",
    "install_deps",
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
    "node_modules",
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
    Path("resc") / "node-24.13.0-win-x64",
    Path("resc") / "GIF" / "SEanima",
    Path("resc") / "user",
    Path("resc") / "gsvmove_update",
    Path("services") / "bundles",
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
    Path("services") / "storage_state.json",
    Path("ASYNC_COMPUTE_PLAN.txt"),
    Path("CONTRIBUTING.md"),
    Path("RELEASING.md"),
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

def _is_under(path: Path, prefix: Path) -> bool:
    """Return True if `path` (relative) starts with `prefix`."""
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


def _should_exclude(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if not _is_allowed_top_level(rel):
        return True
    if rel in EXCLUDE_EXACT_PATHS:
        return True
    if any(part.startswith(".") for part in rel.parts):
        return True
    # directory parts
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
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for entry in file_entries:
            payload = inline_payloads.get(entry.relative)
            if payload is not None:
                zf.writestr(entry.relative.as_posix(), payload)
                continue
            src = ROOT / entry.relative
            zf.write(src, arcname=entry.relative.as_posix())
        for entry in placeholder_entries:
            payload = placeholder_payloads.get(entry.relative, "Generated at runtime.\n")
            zf.writestr(entry.relative.as_posix(), payload)


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Package Flying Snow Velvet release bundle.")
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
    entries = sorted(
        iter_files(ROOT, _should_exclude, inline_payloads, generated_payloads),
        key=lambda e: e.relative.as_posix(),
    )
    placeholder_entries, placeholder_payloads = build_placeholder_entries(args.version, PLACEHOLDER_DIRS)
    all_entries = entries + placeholder_entries
    total_size = sum(entry.size for entry in entries)
    print(
        f"[package] files: {len(entries)} (+{len(placeholder_entries)} placeholders) | "
        f"generated bundles: {len(generated_payloads)} | size: {format_size(total_size)}"
    )
    for entry in all_entries:
        hint = " [placeholder]" if entry in placeholder_entries else ""
        print(f"  {entry.relative.as_posix()} ({format_size(entry.size)}){hint}")
    if args.dry_run:
        print("[package] dry-run complete; no artifacts produced.")
        return 0

    args.output.mkdir(parents=True, exist_ok=True)
    zip_path = args.output / f"FlyingSnowVelvet-{args.version}.zip"
    manifest_path = args.output / f"FlyingSnowVelvet-{args.version}-manifest.json"

    _write_archive(zip_path, entries, placeholder_entries, placeholder_payloads, archive_payloads)
    write_manifest(manifest_path, all_entries)

    print(f"[package] wrote {zip_path.relative_to(ROOT)} ({format_size(zip_path.stat().st_size)})")
    print(f"[package] wrote {manifest_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
