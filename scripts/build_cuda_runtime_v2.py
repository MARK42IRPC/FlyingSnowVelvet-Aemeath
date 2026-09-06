#!/usr/bin/env python3
"""Build the self-contained NVIDIA voice runtime v2 archive.

The source directory is an already assembled payload containing ``python/``,
``runtime/`` and ``worker/``.  No package manager, compiler or network client
is invoked by this command.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.core.cuda_runtime_bundle_v2 import (  # noqa: E402
    CUDA_RUNTIME_V2_MIN_DRIVER_VERSION,
    CudaRuntimeV2Error,
    build_bundle,
    collect_payload_files,
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root",
        type=Path,
        required=True,
        help="assembled payload root (python/, runtime/, worker/)",
    )
    parser.add_argument("--output", type=Path, required=True, help="output ZIP path")
    parser.add_argument(
        "--minimum-driver",
        default=CUDA_RUNTIME_V2_MIN_DRIVER_VERSION,
        help="minimum NVIDIA display driver recorded in the manifest",
    )
    parser.add_argument(
        "--python-version",
        default="3.11",
        help="bundled CPython version label",
    )
    parser.add_argument(
        "--ort-version",
        default="1.22.0",
        help="bundled ONNX Runtime version label",
    )
    parser.add_argument(
        "--no-generate-launchers",
        action="store_true",
        help="require launchers to already exist in the source tree",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="hash and list payload files without writing an archive",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if args.list_only:
            files = collect_payload_files(args.source_root)
            for item in files:
                print(f"{item.path}\t{item.size}\t{item.sha256}\t{item.role}")
            print(f"payload files: {len(files)}; bytes: {sum(item.size for item in files)}")
            return 0
        summary = build_bundle(
            args.source_root,
            args.output,
            python_version=args.python_version,
            ort_version=args.ort_version,
            minimum_driver_version=args.minimum_driver,
            generate_launchers=not args.no_generate_launchers,
        )
    except (CudaRuntimeV2Error, OSError) as exc:
        print(f"[cuda-runtime-v2] error: {exc}", file=sys.stderr)
        return 2
    print(
        f"[cuda-runtime-v2] wrote {summary['archive']} "
        f"({summary['archive_bytes']} bytes, sha256={summary['archive_sha256']})"
    )
    print(f"[cuda-runtime-v2] bundle id: {summary['bundle_id']}")
    sidecar = Path(str(summary["archive"]) + ".release.json")
    print(f"[cuda-runtime-v2] release metadata: {sidecar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
