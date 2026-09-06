"""Build a movable, offline Flying Snow Velvet distribution.

This command collects only already-installed files. It never invokes pip,
npm, git, a compiler, or a network client. The resulting Python tree is a
runtime, not a virtual environment that needs installation at first launch.
"""

from __future__ import annotations

import argparse
from email.parser import BytesParser
from importlib import metadata
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import zipfile

try:
    from scripts.release_common import read_app_version
except ModuleNotFoundError:  # direct ``python scripts/build_*.py`` invocation
    from release_common import read_app_version


def log_stage(message: str) -> None:
    print(f"[离线发行版] {message}", flush=True)


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_MARKER_NAME = ".fsv-install-root"
PAYLOAD_MARKER_BYTES = b"FSV-OFFLINE-PAYLOAD-2\n"
BUILD_STATE_NAME = ".fsv-distribution-state.json"
# These are the modules imported by the installed desktop application and by
# the CPU ONNX voice frontend.  The optional CUDA/TensorRT stack is deliberately
# absent; it is never copied into the base runtime.
DEFAULT_BASE_DISTRIBUTIONS = (
    "PyQt5",
    "Pillow",
    "numpy",
    "requests",
    "rarfile",
    "sounddevice",
    "vosk",
    "webrtcvad-wheels",
    "qrcode",
    "mutagen",
    "pyncm",
    "playwright",
    "opencv-python",
    "pycaw",
    "comtypes",
    "pywin32",
    "onnx",
    "onnxruntime",
    "genie-tts",
    "tokenizers",
    "pypinyin",
    "g2pM",
    "nltk",
    "jieba-fast",
    "jieba",
    "opencc-python-reimplemented",
    "soundfile",
    "soxr",
)
MUSIC_DISTRIBUTIONS = frozenset({"qrcode", "mutagen", "pyncm", "playwright"})
DIRECTML_RUNTIME_VERSION = "1.22.0"
DIRECTML_RUNTIME_ABI = "cp311-win_amd64"
DIRECTML_WHEEL_NAME = (
    f"onnxruntime_directml-{DIRECTML_RUNTIME_VERSION}-cp311-cp311-win_amd64.whl"
)
DIRECTML_RUNTIME_DIRECTORY = (
    Path("runtime")
    / "onnx-directml"
    / f"{DIRECTML_RUNTIME_VERSION}-{DIRECTML_RUNTIME_ABI}"
)
DIRECTML_MARKER_NAME = "runtime.json"

PINNED_BASE_DISTRIBUTIONS = {
    "genie-tts": "2.0.2",
    "onnxruntime": "1.22.0",
}

ONNXRUNTIME_RUNTIME_FILES = frozenset({
    "onnxruntime/LICENSE",
    "onnxruntime/ThirdPartyNotices.txt",
    "onnxruntime/__init__.py",
    "onnxruntime/capi/__init__.py",
    "onnxruntime/capi/_ld_preload.py",
    "onnxruntime/capi/_pybind_state.py",
    "onnxruntime/capi/build_and_package_info.py",
    "onnxruntime/capi/onnxruntime.dll",
    "onnxruntime/capi/onnxruntime_inference_collection.py",
    "onnxruntime/capi/onnxruntime_providers_shared.dll",
    "onnxruntime/capi/onnxruntime_pybind11_state.pyd",
    "onnxruntime/capi/onnxruntime_validation.py",
    "onnxruntime/capi/version_info.py",
})
DIRECTML_RUNTIME_FILES = ONNXRUNTIME_RUNTIME_FILES | {
    "onnxruntime/capi/DirectML.dll",
}

NODE_BUILD_SOURCE_SUFFIXES = frozenset({
    ".asm",
    ".c",
    ".cc",
    ".flow",
    ".gyp",
    ".h",
    ".hh",
    ".inc",
    ".mk",
    ".s",
    ".scss",
    ".ts",
    ".tsbuildinfo",
    ".tsx",
})
NODE_UNUSED_DIRECTORY_NAMES = frozenset({
    ".github",
    "__tests__",
    "benchmark",
    "benchmarks",
    "coverage",
    "docs",
    "documentation",
    "example",
    "examples",
    "test",
    "tests",
})
NODE_UNUSED_SUBTREES = (
    Path("@img") / "sharp-wasm32",
    Path("node-pty") / "prebuilds" / "darwin-arm64",
    Path("node-pty") / "prebuilds" / "darwin-x64",
    Path("node-pty") / "prebuilds" / "win32-arm64",
    Path("node-pty") / "deps",
    Path("node-pty") / "scripts",
    Path("node-pty") / "src",
    Path("node-pty") / "third_party",
    Path("sharp") / "install",
    Path("sharp") / "src",
    Path("koffi") / "vendor",
)

EXCLUDED_PARTS = {
    ".git",
    ".github",
    ".oprate",
    ".claude",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "tests",
    "scripts",
    "install_deps",
    "doc",
    "build",
    ".venv",
    "venv",
    "dist",
    "logs",
    "用户反馈",
    ".tmp-workbench-repro",
    "native/dx_backend",
    "resc/playwright",
    "resc/GIF/SEanima.zip",
    "resc/node-24.13.0-win-x64",
    "services/dsh-office-runtime/node_modules",
    "services/bundles",
    "resc/user",
}
EXCLUDED_PATH_SEQUENCES = tuple(
    tuple(part.lower() for part in PurePosixPath(value).parts)
    for value in EXCLUDED_PARTS
)
EXCLUDED_ROOT_FILES = {
    ".gitignore",
    "resc.net.txt",
    "requirements.txt",
    "requirements-dev.txt",
    "RELEASING.md",
    "CHANGELOG.md",
    "README.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "install_deps.py",
    "安装依赖.bat",
    "调试模式.bat",
    ".tmp-office-settings.png",
    ".tmp-ort.json",
    "pyproject.toml",
    "setup.cfg",
    "resc/python-3.11.6-amd64.exe",
}

STDLIB_EXCLUDED_DIRS = {
    "__pycache__",
    "site-packages",
    "idlelib",
    "ensurepip",
    "venv",
    "tkinter",
    "turtledemo",
    "distutils",
    "lib2to3",
    "msilib",
    "pydoc_data",
    "curses",
    "dbm",
    "test",
    "tests",
}
STDLIB_EXCLUDED_DLLS = {
    "_msi.pyd",
    "_tkinter.pyd",
    "tcl86t.dll",
    "tk86t.dll",
    "winsound.pyd",
}

PYQT_MODULES = (
    "QtCore.pyd",
    "QtGui.pyd",
    "QtWidgets.pyd",
    "QtSvg.pyd",
    "QtMultimedia.pyd",
    "QtNetwork.pyd",
    "sip.cp311-win_amd64.pyd",
)
QT_BIN_FILES = (
    "Qt5Core.dll",
    "Qt5Gui.dll",
    "Qt5Widgets.dll",
    "Qt5Svg.dll",
    "Qt5Multimedia.dll",
    "Qt5Network.dll",
    "libEGL.dll",
    "libGLESv2.dll",
    "opengl32sw.dll",
    "d3dcompiler_47.dll",
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
)
QT_PLUGIN_FILES = {
    "platforms": ("qwindows.dll", "qoffscreen.dll"),
    "imageformats": ("qgif.dll", "qico.dll", "qjpeg.dll", "qsvg.dll", "qwebp.dll"),
    "audio": ("qtaudio_wasapi.dll", "qtaudio_windows.dll"),
    "mediaservice": ("dsengine.dll", "qtmedia_audioengine.dll", "wmfengine.dll"),
    "styles": ("qwindowsvistastyle.dll",),
}

FORBIDDEN_SITE_PARTS = {
    # Optional voice/CUDA packages are installed separately from the base
    # runtime.  Keep their large native trees out of the offline installer.
    "onnxruntime_gpu",
    "onnxruntime_directml",
    "torch",
    "torchaudio",
    "torchvision",
    "tensorflow",
    "nvidia",
    "directml",
}

# ``genie-tts`` publishes dependencies for its optional GUI, HTTP server,
# model downloader, Japanese frontend, and PyTorch converter.  The bundled
# ONNX package imports only the bilingual text frontend below.  Resolve that
# observed runtime chain explicitly so optional FastAPI/PySide/PyTorch and
# Hugging Face download stacks never enter the offline base image.
RUNTIME_DEPENDENCY_OVERRIDES = {
    "genie-tts": (
        "numpy",
        "onnx",
        "onnxruntime",
        "soundfile",
        "soxr",
        "tokenizers",
        "pypinyin",
        "g2pM",
        "nltk",
        "jieba-fast",
    ),
    # The Rust tokenizer used here loads a local tokenizer.json and does not
    # use tokenizers' optional hub integration.
    "tokenizers": (),
    # InferenceSession does not import ORT's command-line logging, FlatBuffers,
    # symbolic algebra, or packaging helpers. ONNX itself brings protobuf.
    "onnxruntime": ("numpy",),
    # The bilingual frontend uses TweetTokenizer and pos_tag. Their only
    # non-stdlib runtime import is regex; Vosk already brings tqdm separately.
    "nltk": ("regex",),
}


def normalize_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", str(value or "").strip().lower())


def excluded(relative: Path) -> bool:
    text = relative.as_posix().lower()
    if len(relative.parts) == 1 and relative.name.lower() in {item.lower() for item in EXCLUDED_ROOT_FILES}:
        return True
    if text.startswith("resc/models/"):
        allowed = (
            "resc/models/vosk-model-small-cn-0.22",
            "resc/models/vosk-model-small-en-us-0.15",
        )
        if not any(text == item or text.startswith(item + "/") for item in allowed):
            return True
    if text in {"resc/python-3.11.6-amd64.exe"}:
        return True
    parts = tuple(part.lower() for part in relative.parts)
    return any(
        len(sequence) <= len(parts)
        and any(
            parts[offset:offset + len(sequence)] == sequence
            for offset in range(len(parts) - len(sequence) + 1)
        )
        for sequence in EXCLUDED_PATH_SEQUENCES
    )


def copy_file(source: Path, target: Path) -> None:
    if not source.is_file():
        raise RuntimeError(f"缺少收集文件：{source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def copy_tree(source: Path, destination: Path) -> None:
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        if not item.is_file():
            continue
        if excluded(relative):
            continue
        target = destination / relative
        copy_file(item, target)


def _stdlib_file_excluded(relative: Path) -> bool:
    if any(part.lower() in STDLIB_EXCLUDED_DIRS for part in relative.parts):
        return True
    return relative.suffix.lower() in {".pyc", ".pyo"}


def copy_python_runtime(python_home: Path, runtime_root: Path) -> None:
    """Copy the interpreter and stdlib without development/tooling payloads."""
    python_home = python_home.resolve()
    for name in (
        "python.exe",
        "pythonw.exe",
        "python311.dll",
        "python3.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
    ):
        copy_file(python_home / name, runtime_root / name)

    source_lib = python_home / "Lib"
    target_lib = runtime_root / "Lib"
    for root, directories, files in os.walk(source_lib):
        directories[:] = [
            name for name in directories
            if name.lower() not in STDLIB_EXCLUDED_DIRS
        ]
        root_path = Path(root)
        for name in files:
            source = root_path / name
            relative = source.relative_to(source_lib)
            if _stdlib_file_excluded(relative):
                continue
            copy_file(source, target_lib / relative)

    source_dlls = python_home / "DLLs"
    target_dlls = runtime_root / "DLLs"
    for item in source_dlls.iterdir():
        if not item.is_file() or item.name.lower() in {name.lower() for name in STDLIB_EXCLUDED_DLLS}:
            continue
        copy_file(item, target_dlls / item.name)


def _active_requirement(requirement: str) -> bool:
    text = requirement.lower()
    if "extra ==" in text:
        return False
    if 'sys_platform == "win32"' in text and sys.platform != "win32":
        return False
    if 'sys_platform != "win32"' in text and sys.platform == "win32":
        return False
    if 'platform_system == "windows"' in text and os.name != "nt":
        return False
    if 'platform_system != "windows"' in text and os.name == "nt":
        return False
    for field, current in (
        ("python_full_version", tuple(sys.version_info[:3])),
        ("python_version", tuple(sys.version_info[:2])),
    ):
        match = re.search(
            rf'{field}\s*(==|!=|<=|>=|<|>)\s*["\']([0-9.]+)["\']',
            text,
        )
        if match is None:
            continue
        operator, raw_expected = match.groups()
        expected = tuple(int(part) for part in raw_expected.split("."))
        width = max(len(current), len(expected))
        left = current + (0,) * (width - len(current))
        right = expected + (0,) * (width - len(expected))
        comparisons = {
            "==": left == right,
            "!=": left != right,
            "<": left < right,
            "<=": left <= right,
            ">": left > right,
            ">=": left >= right,
        }
        if not comparisons[operator]:
            return False
    if 'implementation_name == "pypy"' in text and sys.implementation.name != "pypy":
        return False
    if 'implementation_name != "pypy"' in text and sys.implementation.name == "pypy":
        return False
    return True


def _requirement_name(requirement: str) -> str | None:
    if not _active_requirement(requirement):
        return None
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9_.-]*)", requirement)
    return match.group(1) if match else None


def _coerce_site_packages(value: Path | tuple[Path, ...] | list[Path]) -> tuple[Path, ...]:
    """Return dependency sources in overlay-first order without duplicates."""
    if isinstance(value, (str, Path)):
        values = (Path(value),)
    else:
        values = tuple(Path(item) for item in value)
    result: list[Path] = []
    seen: set[Path] = set()
    for item in values:
        resolved = item.resolve()
        if resolved not in seen:
            result.append(resolved)
            seen.add(resolved)
    return tuple(result)


def collect_distributions(
    site_packages: Path | tuple[Path, ...] | list[Path],
    roots: tuple[str, ...],
) -> dict[str, metadata.Distribution]:
    """Resolve dependencies from ordered site-package overlays.

    ``importlib.metadata`` keeps each distribution's own ``locate_file`` root,
    so a CPU wheel in an overlay can safely replace a same-named GPU package in
    the base runtime without mixing their files.
    """
    sources = _coerce_site_packages(site_packages)
    available: dict[str, metadata.Distribution] = {}
    for source in sources:
        for dist in metadata.distributions(path=[str(source)]):
            name = dist.metadata.get("Name")
            if name:
                available.setdefault(normalize_name(name), dist)
    selected: dict[str, metadata.Distribution] = {}
    pending = list(roots)
    while pending:
        requested = pending.pop()
        key = normalize_name(requested)
        if key in selected:
            continue
        dist = available.get(key)
        if dist is None:
            raise RuntimeError(f"Python 依赖未在指定 site-packages 中找到：{requested}")
        selected[key] = dist
        requirements = RUNTIME_DEPENDENCY_OVERRIDES.get(key)
        if requirements is None:
            requirements = dist.requires or ()
        for requirement in requirements:
            dependency = _requirement_name(requirement)
            if dependency:
                pending.append(dependency)
    return selected


def _site_file_allowed(relative: Path) -> bool:
    lowered = relative.as_posix().lower()
    if any(part in {"__pycache__", "tests", "test", "testing"} for part in relative.parts):
        return False
    if relative.suffix.lower() in {".pyc", ".pyo", ".pyi", ".h", ".hpp", ".c", ".cpp", ".pxd", ".pyx", ".pxi", ".whl"}:
        return False
    if "/include/" in f"/{lowered}/" or lowered.endswith("/include"):
        return False
    if any(part.lower() in {"f2py", "distutils", "demos"} for part in relative.parts):
        return False
    if relative.name in {"RECORD", "INSTALLER", "direct_url.json"}:
        return False
    return True


def _distribution_source(
    dist: metadata.Distribution,
    relative: Path,
    site_packages: Path | tuple[Path, ...] | list[Path],
) -> Path | None:
    """Locate a distribution file while enforcing its source-root boundary."""
    roots = _coerce_site_packages(site_packages)
    candidates: list[Path] = []
    try:
        candidates.append(Path(dist.locate_file(PurePosixPath(relative.as_posix()))))
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    candidates.extend(root / relative for root in roots)
    for candidate in candidates:
        resolved = candidate.resolve()
        if not any(
            resolved == root or root in resolved.parents
            for root in roots
        ):
            continue
        if resolved.is_file():
            return resolved
    return None


def _site_file_source(
    relative: Path,
    site_packages: Path | tuple[Path, ...] | list[Path],
) -> Path | None:
    for root in _coerce_site_packages(site_packages):
        candidate = (root / relative).resolve()
        if (candidate == root or root in candidate.parents) and candidate.is_file():
            return candidate
    return None


def copy_distribution(
    dist: metadata.Distribution,
    site_packages: Path | tuple[Path, ...] | list[Path],
    target_root: Path,
) -> None:
    if not dist.files:
        raise RuntimeError(f"依赖没有文件清单，拒绝猜测复制范围：{dist.metadata['Name']}")
    for entry in dist.files:
        relative = Path(*PurePosixPath(str(entry)).parts)
        if relative.is_absolute() or ".." in relative.parts or not _site_file_allowed(relative):
            continue
        if (
            normalize_name(dist.metadata.get("Name", "")) == "onnxruntime"
            and relative.as_posix() not in ONNXRUNTIME_RUNTIME_FILES
            and ".dist-info/" not in relative.as_posix()
        ):
            continue
        source = _distribution_source(dist, relative, site_packages)
        if source is not None:
            copy_file(source, target_root / relative)


def copy_distribution_metadata(
    dist: metadata.Distribution,
    site_packages: Path | tuple[Path, ...] | list[Path],
    target_root: Path,
) -> None:
    for entry in dist.files or ():
        relative = Path(*PurePosixPath(str(entry)).parts)
        if ".dist-info" not in relative.as_posix() or relative.name in {"RECORD", "INSTALLER", "direct_url.json"}:
            continue
        source = _distribution_source(dist, relative, site_packages)
        if source is not None:
            copy_file(source, target_root / relative)


def _safe_wheel_member(name: str) -> PurePosixPath:
    if not name or "\\" in name or "\x00" in name:
        raise RuntimeError(f"DirectML wheel 含有非法路径：{name!r}")
    relative = PurePosixPath(name)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise RuntimeError(f"DirectML wheel 含有越界路径：{name!r}")
    return relative


def stage_directml_runtime(source: Path, payload_root: Path) -> dict[str, object]:
    """Expand the pinned DirectML wheel into an isolated inference overlay."""
    if not source.is_file() or source.name.casefold() != DIRECTML_WHEEL_NAME.casefold():
        raise RuntimeError(
            f"DirectML wheel 必须为固定版本 {DIRECTML_WHEEL_NAME}：{source}"
        )

    runtime_root = payload_root / DIRECTML_RUNTIME_DIRECTORY
    site_packages = runtime_root / "Lib" / "site-packages"
    copied: set[str] = set()
    metadata_name = ""
    metadata_version = ""
    with zipfile.ZipFile(source) as wheel:
        members: dict[str, zipfile.ZipInfo] = {}
        for info in wheel.infolist():
            relative = _safe_wheel_member(info.filename)
            normalized = relative.as_posix()
            if normalized in members:
                raise RuntimeError(f"DirectML wheel 含有重复文件：{normalized}")
            members[normalized] = info

        metadata_entries = [
            name
            for name in members
            if name.casefold().endswith(".dist-info/metadata")
        ]
        if len(metadata_entries) != 1:
            raise RuntimeError("DirectML wheel 缺少唯一的 METADATA")
        parsed = BytesParser().parsebytes(wheel.read(members[metadata_entries[0]]))
        metadata_name = str(parsed.get("Name") or "")
        metadata_version = str(parsed.get("Version") or "")
        if (
            normalize_name(metadata_name) != "onnxruntime-directml"
            or metadata_version != DIRECTML_RUNTIME_VERSION
        ):
            raise RuntimeError(
                f"DirectML wheel 元数据不匹配：{metadata_name!r} {metadata_version!r}"
            )

        dist_info_root = metadata_entries[0].rsplit("/", 1)[0]
        allowed = set(DIRECTML_RUNTIME_FILES)
        allowed.update({
            f"{dist_info_root}/METADATA",
            f"{dist_info_root}/WHEEL",
            f"{dist_info_root}/entry_points.txt",
            f"{dist_info_root}/top_level.txt",
        })
        required = set(DIRECTML_RUNTIME_FILES)
        required.update({f"{dist_info_root}/METADATA", f"{dist_info_root}/WHEEL"})
        missing = sorted(required - members.keys())
        if missing:
            raise RuntimeError(f"DirectML wheel 缺少运行文件：{missing[0]}")

        for name in sorted(allowed & members.keys()):
            info = members[name]
            unix_mode = (info.external_attr >> 16) & 0o170000
            if info.is_dir() or unix_mode == 0o120000:
                raise RuntimeError(f"DirectML wheel 运行文件类型无效：{name}")
            target = site_packages / Path(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with wheel.open(info) as source_handle, target.open("wb") as target_handle:
                shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
            copied.add(name)

    marker = {
        "format": "fsv-bundled-directml-overlay",
        "format_version": 1,
        "runtime": "onnxruntime-directml",
        "version": DIRECTML_RUNTIME_VERSION,
        "abi": DIRECTML_RUNTIME_ABI,
        "provider": "DmlExecutionProvider",
        "wheel_sha256": sha256(source),
        "files": len(copied),
    }
    (runtime_root / DIRECTML_MARKER_NAME).write_text(
        json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "bundled": True,
        "version": DIRECTML_RUNTIME_VERSION,
        "abi": DIRECTML_RUNTIME_ABI,
        "runtime_root": DIRECTML_RUNTIME_DIRECTORY.as_posix(),
        "site_packages": (
            DIRECTML_RUNTIME_DIRECTORY / "Lib" / "site-packages"
        ).as_posix(),
        "wheel_sha256": marker["wheel_sha256"],
        "files": len(copied),
    }


def copy_minimal_node_runtime(source: Path, destination: Path) -> None:
    """Copy the Node executable and license used by the prebuilt DSH sidecar."""
    copy_file(source / "node.exe", destination / "node.exe")
    copy_file(source / "LICENSE", destination / "LICENSE")


def share_playwright_node(site_packages: Path, node_executable: Path) -> bool:
    """Remove Playwright's byte-identical private Node copy.

    The native release launcher pins ``PLAYWRIGHT_NODEJS_PATH`` to the DSH
    executable.  Refuse to deduplicate if the binaries differ so a future
    Node/Playwright upgrade cannot silently create an ABI mismatch.
    """
    playwright_node = site_packages / "playwright" / "driver" / "node.exe"
    if not playwright_node.exists():
        return False
    if not node_executable.is_file():
        raise RuntimeError(f"共享 Playwright Node 前缺少发行版 Node：{node_executable}")
    if sha256(playwright_node) != sha256(node_executable):
        raise RuntimeError(
            "Playwright 内置 Node 与 DSH Node 不同，拒绝删除以避免浏览器驱动 ABI 不匹配"
        )
    playwright_node.unlink()
    return True


def prune_python_nonruntime_artifacts(site_packages: Path) -> dict[str, int]:
    """Drop development-only trees from packages that are shipped at runtime.

    These paths are not imported by the application: Playwright's async API and
    TypeScript declarations are unused because login workers use its sync API;
    pywin32's IDE, COM extension catalog, web server adapters, and help file
    are likewise outside the desktop feature set.  Keep this list explicit so
    a package upgrade fails closed if a path is unexpectedly absent only when
    the corresponding package is present.
    """
    paths = (
        Path("playwright") / "async_api",
        Path("playwright") / "driver" / "package" / "types",
        Path("pythonwin"),
        Path("win32comext"),
        Path("isapi"),
        Path("adodbapi"),
        Path("PyWin32.chm"),
    )
    removed_files = 0
    removed_bytes = 0
    for relative in paths:
        path = site_packages / relative
        if not path.exists():
            continue
        files = (path,) if path.is_file() else tuple(path.rglob("*"))
        for item in files:
            if not item.is_file():
                continue
            try:
                removed_bytes += item.stat().st_size
            except OSError:
                pass
            removed_files += 1
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    return {"removed_files": removed_files, "removed_bytes": removed_bytes}


def prune_node_modules(root: Path) -> dict[str, int]:
    """Remove artifacts that Node never loads during production execution."""
    removed_files = 0
    removed_bytes = 0
    documentation_names = (
        "readme",
        "changelog",
        "history",
        "authors",
        "contributors",
        "contributing",
    )

    for relative in NODE_UNUSED_SUBTREES:
        subtree = root / relative
        if not subtree.exists():
            continue
        if subtree.is_symlink() or subtree.is_file():
            items = (subtree,)
        else:
            items = tuple(item for item in subtree.rglob("*") if item.is_file())
        for item in items:
            try:
                removed_bytes += item.stat().st_size
            except OSError:
                pass
            removed_files += 1
        if subtree.is_symlink() or subtree.is_file():
            subtree.unlink()
        else:
            shutil.rmtree(subtree)

    for item in root.rglob("*"):
        if not item.is_file():
            continue
        relative = item.relative_to(root)
        lowered = item.name.casefold()
        in_unused_directory = any(
            part.casefold() in NODE_UNUSED_DIRECTORY_NAMES
            for part in relative.parts[:-1]
        )
        remove = (
            in_unused_directory
            or item.suffix.casefold() in NODE_BUILD_SOURCE_SUFFIXES
            or lowered.endswith((".pdb", ".map", ".d.ts", ".d.mts", ".d.cts"))
            or lowered.endswith((".test.js", ".test.mjs", ".test.cjs"))
            or lowered.endswith((".spec.js", ".spec.mjs", ".spec.cjs"))
            or lowered.endswith(".md")
            and not lowered.startswith(("license", "licence", "copying", "notice"))
            or lowered.startswith(documentation_names)
        )
        if not remove:
            continue
        try:
            removed_bytes += item.stat().st_size
        except OSError:
            pass
        item.unlink()
        removed_files += 1
    for directory in sorted(
        (item for item in root.rglob("*") if item.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            pass
    return {"removed_files": removed_files, "removed_bytes": removed_bytes}


def copy_native_runtime(source_root: Path, app_root: Path) -> bool:
    """Keep only the optional DirectX bridge DLL, never its build tree."""
    candidates = (
        source_root / "native" / "dx_backend" / "build" / "cmake" / "Release" / "flying_snow_dx.dll",
        source_root / "native" / "dx_backend" / "build" / "Release" / "flying_snow_dx.dll",
        source_root / "native" / "dx_backend" / "build" / "flying_snow_dx.dll",
    )
    for candidate in candidates:
        if candidate.is_file():
            target = app_root / "native" / "dx_backend" / "build" / "Release" / candidate.name
            copy_file(candidate, target)
            return True
    return False


def copy_minimal_pyqt5(
    site_packages: Path | tuple[Path, ...] | list[Path],
    target_root: Path,
    distributions: dict[str, metadata.Distribution],
) -> None:
    source = _site_file_source(Path("PyQt5") / "__init__.py", site_packages)
    if source is None:
        raise RuntimeError("PyQt5 文件缺失")
    source = source.parent
    for name in ("__init__.py", *PYQT_MODULES):
        copy_file(source / name, target_root / "PyQt5" / name)
    for dist_name in ("PyQt5", "PyQt5-Qt5", "PyQt5-sip"):
        dist = distributions.get(normalize_name(dist_name))
        if dist is None:
            raise RuntimeError(f"PyQt5 元数据缺失：{dist_name}")
        copy_distribution_metadata(dist, site_packages, target_root)

    qt_root = source / "Qt5"
    for name in QT_BIN_FILES:
        copy_file(qt_root / "bin" / name, target_root / "PyQt5" / "Qt5" / "bin" / name)
    for directory, names in QT_PLUGIN_FILES.items():
        for name in names:
            copy_file(
                qt_root / "plugins" / directory / name,
                target_root / "PyQt5" / "Qt5" / "plugins" / directory / name,
            )


def remove_forbidden_site_files(site_packages: Path) -> None:
    for item in site_packages.iterdir():
        item_key = normalize_name(item.name)
        if any(
            item_key == normalize_name(name)
            or item_key.startswith(normalize_name(name) + "-")
            for name in FORBIDDEN_SITE_PARTS
        ):
            raise RuntimeError(f"基础发行版意外包含可选语音/CUDA 依赖：{item.name}")


def validate_pinned_distributions(
    distributions: dict[str, metadata.Distribution],
) -> None:
    for name, expected in PINNED_BASE_DISTRIBUTIONS.items():
        distribution = distributions.get(normalize_name(name))
        actual = distribution.version if distribution is not None else None
        if actual != expected:
            raise RuntimeError(
                f"基础发行版要求 {name}=={expected}，当前收集到 {actual!r}"
            )


def prune_genie_tts_runtime(site_packages: Path) -> None:
    """Keep only the offline bilingual frontend consumed by ``infer.py``."""
    package = site_packages / "genie_tts"
    if not package.is_dir():
        raise RuntimeError("基础发行版缺少 genie-tts 2.0.2 文本前端")
    for relative in (
        "Audio",
        "Converter",
        "Data",
        "GUI",
        "G2P/Japanese",
        "Core/Inference.py",
        "Core/TTSPlayer.py",
        "Internal.py",
        "PredefinedCharacter.py",
        "Server.py",
        "Utils/Language.py",
        "Utils/Shared.py",
        "Utils/TextSplitter.py",
        "Utils/UserData.py",
    ):
        path = package / Path(relative)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    (package / "__init__.py").write_text(
        '"""Offline bilingual frontend subset used by Flying Snow Velvet."""\n'
        '__version__ = "2.0.2"\n',
        encoding="utf-8",
    )
    (package / "Core" / "Resources.py").write_text(
        '"""Local-only Genie resource paths for the bundled ONNX package."""\n'
        "import os\n\n"
        'GENIE_DATA_DIR = os.getenv("GENIE_DATA_DIR", "./GenieData")\n'
        'English_G2P_DIR = os.getenv("English_G2P_DIR", os.path.join(GENIE_DATA_DIR, "G2P", "EnglishG2P"))\n'
        'Chinese_G2P_DIR = os.getenv("Chinese_G2P_DIR", os.path.join(GENIE_DATA_DIR, "G2P", "ChineseG2P"))\n'
        'HUBERT_MODEL_DIR = os.getenv("HUBERT_MODEL_DIR", os.path.join(GENIE_DATA_DIR, "chinese-hubert-base"))\n'
        'SV_MODEL = os.getenv("SV_MODEL", os.path.join(GENIE_DATA_DIR, "speaker_encoder.onnx"))\n'
        'ROBERTA_MODEL_DIR = os.getenv("ROBERTA_MODEL_DIR", os.path.join(GENIE_DATA_DIR, "RoBERTa"))\n\n'
        "def ensure_exists(path, name):\n"
        "    if not os.path.exists(path):\n"
        "        raise FileNotFoundError(f\"Required local resource {name!r} was not found: {path}\")\n\n"
        "def download_genie_data():\n"
        "    raise RuntimeError(\"The offline runtime never downloads Genie resources\")\n\n"
        "# Resource checks are intentionally lazy: the bilingual text frontend\n"
        "# can run before an optional character voice package is installed.\n",
        encoding="utf-8",
    )


def stage_python_runtime(
    python_home: Path,
    site_packages_source: Path | tuple[Path, ...] | list[Path],
    runtime_root: Path,
    roots: tuple[str, ...],
) -> list[dict[str, str]]:
    copy_python_runtime(python_home, runtime_root)
    target_site = runtime_root / "Lib" / "site-packages"
    target_site.mkdir(parents=True, exist_ok=True)
    distributions = collect_distributions(site_packages_source, roots)
    validate_pinned_distributions(distributions)
    for key, dist in sorted(distributions.items()):
        if key in {normalize_name("PyQt5"), normalize_name("PyQt5-Qt5"), normalize_name("PyQt5-sip")}:
            continue
        copy_distribution(dist, site_packages_source, target_site)
    copy_minimal_pyqt5(site_packages_source, target_site, distributions)
    prune_genie_tts_runtime(target_site)
    prune_python_nonruntime_artifacts(target_site)
    remove_forbidden_site_files(target_site)
    return [
        {"name": dist.metadata["Name"], "version": dist.version}
        for dist in sorted(distributions.values(), key=lambda value: normalize_name(value.metadata["Name"]))
    ]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(payload: Path) -> list[dict[str, object]]:
    files = sorted(item for item in payload.rglob("*") if item.is_file())
    total = len(files)
    entries = []
    log_stage(f"正在生成逐文件 SHA-256 清单（{total} 个文件）")
    for index, item in enumerate(files, start=1):
        entries.append({
            "path": item.relative_to(payload).as_posix(),
            "size": item.stat().st_size,
            "sha256": sha256(item),
        })
        if index % 1000 == 0 or index == total:
            log_stage(f"清单进度：{index}/{total}（{index * 100 // max(total, 1)}%）")
    return entries


def write_payload_marker(payload: Path) -> None:
    """Mark a staged tree as a complete installer payload."""
    (payload / PAYLOAD_MARKER_NAME).write_bytes(PAYLOAD_MARKER_BYTES)


def write_release_launcher_config(app_root: Path) -> None:
    app_root.mkdir(parents=True, exist_ok=True)
    (app_root / "py.ini").write_text(
        "[Python]\n"
        "python_executable = ..\\runtime\\python311\\python.exe\n"
        "pythonw_executable = ..\\runtime\\python311\\pythonw.exe\n",
        encoding="utf-8",
    )
    # Keep this compatibility entry point ASCII-only. cmd.exe interprets a
    # UTF-8 BOM or a UTF-8 Chinese executable name using the active code page,
    # which turns the generated batch into an unusable command on Windows.
    # The native launcher is copied under the stable ASCII alias below.
    (app_root / "启动程序.bat").write_text(
        "@echo off\n"
        "setlocal DisableDelayedExpansion\n"
        "cd /d \"%~dp0\" || exit /b 1\n"
        "\"%~dp0FlyingSnowVelvetLauncher.exe\" %*\n"
        "exit /b %errorlevel%\n",
        encoding="ascii",
    )


def _distribution_build_state(
    *,
    source: Path,
    python_home: Path,
    site_packages_sources: tuple[Path, ...],
    node_runtime: Path,
    node_modules: Path,
    directml_wheel: Path,
    without_music: bool,
) -> dict[str, object]:
    """Return the explicit inputs used by a staged distribution.

    The state records a content fingerprint for every input tree.  A resume is
    an operator choice; the fingerprint prevents accidentally reusing a
    payload staged for a different source tree or dependency overlay.
    """
    def signature(
        path: Path,
        *,
        excluded_relative=None,
    ) -> dict[str, object]:
        """Build a compact recursive fingerprint for a file or directory.

        Directory mtime is not reliable when a nested file is edited in place.
        Include every file's relative name and metadata in the digest so a
        resume can never silently reuse a payload assembled from older inputs.
        The source tree uses the same exclusion rules as payload collection;
        otherwise the build's own generated directory would invalidate every
        subsequent resume.
        """
        try:
            stat_result = path.stat()
        except OSError:
            return {"path": str(path), "missing": True}
        if not path.is_dir():
            return {
                "path": str(path),
                "kind": "file",
                "size": stat_result.st_size,
                "mtime_ns": stat_result.st_mtime_ns,
                "sha256": sha256(path),
            }

        digest = hashlib.sha256()
        file_count = 0
        total_bytes = 0
        try:
            children = sorted(
                (item for item in path.rglob("*") if item.is_file()),
                key=lambda item: item.relative_to(path).as_posix(),
            )
            for item in children:
                relative = item.relative_to(path).as_posix()
                relative_path = PurePosixPath(relative)
                if excluded_relative is not None and excluded_relative(relative_path):
                    continue
                item_stat = item.stat()
                digest.update(relative.encode("utf-8", "surrogateescape"))
                digest.update(b"\0")
                digest.update(str(item_stat.st_size).encode("ascii"))
                digest.update(b"\0")
                digest.update(str(item_stat.st_mtime_ns).encode("ascii"))
                digest.update(b"\0")
                digest.update(sha256(item).encode("ascii"))
                digest.update(b"\n")
                file_count += 1
                total_bytes += item_stat.st_size
        except OSError:
            return {"path": str(path), "missing": True}
        return {
            "path": str(path),
            "kind": "directory",
            "size": stat_result.st_size,
            "mtime_ns": stat_result.st_mtime_ns,
            "file_count": file_count,
            "total_bytes": total_bytes,
            "sha256": digest.hexdigest(),
        }

    return {
        "format": 1,
        "source": signature(source, excluded_relative=excluded),
        "python_home": signature(python_home),
        "site_packages": [signature(item) for item in site_packages_sources],
        "node_runtime": signature(node_runtime),
        "node_modules": signature(node_modules),
        "directml_wheel": signature(directml_wheel),
        "without_music": bool(without_music),
    }


def _read_distribution_state(workspace: Path) -> dict[str, object] | None:
    path = workspace / BUILD_STATE_NAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write_distribution_state(workspace: Path, state: dict[str, object]) -> None:
    path = workspace / BUILD_STATE_NAME
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _is_complete_staged_distribution(workspace: Path, payload: Path) -> bool:
    marker = payload / PAYLOAD_MARKER_NAME
    manifest = workspace / "manifest.json"
    if not marker.is_file() or marker.read_bytes() != PAYLOAD_MARKER_BYTES:
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return False
    entries = data.get("files") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        return False
    expected: set[str] = set()
    digest_pattern = re.compile(r"^[0-9a-f]{64}$")
    try:
        for entry in entries:
            if not isinstance(entry, dict):
                return False
            relative = PurePosixPath(str(entry.get("path", "")))
            if (
                not relative.parts
                or relative.is_absolute()
                or ".." in relative.parts
                or relative.as_posix() in expected
                or not isinstance(entry.get("size"), int)
                or entry["size"] < 0
                or not digest_pattern.fullmatch(str(entry.get("sha256", "")).lower())
            ):
                return False
            target = payload.joinpath(*relative.parts)
            if not target.is_file() or target.stat().st_size != entry["size"]:
                return False
            if sha256(target) != str(entry["sha256"]).lower():
                return False
            expected.add(relative.as_posix())
        actual = {
            item.relative_to(payload).as_posix()
            for item in payload.rglob("*")
            if item.is_file()
        }
    except (OSError, ValueError, TypeError):
        return False
    return actual == expected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=PRODUCT_ROOT)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--python-home", type=Path, required=True,
                        help="Python 3.11 安装根目录；只读收集，不修改该环境")
    parser.add_argument("--site-packages", type=Path,
                        help="基础 Python site-packages，默认使用 --python-home/Lib/site-packages")
    parser.add_argument(
        "--extra-site-packages",
        type=Path,
        action="append",
        default=[],
        help="额外依赖覆盖层，可重复指定；按参数顺序优先于基础 site-packages",
    )
    parser.add_argument("--dsh-node-runtime", type=Path, required=True,
                        help="预构建的 Node runtime 目录")
    parser.add_argument("--dsh-node-modules", type=Path, required=True,
                        help="已执行 npm ci 的 DSH node_modules 目录")
    parser.add_argument(
        "--directml-wheel",
        type=Path,
        required=True,
        help="固定的 onnxruntime-directml 1.22.0 cp311 win_amd64 wheel；构建时展开为包内隔离覆盖层",
    )
    parser.add_argument("--without-music", action="store_true",
                        help="不收集音乐登录/播放扩展，仅生成启动核心")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="删除当前工作区的已知生成目录后重新构建",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="输入指纹一致且 payload 已完成时复用现有工作区",
    )
    args = parser.parse_args()

    source = args.source_root.resolve()
    workspace = args.workspace.resolve()
    python_home = args.python_home.resolve()
    site_packages = (args.site_packages or python_home / "Lib" / "site-packages").resolve()
    site_packages_sources = tuple(
        dict.fromkeys(
            [Path(item).resolve() for item in args.extra_site_packages]
            + [site_packages]
        )
    )
    node_runtime = args.dsh_node_runtime.resolve()
    node_modules = args.dsh_node_modules.resolve()
    directml_wheel = args.directml_wheel.resolve()
    for required in (source, python_home, *site_packages_sources, node_runtime, node_modules, directml_wheel):
        if not required.exists():
            raise SystemExit(f"缺少收集输入：{required}")
    if workspace.exists():
        allowed = {
            "README.md", "manifest.json", BUILD_STATE_NAME,
            "build", "installer", "payload", "dist",
        }
        unexpected = [item.name for item in workspace.iterdir() if item.name not in allowed]
        if unexpected:
            raise SystemExit(f"发行版工作区含有未知文件，为避免覆盖而停止：{', '.join(unexpected)}")
    workspace.mkdir(parents=True, exist_ok=True)
    payload = workspace / "payload"
    if args.clean:
        for relative in ("payload", "manifest.json", BUILD_STATE_NAME):
            target = workspace / relative
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            elif target.exists() or target.is_symlink():
                target.unlink()
    state = _distribution_build_state(
        source=source,
        python_home=python_home,
        site_packages_sources=site_packages_sources,
        node_runtime=node_runtime,
        node_modules=node_modules,
        directml_wheel=directml_wheel,
        without_music=args.without_music,
    )
    if args.resume and payload.exists():
        if _read_distribution_state(workspace) != state:
            raise SystemExit("发行版工作区输入已变化，不能安全复用；请使用 --clean")
        if _is_complete_staged_distribution(workspace, payload):
            print(f"复用已完成的离线 payload：{payload}")
            return 0
        raise SystemExit("发行版工作区未完成，不能复用；请使用 --clean")
    if payload.exists():
        raise SystemExit(f"payload 已存在，为避免覆盖而停止：{payload}")

    log_stage("正在收集应用源码与资源")
    app = payload / "app"
    runtime = payload / "runtime" / "python311"
    services = payload / "app" / "services" / "dsh-office-runtime"
    copy_tree(source, app)
    copy_native_runtime(source, app)
    write_release_launcher_config(app)
    roots = tuple(
        name
        for name in DEFAULT_BASE_DISTRIBUTIONS
        if not args.without_music or name not in MUSIC_DISTRIBUTIONS
    )
    log_stage("正在收集 Python 3.11 与最小运行依赖")
    distributions = stage_python_runtime(python_home, site_packages_sources, runtime, roots)
    log_stage(f"Python 依赖闭包已收集（{len(distributions)} 个发行包）")
    node_target = app / "resc" / "node-24.13.0-win-x64"
    log_stage("正在收集并剪枝 DSH Node 运行时")
    copy_minimal_node_runtime(node_runtime, node_target)
    playwright_node_shared = share_playwright_node(
        runtime / "Lib" / "site-packages",
        node_target / "node.exe",
    )
    shutil.copytree(
        source / "services" / "dsh-office-runtime",
        services,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("node_modules"),
    )
    shutil.copytree(node_modules, services / "node_modules", dirs_exist_ok=True)
    node_pruning = prune_node_modules(services / "node_modules")

    log_stage("正在展开包内 DirectML 推理覆盖层")
    directml = stage_directml_runtime(directml_wheel, payload)

    write_payload_marker(payload)
    files = build_manifest(payload)
    manifest = {
        "format": 2,
        "product": "Flying Snow Velvet",
        "version": read_app_version(source),
        "offline": True,
        "office_backend": "dsh",
        "speech_recognition": True,
        "voice_synthesis": True,
        "cuda_onnx": False,
        "python": {
            "major_minor": "3.11",
            "distributions": distributions,
        },
        "qt": {
            "python_modules": [name.removesuffix(".pyd") for name in PYQT_MODULES],
            "plugins": {key: list(value) for key, value in QT_PLUGIN_FILES.items()},
        },
        "optional_components": ["onnx_voice_package"],
        "directml": directml,
        "node_pruning": node_pruning,
        "playwright_node_shared": playwright_node_shared,
        "music_extensions": not args.without_music,
        "files": files,
    }
    (workspace / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_distribution_state(workspace, state)
    print(f"已生成离线 payload：{payload}")
    print(f"文件数：{len(files)}")
    print(f"Python distributions：{len(distributions)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
