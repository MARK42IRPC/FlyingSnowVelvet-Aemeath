#!/usr/bin/env python3
"""
Create a green distribution archive that keeps bundled runtime assets
such as Vosk models and Chromium resources for direct file sharing.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VERSION = "LTS1.0.5pre1"
DIST_DIR = ROOT / "dist"

EXCLUDE_PART_NAMES = {
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
    Path("resc") / "playwright" / "browsers",
    Path("resc") / "user",
    Path("resc") / "gsvmove_update",
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
    Path("resc") / "user",
)


@dataclass
class FileEntry:
    relative: Path
    size: int


def _is_under(path: Path, prefix: Path) -> bool:
    prefix_parts = prefix.parts
    parts = path.parts
    if len(parts) < len(prefix_parts):
        return False
    return parts[: len(prefix_parts)] == prefix_parts


def _should_exclude(path: Path) -> bool:
    rel = path.relative_to(ROOT)
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


def _iter_files() -> Iterator[FileEntry]:
    for path in ROOT.rglob("*"):
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        if _should_exclude(path):
            continue
        rel = path.relative_to(ROOT)
        size = path.stat().st_size
        yield FileEntry(relative=rel, size=size)


def _write_manifest(manifest_path: Path, files: Iterable[FileEntry]) -> None:
    data = [
        {
            "path": entry.relative.as_posix(),
            "size": entry.size,
        }
        for entry in files
    ]
    manifest_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_archive(
    zip_path: Path,
    file_entries: List[FileEntry],
    placeholder_entries: List[FileEntry],
    placeholder_payloads: Dict[Path, str],
) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for entry in file_entries:
            src = ROOT / entry.relative
            zf.write(src, arcname=entry.relative.as_posix())
        for entry in placeholder_entries:
            payload = placeholder_payloads.get(entry.relative, "Generated at runtime.\n")
            zf.writestr(entry.relative.as_posix(), payload)


def _build_placeholder_entries(version: str) -> Tuple[List[FileEntry], Dict[Path, str]]:
    entries: List[FileEntry] = []
    payloads: Dict[Path, str] = {}
    for placeholder in PLACEHOLDER_DIRS:
        arcname = placeholder / ".keep"
        text = f"{placeholder.as_posix()} is generated at runtime.\nVersion: {version}\n"
        entries.append(FileEntry(relative=arcname, size=len(text.encode("utf-8"))))
        payloads[arcname] = text
    return entries, payloads


def _format_size(num_bytes: int) -> str:
    units = ("B", "KB", "MB", "GB")
    value = float(num_bytes)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.2f}{unit}"
        value /= 1024.0
    return f"{value:.2f}GB"


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Package Flying Snow Velvet green release bundle.")
    parser.add_argument("--version", default=DEFAULT_VERSION, help="Version tag (default: %(default)s)")
    parser.add_argument("--output", type=Path, default=DIST_DIR, help="Output directory (default: dist/)")
    parser.add_argument("--dry-run", action="store_true", help="List files without creating archives")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    entries = sorted(_iter_files(), key=lambda e: e.relative.as_posix())
    placeholder_entries, placeholder_payloads = _build_placeholder_entries(args.version)
    all_entries = entries + placeholder_entries
    total_size = sum(entry.size for entry in entries)
    print(f"[green-package] files: {len(entries)} (+{len(placeholder_entries)} placeholders) | size: {_format_size(total_size)}")
    for entry in all_entries:
        hint = " [placeholder]" if entry in placeholder_entries else ""
        print(f"  {entry.relative.as_posix()} ({_format_size(entry.size)}){hint}")
    if args.dry_run:
        print("[green-package] dry-run complete; no artifacts produced.")
        return 0

    args.output.mkdir(parents=True, exist_ok=True)
    zip_path = args.output / f"FlyingSnowVelvet-{args.version}-green.zip"
    manifest_path = args.output / f"FlyingSnowVelvet-{args.version}-green-manifest.json"

    _write_archive(zip_path, entries, placeholder_entries, placeholder_payloads)
    _write_manifest(manifest_path, all_entries)

    print(f"[green-package] wrote {zip_path.relative_to(ROOT)} ({_format_size(zip_path.stat().st_size)})")
    print(f"[green-package] wrote {manifest_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
