"""Build the self-contained Windows installer for the offline distribution.

The distribution payload is prepared by ``build_offline_distribution.py``. This
step only creates a ZIP with the already-staged files, compiles the small C
extractor with the local Visual Studio toolchain, and appends the ZIP plus a
SHA-256 trailer to the PE file. No dependency installation or network access
is performed.
"""

from __future__ import annotations

import argparse
from importlib import metadata
import hashlib
import json
import locale
from pathlib import Path
import shutil
import struct
import subprocess
import os
import re
import tempfile
import zipfile


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INSTALLER_SOURCE = PRODUCT_ROOT / "installer" / "windows"
MAGIC = b"FSV-OFFLINE-PAYLOAD-2"
TRAILER_FORMAT = "<24sQ32s"
TRAILER_SIZE = struct.calcsize(TRAILER_FORMAT)
MARKER_NAME = ".fsv-install-root"
MARKER_BYTES = MAGIC + b"\n"
ZLIB_SOURCES = (
    "adler32.c",
    "crc32.c",
    "inffast.c",
    "inflate.c",
    "inftrees.c",
    "zutil.c",
)
_VS_ENVIRONMENTS: dict[str, dict[str, str]] = {}


def sha256(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()


def _archive_entries(payload: Path) -> list[tuple[Path, str]]:
    """Return the final archive view, including the compatibility path map.

    Older staging runs placed the DSH tree at ``payload/services`` while the
    application resolves it below ``app/services``. Prefer that complete tree
    when it exists, and filter stale developer material from either layout.
    """
    legacy_dsh = payload / "services" / "dsh-office-runtime"
    entries: list[tuple[Path, str]] = []
    root_files = {
        "AGENTS.md",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "README.md",
        "RELEASING.md",
        "install_deps.py",
        ".tmp-office-settings.png",
        ".tmp-ort.json",
        "安装依赖.bat",
        "调试模式.bat",
    }
    excluded_app_parts = {
        ".claude",
        ".github",
        ".oprate",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "doc",
        "install_deps",
        "logs",
        "scripts",
        "tests",
        "用户反馈",
    }
    for item in sorted(payload.rglob("*")):
        if not item.is_file():
            continue
        relative = item.relative_to(payload)
        parts = relative.parts
        if any(part.lower() in {"__pycache__", ".pytest_cache", ".ruff_cache"} for part in parts):
            continue
        if len(parts) >= 2 and parts[0].lower() == "app" and parts[1].lower() in excluded_app_parts:
            continue
        if len(parts) == 1 and parts[0].lower() in {item.lower() for item in root_files}:
            continue
        if len(parts) == 2 and parts[0].lower() == "app" and parts[1].lower() in {item.lower() for item in root_files}:
            continue
        if tuple(part.lower() for part in parts[:2]) == ("app", "services") and legacy_dsh.is_dir():
            continue
        if tuple(part.lower() for part in parts[:1]) == ("services",):
            if not legacy_dsh.is_dir() or tuple(part.lower() for part in parts[1:2]) != ("dsh-office-runtime",):
                continue
            relative = Path("app", "services", *parts[1:])
        normalized_relative = relative.as_posix().lower()
        if normalized_relative.startswith("app/native/dx_backend/") and normalized_relative != "app/native/dx_backend/build/release/flying_snow_dx.dll":
            continue
        if normalized_relative == "app/resc/gif/seanima.zip":
            continue
        if normalized_relative.startswith("app/resc/models/"):
            allowed_models = (
                "app/resc/models/vosk-model-small-cn-0.22/",
                "app/resc/models/vosk-model-small-en-us-0.15/",
            )
            if not any(
                normalized_relative.startswith(prefix)
                for prefix in allowed_models
            ):
                continue
        entries.append((item, relative.as_posix()))
    return entries


def validate_payload(payload: Path) -> None:
    entries = {relative for _, relative in _archive_entries(payload)}
    folded_entries = {entry.casefold() for entry in entries}
    required = {
        ".fsv-install-root",
        "app/py.ini",
        "app/lib/core/qt_desktop_pet.py",
        "app/启动飞行雪绒.exe",
        "app/卸载飞行雪绒.exe",
        "app/services/dsh-office-runtime/package.json",
        "app/services/dsh-office-runtime/bridge/index.mjs",
        "app/services/dsh-office-runtime/profile/package.json",
        "app/resc/agent/office_system_prompt.txt",
        "app/resc/node-24.13.0-win-x64/node.exe",
        "app/resc/GIF/SEanima/耶比_anima/0001.webp",
        "app/resc/GIF/SEanima/星炬学院_anima/0001.webp",
        "runtime/python311/python.exe",
        "runtime/python311/pythonw.exe",
        "runtime/python311/Lib/site-packages/PyQt5/__init__.py",
        "runtime/python311/Lib/site-packages/vosk/__init__.py",
        "runtime/python311/Lib/site-packages/genie_tts/GetPhonesAndBert.py",
        "runtime/python311/Lib/site-packages/genie_tts/ModelManager.py",
        "runtime/python311/Lib/site-packages/genie_tts/G2P/English/EnglishG2P.py",
        "runtime/python311/Lib/site-packages/genie_tts/G2P/Chinese/ChineseG2P.py",
        "runtime/python311/Lib/site-packages/genie_tts-2.0.2.dist-info/METADATA",
        "runtime/onnx-directml/1.22.0-cp311-win_amd64/runtime.json",
        "runtime/onnx-directml/1.22.0-cp311-win_amd64/Lib/site-packages/onnxruntime/__init__.py",
        "runtime/onnx-directml/1.22.0-cp311-win_amd64/Lib/site-packages/onnxruntime/capi/DirectML.dll",
        "runtime/onnx-directml/1.22.0-cp311-win_amd64/Lib/site-packages/onnxruntime/capi/onnxruntime_pybind11_state.pyd",
        "runtime/onnx-directml/1.22.0-cp311-win_amd64/Lib/site-packages/onnxruntime_directml-1.22.0.dist-info/METADATA",
    }
    missing = sorted(entry for entry in required if entry.casefold() not in folded_entries)
    if missing:
        raise SystemExit(f"payload 不完整，缺少：{missing[0]}")
    site_prefix = "runtime/python311/Lib/site-packages/"
    required_modules = (
        "onnx",
        "onnxruntime",
        "genie_tts",
        "tokenizers",
        "pypinyin",
        "g2pM",
        "nltk",
        "jieba",
        "jieba_fast",
        "opencc",
        "soundfile",
        "soxr",
        "cv2",
        "pycaw",
        "comtypes",
        "win32com",
    )
    for module in required_modules:
        prefix = (site_prefix + module).casefold()
        if not any(
            path == prefix
            or path.startswith(prefix + "/")
            or path.startswith(prefix + ".")
            for path in folded_entries
        ):
            raise SystemExit(f"payload 缺少 CPU 推理/桌面依赖：{module}")
    if not any(path.startswith("app/resc/models/vosk-model-small-cn-0.22/") for path in folded_entries):
        raise SystemExit("payload 缺少 Vosk 中文识别模型")
    if not any(path.startswith("app/resc/models/vosk-model-small-en-us-0.15/") for path in folded_entries):
        raise SystemExit("payload 缺少 Vosk 英文识别模型")
    site_packages = payload / "runtime" / "python311" / "Lib" / "site-packages"
    versions = {
        str(dist.metadata.get("Name") or "").casefold().replace("_", "-").replace(".", "-"): dist.version
        for dist in metadata.distributions(path=[str(site_packages)])
    }
    if versions.get("genie-tts") != "2.0.2":
        raise SystemExit(f"payload 中 genie-tts 版本错误：{versions.get('genie-tts')!r}")
    if versions.get("onnxruntime") != "1.22.0":
        raise SystemExit(f"payload 中 CPU onnxruntime 版本错误：{versions.get('onnxruntime')!r}")
    if "runtime/python311/lib/site-packages/playwright/driver/node.exe" in folded_entries:
        raise SystemExit("payload 不应重复内置 Playwright Node；应复用 app/resc 下的发行版 Node")
    directml_root = (
        payload
        / "runtime"
        / "onnx-directml"
        / "1.22.0-cp311-win_amd64"
    )
    directml_site = directml_root / "Lib" / "site-packages"
    directml_versions = {
        str(dist.metadata.get("Name") or "").casefold().replace("_", "-").replace(".", "-"): dist.version
        for dist in metadata.distributions(path=[str(directml_site)])
    }
    if directml_versions.get("onnxruntime-directml") != "1.22.0":
        raise SystemExit(
            "payload 中 onnxruntime-directml 版本错误："
            f"{directml_versions.get('onnxruntime-directml')!r}"
        )
    try:
        directml_marker = json.loads(
            (directml_root / "runtime.json").read_text(encoding="utf-8")
        )
    except (OSError, TypeError, ValueError) as exc:
        raise SystemExit("payload 中 DirectML 运行时标记无效") from exc
    if not (
        isinstance(directml_marker, dict)
        and directml_marker.get("format") == "fsv-bundled-directml-overlay"
        and directml_marker.get("format_version") == 1
        and directml_marker.get("runtime") == "onnxruntime-directml"
        and directml_marker.get("version") == "1.22.0"
        and directml_marker.get("abi") == "cp311-win_amd64"
        and directml_marker.get("provider") == "DmlExecutionProvider"
    ):
        raise SystemExit("payload 中 DirectML 运行时标记不匹配")

    forbidden_roots = {
        "onnxruntime-gpu",
        "onnxruntime-cuda",
        "torch",
        "torchaudio",
        "torchvision",
        "tensorflow",
        "tensorrt",
        "nvidia",
    }
    forbidden_native_names = {
        "onnxruntime_providers_cuda.dll",
        "onnxruntime_providers_tensorrt.dll",
        "cudart64_12.dll",
        "cublas64_12.dll",
        "cublaslt64_12.dll",
        "cudnn64_9.dll",
    }
    for path in folded_entries:
        if not path.startswith(site_prefix.casefold()):
            continue
        relative = path[len(site_prefix):]
        root = relative.split("/", 1)[0].replace("_", "-").replace(".", "-")
        if any(root == name or root.startswith(name + "-") for name in forbidden_roots) or Path(path).name.casefold() in forbidden_native_names:
            raise SystemExit(f"基础 payload 含有被排除的语音/CUDA 组件：{path}")


def update_manifest_metadata(manifest: dict, payload: Path) -> None:
    site_packages = payload / "runtime" / "python311" / "Lib" / "site-packages"
    distributions = [
        {"name": dist.metadata["Name"], "version": dist.version}
        for dist in metadata.distributions(path=[str(site_packages)])
        if dist.metadata.get("Name")
    ]
    manifest.update({
        "format": 2,
        "product": "Flying Snow Velvet",
        "offline": True,
        "office_backend": "dsh",
        "speech_recognition": True,
        "voice_synthesis": True,
        "cuda_onnx": False,
        "optional_components": ["onnx_voice_package"],
        "directml": {
            "bundled": True,
            "version": "1.22.0",
            "abi": "cp311-win_amd64",
            "runtime_root": "runtime/onnx-directml/1.22.0-cp311-win_amd64",
        },
        "music_extensions": (site_packages / "pyncm").exists(),
        "python": {
            "major_minor": "3.11",
            "distributions": sorted(distributions, key=lambda item: item["name"].lower()),
        },
        "qt": {
            "python_modules": [
                "QtCore",
                "QtGui",
                "QtWidgets",
                "QtSvg",
                "QtMultimedia",
                "QtNetwork",
                "sip.cp311-win_amd64",
            ],
            "plugins": {
                "platforms": ["qwindows.dll", "qoffscreen.dll"],
                "imageformats": ["qgif.dll", "qico.dll", "qjpeg.dll", "qsvg.dll", "qwebp.dll"],
                "audio": ["qtaudio_wasapi.dll", "qtaudio_windows.dll"],
                "mediaservice": ["dsengine.dll", "qtmedia_audioengine.dll", "wmfengine.dll"],
                "styles": ["qwindowsvistastyle.dll"],
            },
        },
    })


def quote_cmd(path: Path) -> str:
    return f'"{path}"'


def find_vsdevcmd(explicit: Path | None) -> Path:
    candidates = [explicit] if explicit else []
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    candidates.extend([
        program_files / "Microsoft Visual Studio" / "2022" / "Community" / "Common7" / "Tools" / "VsDevCmd.bat",
        program_files / "Microsoft Visual Studio" / "2022" / "Professional" / "Common7" / "Tools" / "VsDevCmd.bat",
        program_files / "Microsoft Visual Studio" / "2022" / "Enterprise" / "Common7" / "Tools" / "VsDevCmd.bat",
    ])
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    raise SystemExit("找不到 Visual Studio VsDevCmd.bat，请通过 --vsdevcmd 指定 VS2022 安装路径")


def _load_vs_environment(vsdevcmd: Path) -> dict[str, str]:
    cache_key = str(vsdevcmd.resolve()).casefold()
    cached = _VS_ENVIRONMENTS.get(cache_key)
    if cached is not None:
        return cached.copy()

    with tempfile.TemporaryDirectory(prefix="fsv-vs-env-") as temporary:
        batch = Path(temporary) / "environment.cmd"
        environment_file = Path(temporary) / "environment.txt"
        batch.write_text(
            "@echo off\n"
            f"call {quote_cmd(vsdevcmd)} -arch=x64 -host_arch=x64 >nul\n"
            "if errorlevel 1 exit /b %errorlevel%\n"
            f"set > {quote_cmd(environment_file)}\n",
            encoding="utf-8-sig",
        )
        subprocess.run(
            ["cmd.exe", "/d", "/u", "/c", "call", str(batch)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        raw_environment = environment_file.read_bytes()
        looks_utf16 = (
            len(raw_environment) >= 2
            and raw_environment[1::2].count(0) > len(raw_environment) // 8
        )
        environment_lines = raw_environment.decode(
            "utf-16-le" if looks_utf16 else locale.getpreferredencoding(False),
            errors="replace",
        ).splitlines()
    environment = {key.upper(): value for key, value in os.environ.items()}
    for line in environment_lines:
        key, separator, value = line.partition("=")
        if separator and key and not key.startswith("="):
            environment[key.upper()] = value
    if shutil.which("cl.exe", path=environment.get("PATH")) is None:
        raise SystemExit("Visual Studio 环境初始化完成，但未找到 cl.exe")
    _VS_ENVIRONMENTS[cache_key] = environment
    return environment.copy()


def run_vs_command(vsdevcmd: Path, command: str, cwd: Path) -> None:
    batch = cwd / f".fsv-vs-command-{os.getpid()}.cmd"
    batch.write_text(f"@echo off\n{command}\n", encoding="utf-8-sig")
    try:
        subprocess.run(
            ["cmd.exe", "/d", "/c", "call", batch.name],
            cwd=cwd,
            env=_load_vs_environment(vsdevcmd),
            check=True,
        )
    finally:
        batch.unlink(missing_ok=True)


def ensure_payload_marker(workspace: Path, payload: Path) -> None:
    marker = payload / MARKER_NAME
    marker.write_bytes(MARKER_BYTES)

    manifest_path = workspace / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"缺少发行版 manifest：{manifest_path}")
    validate_payload(payload)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    update_manifest_metadata(manifest, payload)
    entries = [
        {
            "path": relative,
            "size": source.stat().st_size,
            "sha256": sha256(source).hex(),
        }
        for source, relative in _archive_entries(payload)
        if relative != MARKER_NAME
    ]
    entries.append({
        "path": MARKER_NAME,
        "size": len(MARKER_BYTES),
        "sha256": hashlib.sha256(MARKER_BYTES).hexdigest(),
    })
    manifest["files"] = sorted(entries, key=lambda entry: entry["path"])
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def create_archive(payload: Path, archive: Path) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(
        archive,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=1,
        allowZip64=True,
    ) as output:
        entries = _archive_entries(payload)
        for source, relative in entries:
            if relative == MARKER_NAME:
                continue
            output.write(source, relative)
        output.write(payload / MARKER_NAME, MARKER_NAME)


def _prepare_native_sources(installer_source: Path) -> tuple[Path, Path]:
    source_root = installer_source / "src"
    zlib_root = installer_source / "third_party" / "zlib-1.3.1"
    required = (
        source_root / "main.c",
        source_root / "zip_extract.c",
        source_root / "zip_extract.h",
        source_root / "launcher.c",
        source_root / "uninstaller.c",
        source_root / "resource.rc",
        source_root / "resource.h",
        source_root / "installer.manifest",
        source_root / "launcher.manifest",
        source_root / "uninstaller.manifest",
        zlib_root / "zlib.h",
        *(zlib_root / name for name in ZLIB_SOURCES),
    )
    missing = [path for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"缺少原生安装器源文件：{missing[0]}")
    return source_root, zlib_root


def _write_resource_script(path: Path, manifest_name: str) -> None:
    path.write_text(
        '#include "resource.h"\n\n'
        '#ifndef RT_MANIFEST\n#define RT_MANIFEST 24\n#endif\n'
        'IDI_INSTALLER ICON "icon.ico"\n'
        f'1 RT_MANIFEST "{manifest_name}"\n',
        encoding="ascii",
    )


def _compile_payload_binary(
    *,
    source_root: Path,
    icon_source: Path,
    vsdevcmd: Path,
    compile_root: Path,
    source_name: str,
    manifest_name: str,
    output_name: str,
) -> Path:
    compile_root.mkdir(parents=True, exist_ok=True)
    for name in (source_name, manifest_name, "resource.h"):
        shutil.copy2(source_root / name, compile_root / name)
    shutil.copy2(icon_source, compile_root / "icon.ico")
    _write_resource_script(compile_root / "native.rc", manifest_name)
    run_vs_command(vsdevcmd, 'rc.exe /nologo /fo"native.res" "native.rc"', compile_root)
    run_vs_command(
        vsdevcmd,
        " ".join([
            "cl.exe",
            "/nologo",
            "/MT",
            "/O2",
            "/W4",
            "/WX",
            "/utf-8",
            f'/Fe:"{output_name}"',
            f'"{source_name}"',
            '"native.res"',
            "/link",
            "/SUBSYSTEM:WINDOWS",
            "/DYNAMICBASE",
            "/HIGHENTROPYVA",
            "/NXCOMPAT",
            "/MANIFEST:NO",
            f'/OUT:"{output_name}"',
        ]),
        compile_root,
    )
    output = compile_root / output_name
    if not output.is_file():
        raise SystemExit(f"原生程序编译后不存在：{output}")
    return output


def compile_payload_binaries(
    payload: Path,
    installer_source: Path,
    icon_source: Path,
    vsdevcmd: Path,
    compile_root: Path,
) -> None:
    source_root, _ = _prepare_native_sources(installer_source)
    launcher = _compile_payload_binary(
        source_root=source_root,
        icon_source=icon_source,
        vsdevcmd=vsdevcmd,
        compile_root=compile_root / "launcher",
        source_name="launcher.c",
        manifest_name="launcher.manifest",
        output_name="FlyingSnowVelvetLauncher.exe",
    )
    uninstaller = _compile_payload_binary(
        source_root=source_root,
        icon_source=icon_source,
        vsdevcmd=vsdevcmd,
        compile_root=compile_root / "uninstaller",
        source_name="uninstaller.c",
        manifest_name="uninstaller.manifest",
        output_name="FlyingSnowVelvetUninstaller.exe",
    )
    app_root = payload / "app"
    app_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(launcher, app_root / "启动飞行雪绒.exe")
    shutil.copy2(uninstaller, app_root / "卸载飞行雪绒.exe")


def _write_payload_info_header(payload: Path, archive: Path, output: Path) -> None:
    entries = _archive_entries(payload)
    total_bytes = sum(source.stat().st_size for source, _ in entries)
    output.write_text(
        "#pragma once\n\n"
        f"#define FSV_PAYLOAD_ARCHIVE_BYTES ((ULONGLONG){archive.stat().st_size}ULL)\n"
        f"#define FSV_PAYLOAD_FILE_COUNT ((ULONGLONG){len(entries)}ULL)\n"
        f"#define FSV_PAYLOAD_UNCOMPRESSED_BYTES ((ULONGLONG){total_bytes}ULL)\n",
        encoding="ascii",
    )


def _compile_zlib(zlib_root: Path, vsdevcmd: Path, compile_root: Path) -> Path:
    local_root = compile_root / "zlib"
    local_root.mkdir(parents=True, exist_ok=True)
    for source in zlib_root.iterdir():
        if source.is_file():
            shutil.copy2(source, local_root / source.name)
    sources = " ".join(f'"zlib\\{name}"' for name in ZLIB_SOURCES)
    run_vs_command(
        vsdevcmd,
        f"cl.exe /nologo /c /MT /O2 /W3 /utf-8 /DZ_SOLO {sources}",
        compile_root,
    )
    objects = " ".join(f'"{Path(name).stem}.obj"' for name in ZLIB_SOURCES)
    run_vs_command(
        vsdevcmd,
        f'lib.exe /nologo /OUT:"zlibstatic.lib" {objects}',
        compile_root,
    )
    library = compile_root / "zlibstatic.lib"
    if not library.is_file():
        raise SystemExit("zlib 静态库编译失败")
    return library


def compile_installer(
    payload: Path,
    archive: Path,
    installer_source: Path,
    icon_source: Path,
    vsdevcmd: Path,
    compile_root: Path,
) -> Path:
    source_root, zlib_root = _prepare_native_sources(installer_source)
    compile_root.mkdir(parents=True, exist_ok=True)
    for name in (
        "main.c",
        "zip_extract.c",
        "zip_extract.h",
        "resource.rc",
        "resource.h",
        "installer.manifest",
    ):
        shutil.copy2(source_root / name, compile_root / name)
    shutil.copy2(icon_source, compile_root / "icon.ico")
    _write_payload_info_header(payload, archive, compile_root / "payload_info.h")
    _compile_zlib(zlib_root, vsdevcmd, compile_root)
    run_vs_command(vsdevcmd, 'rc.exe /nologo /fo"installer.res" "resource.rc"', compile_root)
    run_vs_command(
        vsdevcmd,
        " ".join([
            "cl.exe",
            "/nologo",
            "/MT",
            "/O2",
            "/W4",
            "/WX",
            "/utf-8",
            "/DZ_SOLO",
            '/I"zlib"',
            "/Fe:FlyingSnowVelvetInstaller.base.exe",
            '"main.c"',
            '"zip_extract.c"',
            '"zlibstatic.lib"',
            '"installer.res"',
            "/link",
            "/SUBSYSTEM:WINDOWS",
            "/DYNAMICBASE",
            "/HIGHENTROPYVA",
            "/NXCOMPAT",
            "/MANIFEST:NO",
            "/OUT:FlyingSnowVelvetInstaller.base.exe",
        ]),
        compile_root,
    )
    base_executable = compile_root / "FlyingSnowVelvetInstaller.base.exe"
    if not base_executable.is_file():
        raise SystemExit("安装器编译后不存在")
    return base_executable


def append_payload(base_executable: Path, archive: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    archive_hash = sha256(archive)
    shutil.copyfile(base_executable, output)
    with output.open("ab") as destination, archive.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            destination.write(chunk)
        destination.write(struct.pack(
            TRAILER_FORMAT,
            MAGIC,
            archive.stat().st_size,
            archive_hash,
        ))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--version",
        help="发行版本；默认读取 workspace/manifest.json 中的 version",
    )
    parser.add_argument("--vsdevcmd", type=Path)
    parser.add_argument(
        "--installer-source",
        type=Path,
        default=DEFAULT_INSTALLER_SOURCE,
        help="原生安装器源码根目录，默认使用仓库 installer/windows",
    )
    parser.add_argument(
        "--icon",
        type=Path,
        default=PRODUCT_ROOT / "resc" / "icon.ico",
        help="安装器、启动器与卸载器共用的 ICO 文件",
    )
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    payload = workspace / "payload"
    archive = workspace / "build" / "payload.zip"
    compile_root = workspace / "build" / ".installer-compile"
    try:
        workspace_manifest = json.loads((workspace / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        raise SystemExit(f"发行版 manifest 无效：{workspace / 'manifest.json'}") from exc
    version = str(args.version or workspace_manifest.get("version") or "").strip()
    if not version or not re.fullmatch(r"[A-Za-z0-9._+-]+", version):
        raise SystemExit(f"发行版本不能用于安装器文件名：{version!r}")
    output = (
        args.output
        or workspace / "dist" / f"FlyingSnowVelvet-{version}-Offline-Installer.exe"
    ).resolve()
    installer_source = args.installer_source.resolve()
    icon_source = args.icon.resolve()
    if not payload.is_dir():
        raise SystemExit(f"缺少 payload：{payload}")
    if not icon_source.is_file():
        raise SystemExit(f"缺少程序图标：{icon_source}")
    _prepare_native_sources(installer_source)
    vsdevcmd = find_vsdevcmd(args.vsdevcmd.resolve() if args.vsdevcmd else None)
    if compile_root.exists():
        shutil.rmtree(compile_root)
    compile_root.mkdir(parents=True)
    compile_payload_binaries(
        payload,
        installer_source,
        icon_source,
        vsdevcmd,
        compile_root / "payload-binaries",
    )
    ensure_payload_marker(workspace, payload)
    create_archive(payload, archive)
    base_executable = compile_installer(
        payload,
        archive,
        installer_source,
        icon_source,
        vsdevcmd,
        compile_root / "installer",
    )
    append_payload(base_executable, archive, output)
    print(f"已生成安装器：{output}")
    print(f"内置 ZIP：{archive} ({archive.stat().st_size} bytes)")
    print(f"安装器大小：{output.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
