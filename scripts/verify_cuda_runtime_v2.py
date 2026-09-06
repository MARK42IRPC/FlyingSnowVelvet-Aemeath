#!/usr/bin/env python3
"""Verify a self-contained NVIDIA voice runtime v2 archive offline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.core.cuda_runtime_bundle_v2 import (  # noqa: E402
    CudaRuntimeV2Error,
    verify_archive_offline,
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True, help="local v2 ZIP")
    parser.add_argument("--expected-sha256", help="optional release SHA-256")
    parser.add_argument(
        "--driver-version",
        help="optional locally observed NVIDIA display driver version",
    )
    parser.add_argument("--report", type=Path, help="optional JSON report path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        result = verify_archive_offline(
            args.archive,
            expected_archive_sha256=args.expected_sha256,
            driver_version=args.driver_version,
        )
    except (CudaRuntimeV2Error, OSError) as exc:
        print(f"[cuda-runtime-v2-verify] error: {exc}", file=sys.stderr)
        return 2
    payload = {
        "archive": str(result.archive),
        "archive_bytes": result.archive_bytes,
        "archive_sha256": result.archive_sha256,
        "bundle_id": result.bundle_id,
        "file_count": result.file_count,
        "payload_bytes": result.payload_bytes,
        "driver_requirement": result.driver_requirement,
        "detected_driver": result.detected_driver,
        "worker_command": list(result.worker_command),
        "offline": True,
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    print(rendered, end="")
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
