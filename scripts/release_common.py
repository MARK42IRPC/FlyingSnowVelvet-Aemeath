"""Shared, deterministic payload generation for release packagers."""

from __future__ import annotations

import ast
from fnmatch import fnmatch
import io
import json
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


@dataclass(frozen=True)
class FileEntry:
    relative: Path
    size: int


def configure_console_output() -> None:
    """Keep release logs writable when paths exceed the active code page."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding='utf-8', errors='backslashreplace')
        except (OSError, ValueError):
            continue


def read_app_version(root: Path) -> str:
    """Read APP_VERSION without importing config package side effects."""
    version_path = root / 'config' / 'version_info.py'
    tree = ast.parse(version_path.read_text(encoding='utf-8-sig'), filename=str(version_path))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == 'APP_VERSION' for target in targets):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value.strip():
            return value.value.strip()
    raise ValueError(f'APP_VERSION is missing or invalid: {version_path}')


def _replace_assignment(text: str, name: str, value_literal: str) -> str:
    return re.sub(
        rf"(?m)^(\s*{re.escape(name)}\s*=\s*).*$",
        rf"\g<1>{value_literal}",
        text,
    )


def _replace_named_dict_item(text: str, dict_name: str, key: str, value_literal: str) -> str:
    pattern = (
        rf"(?ms)(^\s*{re.escape(dict_name)}\s*=\s*\{{.*?^[ \t]*['\"]{re.escape(key)}['\"]\s*:\s*)"
        rf"([^\r\n#]*?)(\s*,\s*(?:#.*)?$|\s*(?:#.*)?$)"
    )
    return re.sub(pattern, rf"\g<1>{value_literal}\g<3>", text)


def build_inline_payloads(root: Path) -> dict[Path, bytes]:
    relative = Path('config') / 'ollama_config.py'
    source = root / relative
    if not source.exists():
        return {}
    text = source.read_text(encoding='utf-8')
    text = _replace_assignment(text, 'API_KEY', "''")
    for key in ('hy_user', 'x_uskey', 'chat_id'):
        text = _replace_named_dict_item(text, 'YUANBAO_FREE_API', key, "''")
    ast.parse(text, filename=str(relative))
    return {relative: text.encode('utf-8')}


def _iter_payload_files(
    source_root: Path,
    *,
    excluded_names: frozenset[str] = frozenset(),
    excluded_patterns: tuple[str, ...] = (),
) -> Iterator[Path]:
    for path in source_root.rglob('*'):
        if not path.is_file():
            continue
        rel = path.relative_to(source_root)
        if '__pycache__' in rel.parts or path.suffix.lower() in {'.pyc', '.pyo'}:
            continue
        lower_name = path.name.lower()
        if lower_name in excluded_names or any(fnmatch(lower_name, pattern) for pattern in excluded_patterns):
            continue
        yield path


def _zip_tree(
    source_root: Path,
    *,
    excluded_names: frozenset[str] = frozenset(),
    excluded_patterns: tuple[str, ...] = (),
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        files = _iter_payload_files(
            source_root,
            excluded_names=excluded_names,
            excluded_patterns=excluded_patterns,
        )
        for path in sorted(files, key=lambda item: item.relative_to(source_root).as_posix()):
            archive.write(path, arcname=path.relative_to(source_root).as_posix())
    return buffer.getvalue()


def build_generated_payloads(root: Path) -> dict[Path, bytes]:
    payloads: dict[Path, bytes] = {}
    official_root = root / 'lib' / 'script' / 'gemes' / 'packages' / 'official'
    if official_root.exists():
        for package_dir in sorted((path for path in official_root.iterdir() if path.is_dir()), key=lambda path: path.name.lower()):
            payloads[Path('gamepack') / 'official' / f'{package_dir.name}.zip'] = _zip_tree(package_dir)

    return payloads


def iter_files(
    root: Path,
    should_exclude,
    inline_payloads: dict[Path, bytes],
    generated_payloads: dict[Path, bytes],
) -> Iterator[FileEntry]:
    for path in root.rglob('*'):
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        if should_exclude(path):
            continue
        relative = path.relative_to(root)
        if relative in generated_payloads:
            continue
        payload = inline_payloads.get(relative)
        yield FileEntry(relative, len(payload) if payload is not None else path.stat().st_size)
    for relative, payload in sorted(generated_payloads.items(), key=lambda item: item[0].as_posix()):
        yield FileEntry(relative, len(payload))


def build_placeholder_entries(version: str, directories: Iterable[Path]):
    entries: list[FileEntry] = []
    payloads: dict[Path, str] = {}
    for directory in directories:
        relative = directory / '.keep'
        text = f'{directory.as_posix()} is generated at runtime.\nVersion: {version}\n'
        entries.append(FileEntry(relative, len(text.encode('utf-8'))))
        payloads[relative] = text
    return entries, payloads


def write_manifest(path: Path, files: Iterable[FileEntry]) -> None:
    path.write_text(
        json.dumps(
            [{'path': item.relative.as_posix(), 'size': item.size} for item in files],
            indent=2,
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )


def format_size(num_bytes: int) -> str:
    value = float(num_bytes)
    units = ('B', 'KB', 'MB', 'GB')
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f'{value:.2f}{unit}'
        value /= 1024.0
    return f'{value:.2f}GB'
