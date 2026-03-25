#!/usr/bin/env python3
"""Fetch or repack a local Playwright Chromium runtime into resc/playwright."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "resc" / "playwright"


def _run(cmd: list[str], timeout: int = 1200) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=timeout)


def _query_browser_info(python_exe: str) -> dict:
    code = (
        "import json; from pathlib import Path; "
        "from playwright.sync_api import sync_playwright; "
        "import playwright; "
        "p = sync_playwright().start(); "
        "exe = Path(p.chromium.executable_path); "
        "info = {'playwright_version': getattr(playwright, '__version__', ''), 'executable_path': str(exe), 'browser_dir': str(exe.parent.parent)}; "
        "p.stop(); print(json.dumps(info, ensure_ascii=True))"
    )
    result = _run([python_exe, "-c", code], timeout=120)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "query failed")
    return json.loads(result.stdout.strip())


def _ensure_browser_downloaded(python_exe: str) -> None:
    result = _run([python_exe, "-m", "playwright", "install", "chromium"], timeout=1800)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "playwright install chromium failed")


def _zip_browser_dir(browser_dir: Path, output_path: Path) -> None:
    if output_path.exists():
        output_path.unlink()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in browser_dir.rglob("*"):
            if not path.is_file():
                continue
            arcname = Path("ms-playwright") / browser_dir.name / path.relative_to(browser_dir)
            zf.write(path, arcname=arcname.as_posix())


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Playwright Chromium runtime resource for green distribution.")
    parser.add_argument("--python", default=sys.executable, help="Python executable used to query/install playwright")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory for resource zip")
    parser.add_argument("--force-download", action="store_true", help="Run playwright install chromium before packaging")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.force_download:
        print("[fetch] downloading Playwright Chromium runtime...")
        _ensure_browser_downloaded(args.python)

    info = _query_browser_info(args.python)
    browser_dir = Path(info["browser_dir"])
    executable_path = Path(info["executable_path"])
    if not executable_path.exists() or not browser_dir.exists():
        print("[fetch] browser runtime missing locally, trying online install...")
        _ensure_browser_downloaded(args.python)
        info = _query_browser_info(args.python)
        browser_dir = Path(info["browser_dir"])
        executable_path = Path(info["executable_path"])

    if not executable_path.exists() or not browser_dir.exists():
        raise SystemExit("[fetch] failed to resolve local Chromium runtime")

    output_name = f"playwright-chromium-{browser_dir.name}.zip"
    output_path = args.output_dir / output_name
    _zip_browser_dir(browser_dir, output_path)

    metadata = {
        "playwright_version": info.get("playwright_version", ""),
        "browser_dir": browser_dir.name,
        "executable_path": str(executable_path),
        "archive": str(output_path.relative_to(ROOT)),
    }
    metadata_path = args.output_dir / "playwright-chromium-resource.json"
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[fetch] browser dir: {browser_dir}")
    print(f"[fetch] archive: {output_path.relative_to(ROOT)}")
    print(f"[fetch] metadata: {metadata_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
