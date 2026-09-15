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
import lzma
import locale
from pathlib import Path
import shutil
import struct
import subprocess
import os
import re
import tempfile
import sys
import zipfile


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INSTALLER_SOURCE = PRODUCT_ROOT / "installer" / "windows"
HARMONY_FONT_SOURCE = PRODUCT_ROOT / "resc" / "FRONTS" / "HarmonyOS_Sans_SC_Bold.ttf"
HARMONY_FONT_SUBSET_NAME = "FlyingSnowVelvet-Installer-HarmonyOS-Sans-SC-Subset.ttf"
MAGIC = b"FSV-OFFLINE-PAYLOAD-2"
TRAILER_FORMAT = "<24sQ32s"
TRAILER_SIZE = struct.calcsize(TRAILER_FORMAT)
MARKER_NAME = ".fsv-install-root"
MARKER_BYTES = MAGIC + b"\n"
# 打包阶段自己写进 payload 的文件（安装标记、启动器、卸载器）没有稳定的来源 mtime：
# 归档条目会带上生成时刻，同一份 payload 连打两次就会得到不同字节。统一钉到一个固定
# 时刻，两次打包的哈希比对才是在证明「打包是确定的」，而不是在比两次的时钟。
PACKAGED_FILE_MTIME = 1_700_000_000.0
# Mirrors ``build_offline_distribution.APP_DOC_ASSET_DIRECTORY``.  The archive
# filter below drops ``app/doc`` scratch material, and this is the one subtree
# the about page reads at runtime, so the two staging scripts must agree.
APP_DOC_ASSET_DIRECTORY = Path("doc") / "贡献名单和主播的狗盆"
ZLIB_SOURCES = (
    "adler32.c",
    "crc32.c",
    "inffast.c",
    "inflate.c",
    "inftrees.c",
    "zutil.c",
)
LZMA_DIRECTORY = Path("third_party") / "lzma-sdk"
LZMA_SOURCES = (
    "LzmaDec.c",
    "Lzma2Dec.c",
)
LZMA_HEADERS = (
    "LzmaDec.h",
    "Lzma2Dec.h",
    "7zTypes.h",
    "Compiler.h",
    "Precomp.h",
)
# The offline payload is packed as a handful of independent solid LZMA2
# streams ("shards") sitting inside an otherwise ordinary ZIP.  One solid
# stream compresses slightly better, but it would pin the whole extraction to
# a single core, which is what the adaptive worker pool exists to avoid.  Four
# shards keep every core's share bounded and cost well under one percent of the
# packed size.  The dictionary size has to be mirrored by the native decoder
# (``FSV_ZIP_SHARD_DICT_PROPERTY`` in ``installer/windows/src/zip_extract.h``)
# because a raw LZMA2 stream does not describe its own dictionary.
PAYLOAD_SHARD_INDEX_NAME = ".fsv-shard-index.bin"
PAYLOAD_SHARD_NAME_TEMPLATE = ".fsv-shard-{index:03d}.fsvlzma"
PAYLOAD_SHARD_TARGET_BYTES = 224 << 20
PAYLOAD_SHARD_DICT_SIZE = 64 << 20
PAYLOAD_SHARD_DICT_PROPERTY = 0x1C
PAYLOAD_SHARD_FILTERS = (
    {"id": lzma.FILTER_LZMA2, "preset": 9, "dict_size": PAYLOAD_SHARD_DICT_SIZE},
)
PAYLOAD_SHARD_INDEX_ROW = struct.Struct("<IQQ")
# 归档里由打包器自己生成的条目（分片索引、占位条目、在线版 marker）统一用 ZIP 纪元写出，
# 两次打包才会得到同样的字节。``ZipInfo`` 的默认值就是这个，但 ``ZipFile.writestr`` 收到
# 字符串名时会用当前时间，所以凡是自己造的条目都必须显式给 ``ZipInfo``。
ARCHIVE_ENTRY_DATE_TIME = (1980, 1, 1, 0, 0, 0)
_VS_ENVIRONMENTS: dict[str, dict[str, str]] = {}


def create_installer_font_subset(output: Path, source: Path = HARMONY_FONT_SOURCE) -> Path:
    """Create the small font used only by the native installer UI."""
    from fontTools import subset
    from fontTools.ttLib import TTFont
    source_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (
            PRODUCT_ROOT / "installer" / "windows" / "src" / "main.c",
            PRODUCT_ROOT / "installer" / "windows" / "src" / "uninstaller.c",
        )
    )
    chars = set(chr(code) for code in range(0x20, 0x7F))
    for literal in re.findall(r'L\"((?:\\.|[^\"])*)\"', source_text):
        chars.update(literal.replace(r'\"', '"').replace(r'\\', '\\'))
    options = subset.Options()
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.name_legacy = True
    options.glyph_names = True
    options.legacy_cmap = True
    options.symbol_cmap = True
    options.notdef_glyph = True
    options.notdef_outline = True
    options.recalc_average_width = True
    options.recalc_timestamp = False
    font = subset.load_font(str(source), options)
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(text="".join(sorted(chars)))
    subsetter.subset(font)
    output.parent.mkdir(parents=True, exist_ok=True)
    subset.save_font(font, str(output), options)
    # Fail the build if the generated TTF cannot be parsed or lost the family
    # name used by CreateFontW.
    check = TTFont(str(output), lazy=False)
    names = {name.toUnicode() for name in check["name"].names if name.nameID == 1}
    if "HarmonyOS Sans SC" not in names or len(check.getGlyphOrder()) < 2:
        raise RuntimeError("安装器特供字体校验失败")
    check.close()
    return output


def _write_installer_theme_header(output: Path) -> None:
    """Compile the announcement's light palette into the standalone native UI."""
    if str(PRODUCT_ROOT) not in sys.path:
        sys.path.insert(0, str(PRODUCT_ROOT))
    from lib.core.graphics.announcement_visuals import ANNOUNCEMENT_LIGHT_COLORS

    lines = ["#pragma once", ""]
    for name, color in ANNOUNCEMENT_LIGHT_COLORS.items():
        lines.append(
            f"#define FSV_COLOR_{name.upper()} RGB({color.red}, {color.green}, {color.blue})"
        )
    output.write_text("\n".join(lines) + "\n", encoding="ascii")

def _write_resource_urls_header(output: Path, version: str) -> None:
    name = f"FlyingSnowVelvet-{version}-Resources.zip"
    output.write_text(
        "#pragma once\n"
        f'#define FSV_RESOURCE_URL_HF L"https://huggingface.co/Mark42IRP/Aemeath_onnx_GSV_model/resolve/main/updates/{name}"\n'
        f'#define FSV_RESOURCE_URL_MODELSCOPE L"https://www.modelscope.cn/models/Mark42IRPC/GSV_onnx_Aemeath_Pack/resolve/master/updates/{name}"\n',
        encoding="ascii",
    )


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
        "启动程序.bat",
    }
    excluded_app_parts = {
        ".claude",
        ".github",
        ".localpage",
        ".oprate",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "doc",
        "install_deps",
        "local_pages",
        "logs",
        "scripts",
        "tests",
        "用户反馈",
    }
    bundled_doc_prefix = tuple(part.lower() for part in APP_DOC_ASSET_DIRECTORY.parts)
    for item in sorted(payload.rglob("*")):
        if not item.is_file():
            continue
        relative = item.relative_to(payload)
        parts = relative.parts
        if any(part.lower() in {"__pycache__", ".pytest_cache", ".ruff_cache"} for part in parts):
            continue
        if len(parts) >= 2 and parts[0].lower() == "app" and parts[1].lower() in excluded_app_parts:
            bundled_doc = (
                len(parts) >= 1 + len(bundled_doc_prefix)
                and all(
                    parts[1 + offset].lower() == part
                    for offset, part in enumerate(bundled_doc_prefix)
                )
            )
            if not bundled_doc:
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
        "app/resc/GIF/SEanima/爱弥斯联合_anima/0001.webp",
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
        "jieba_fast",
        "opencc",
        "soundfile",
        "soxr",
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
    # ``build_offline_distribution`` owns the full pruning rule; this mirrors the
    # parts that used to ship by accident.  A staged workspace must never carry
    # the pure-Python tokenizer fork, the keyword-extraction and SWIG trees, or
    # link-time/C++ build outputs.
    forbidden_site_prefixes = (
        "jieba/",
        "jieba_fast/analyse/",
        "jieba_fast/source/",
    )
    forbidden_site_suffixes = (".p", ".cc", ".lib", ".obj")
    # Qt's software OpenGL rasterizer only loads when something asks for a GL
    # context and the hardware paths failed; the workbench never does, so the
    # payload drops it (guarded by tests/test_qt_dependency_boundaries.py).
    forbidden_site_names = ("opengl32sw.dll",)
    site_prefix_folded = site_prefix.casefold()
    for path in folded_entries:
        if not path.startswith(site_prefix_folded):
            continue
        relative = path[len(site_prefix_folded):]
        if (
            relative.startswith(forbidden_site_prefixes)
            or relative.endswith(forbidden_site_suffixes)
            or relative.rsplit("/", 1)[-1] in forbidden_site_names
        ):
            raise SystemExit(f"payload 含有已剪枝的依赖文件：{path}")
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


def pin_packaged_mtime(path: Path) -> None:
    """把打包阶段生成的文件的 mtime 钉到固定时刻（见 ``PACKAGED_FILE_MTIME``）。"""
    os.utime(path, (PACKAGED_FILE_MTIME, PACKAGED_FILE_MTIME))


def ensure_payload_marker(workspace: Path, payload: Path) -> None:
    """写安装标记并刷新清单：只记路径与大小，不算 SHA-256。

    逐文件哈希要把上千兆文件重新读一遍，而它想防的「包内容不对」由打包前的真实启动
    与功能自检、以及打包时连打两次比对哈希一起兜住（见 ``main``）。
    """
    marker = payload / MARKER_NAME
    marker.write_bytes(MARKER_BYTES)
    pin_packaged_mtime(marker)

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
        }
        for source, relative in _archive_entries(payload)
        if relative != MARKER_NAME
    ]
    entries.append({
        "path": MARKER_NAME,
        "size": len(MARKER_BYTES),
    })
    manifest["files"] = sorted(entries, key=lambda entry: entry["path"])
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _shard_dictionary_property(dictionary_size: int) -> int:
    """Encode a dictionary size as the LZMA2 property byte.

    ``LZMA2_DIC_SIZE_FROM_PROP(p)`` is ``(2 | (p & 1)) << (p / 2 + 11)``, so a
    power-of-two dictionary of ``2 ** exponent`` bytes needs ``(exponent - 12)
    * 2``.  A raw LZMA2 stream carries no such byte, which is why the packer
    and the native decoder have to agree on it out of band.
    """
    if dictionary_size < 4096 or dictionary_size & (dictionary_size - 1):
        raise ValueError("LZMA2 字典大小必须是 4096 以上的 2 的幂")
    return (dictionary_size.bit_length() - 13) * 2


def _shard_plan(entries: list[tuple[Path, str]]) -> list[list[int]]:
    """Split the payload into solid shards, always cutting at file boundaries."""
    shards: list[list[int]] = []
    current: list[int] = []
    current_bytes = 0
    for index, (source, _) in enumerate(entries):
        size = source.stat().st_size
        if current and current_bytes + size > PAYLOAD_SHARD_TARGET_BYTES:
            shards.append(current)
            current = []
            current_bytes = 0
        current.append(index)
        current_bytes += size
    if current:
        shards.append(current)
    return shards


def _write_sharded_archive(
    output: zipfile.ZipFile,
    payload: Path,
    entries: list[tuple[Path, str]],
) -> None:
    """Store every payload byte in independent LZMA2 shards inside the ZIP.

    The per-file entries stay in the archive, but only as placeholders: the
    in-app updater still walks and vets each path, and the extractor learns
    from the central directory how many files and bytes to expect.  The bytes
    themselves live in the shard entries, and the index entry maps every
    placeholder - in central-directory order - to its shard and offset.
    """
    plan = _shard_plan(entries)
    shard_of = [0] * len(entries)
    offset_of = [0] * len(entries)
    sizes = [0] * len(entries)
    for number, members in enumerate(plan):
        info = zipfile.ZipInfo(
            PAYLOAD_SHARD_NAME_TEMPLATE.format(index=number), ARCHIVE_ENTRY_DATE_TIME
        )
        info.compress_type = zipfile.ZIP_STORED
        offset = 0
        compressor = lzma.LZMACompressor(
            format=lzma.FORMAT_RAW, filters=PAYLOAD_SHARD_FILTERS
        )
        with output.open(info, "w") as handle:
            pending = bytearray()
            for member in members:
                source, _ = entries[member]
                shard_of[member] = number
                offset_of[member] = offset
                with source.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(4 << 20), b""):
                        offset += len(chunk)
                        pending += compressor.compress(chunk)
                        if len(pending) >= (4 << 20):
                            handle.write(bytes(pending))
                            pending.clear()
                sizes[member] = offset - offset_of[member]
            pending += compressor.flush()
            if pending:
                handle.write(bytes(pending))
    rows = bytearray(struct.pack("<I", len(entries)))
    for member in range(len(entries)):
        rows += PAYLOAD_SHARD_INDEX_ROW.pack(
            shard_of[member], offset_of[member], sizes[member]
        )
    index_info = zipfile.ZipInfo(PAYLOAD_SHARD_INDEX_NAME, ARCHIVE_ENTRY_DATE_TIME)
    index_info.compress_type = zipfile.ZIP_STORED
    output.writestr(index_info, bytes(rows))
    for _, relative in entries:
        placeholder = zipfile.ZipInfo(relative, ARCHIVE_ENTRY_DATE_TIME)
        placeholder.compress_type = zipfile.ZIP_STORED
        output.writestr(placeholder, b"")
    output.write(payload / MARKER_NAME, MARKER_NAME)


def _patch_entry_sizes(archive: Path, sizes: dict[str, int]) -> None:
    """Advertise the real size on every placeholder entry.

    ``zipfile`` refuses to write a stored entry whose size does not match its
    data, so the placeholders are written empty and fixed up here.  The native
    extractor reserves each target file from this value and reports progress
    against it, and no byte length changes, so the offsets stay valid.
    """
    with zipfile.ZipFile(archive) as bundle:
        start_directory = bundle.start_dir
        members = bundle.infolist()
    with archive.open("r+b") as handle:
        position = start_directory
        for member in members:
            name = member.filename
            size = sizes.get(name)
            if size is not None and 0 < size < 0xFFFFFFFF:
                handle.seek(member.header_offset + 22)
                handle.write(struct.pack("<I", size))
                handle.seek(position + 24)
                handle.write(struct.pack("<I", size))
            position += (
                46
                + len(name.encode("utf-8"))
                + len(member.extra or b"")
                + len(member.comment or b"")
            )


def create_archive(payload: Path, archive: Path, *, sharded: bool = True) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        archive.unlink()
    entries = [
        (source, relative)
        for source, relative in _archive_entries(payload)
        if relative != MARKER_NAME
    ]
    if sharded:
        property_byte = _shard_dictionary_property(PAYLOAD_SHARD_DICT_SIZE)
        if property_byte != PAYLOAD_SHARD_DICT_PROPERTY:
            raise SystemExit("LZMA2 字典属性与原生解码器不一致")
        # The placeholder size lives in the 32-bit field a stored entry uses
        # for its data length, so a bigger file would silently lose its size.
        oversized = [
            relative
            for source, relative in entries
            if source.stat().st_size >= 0xFFFFFFFF
        ]
        if oversized:
            raise SystemExit(f"分片归档不支持 4 GiB 以上的单个文件：{oversized[0]}")
        with zipfile.ZipFile(
            archive, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True
        ) as output:
            _write_sharded_archive(output, payload, entries)
        _patch_entry_sizes(
            archive, {relative: source.stat().st_size for source, relative in entries}
        )
        return
    with zipfile.ZipFile(
        archive,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        # Level 6 is worth the extra build time for the plain archive: it saves
        # 24 MiB over level 1 for about 15 seconds of packaging, and the native
        # extractor inflates at the same speed either way.
        compresslevel=6,
        allowZip64=True,
    ) as output:
        for source, relative in entries:
            output.write(source, relative)
        output.write(payload / MARKER_NAME, MARKER_NAME)


def create_resource_archive(
    payload: Path, archive: Path, *, sharded: bool = False
) -> None:
    """Create the remotely distributable desktop/runtime resource package.

    The resource package intentionally contains the same payload tree as the
    installer archive, but is published as a plain ZIP so online installers
    and the in-app updater can fetch and overlay it without downloading an EXE.

    It defaults to ordinary Deflate.  LTS1.0.7pre4 的应用内更新器已经能读分片，
    但更早的客户端只会把分片归档里的占位条目解成空文件；等所有在用客户端都
    升到读得懂分片的版本后，再用 ``sharded=True`` 切换发布布局。
    """
    create_archive(payload, archive, sharded=sharded)


def _prepare_native_sources(installer_source: Path) -> tuple[Path, Path, Path]:
    source_root = installer_source / "src"
    zlib_root = installer_source / "third_party" / "zlib-1.3.1"
    lzma_root = installer_source / LZMA_DIRECTORY
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
        HARMONY_FONT_SOURCE,
        zlib_root / "zlib.h",
        *(zlib_root / name for name in ZLIB_SOURCES),
        *(lzma_root / name for name in (*LZMA_HEADERS, *LZMA_SOURCES)),
    )
    missing = [path for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"缺少原生安装器源文件：{missing[0]}")
    return source_root, zlib_root, lzma_root


def _write_resource_script(path: Path, manifest_name: str, *, include_font: bool = False) -> None:
    text = (
        '#include "resource.h"\n\n'
        '#ifndef RT_MANIFEST\n#define RT_MANIFEST 24\n#endif\n'
        'IDI_INSTALLER ICON "icon.ico"\n'
    )
    if include_font:
        text += 'IDR_HARMONY_FONT RCDATA "HarmonyOS_Sans_SC_Bold.ttf"\n'
    path.write_text(text + f'1 RT_MANIFEST "{manifest_name}"\n', encoding="ascii")


def _compile_payload_binary(
    *,
    source_root: Path,
    icon_source: Path,
    vsdevcmd: Path,
    compile_root: Path,
    source_name: str,
    manifest_name: str,
    output_name: str,
    embed_font: bool = False,
) -> Path:
    compile_root.mkdir(parents=True, exist_ok=True)
    for name in (source_name, manifest_name, "resource.h"):
        shutil.copy2(source_root / name, compile_root / name)
    shutil.copy2(icon_source, compile_root / "icon.ico")
    _write_installer_theme_header(compile_root / "installer_theme.h")
    if embed_font:
        create_installer_font_subset(compile_root / "HarmonyOS_Sans_SC_Bold.ttf")
    _write_resource_script(compile_root / "native.rc", manifest_name, include_font=embed_font)
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
            "/Brepro",
            "/link",
            "/Brepro",
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
    source_root, _, _ = _prepare_native_sources(installer_source)
    launcher = _compile_payload_binary(
        source_root=source_root,
        icon_source=icon_source,
        vsdevcmd=vsdevcmd,
        compile_root=compile_root / "launcher",
        source_name="launcher.c",
        manifest_name="launcher.manifest",
        output_name="FSVLauncher.exe",
    )
    uninstaller = _compile_payload_binary(
        source_root=source_root,
        icon_source=icon_source,
        vsdevcmd=vsdevcmd,
        compile_root=compile_root / "uninstaller",
        source_name="uninstaller.c",
        manifest_name="uninstaller.manifest",
        output_name="FlyingSnowVelvetUninstaller.exe",
        embed_font=True,
    )
    app_root = payload / "app"
    app_root.mkdir(parents=True, exist_ok=True)
    # The package ships exactly two executables: the launcher and the
    # uninstaller. No batch entry point is generated for the offline package.
    shutil.copy2(launcher, app_root / "启动飞行雪绒.exe")
    shutil.copy2(uninstaller, app_root / "卸载飞行雪绒.exe")
    pin_packaged_mtime(app_root / "启动飞行雪绒.exe")
    pin_packaged_mtime(app_root / "卸载飞行雪绒.exe")


def _write_payload_info_header(
    payload: Path,
    archive: Path | None,
    output: Path,
    *,
    online: bool,
) -> None:
    """烘焙安装器要用的 payload 常量。

    ``archive`` 只有在离线版才是那个内置的完整归档：在线版 EXE 里只有几百字节的 bootstrap
    marker，完整资源包是安装时另外下的，所以 ``FSV_PAYLOAD_ARCHIVE_BYTES`` 写成哨兵 0。
    原生安装器只拿它把「归档就在 EXE 里」与「归档要另外下载」分开（``set_payload`` 与下载
    进度条的初值），真值由 ``FSV_ONLINE_BUILD`` 决定。文件数与未压缩字节数两种模式都要：
    它们决定磁盘空间预估与解压进度。
    """
    if online and archive is not None:
        raise SystemExit("在线版不带内置归档，不该传 archive")
    if not online and archive is None:
        raise SystemExit("离线版必须提供内置归档")
    entries = _archive_entries(payload)
    total_bytes = sum(source.stat().st_size for source, _ in entries)
    archive_bytes = 0 if archive is None else archive.stat().st_size
    output.write_text(
        "#pragma once\n\n"
        f"#define FSV_PAYLOAD_ARCHIVE_BYTES ((ULONGLONG){archive_bytes}ULL)\n"
        f"#define FSV_PAYLOAD_FILE_COUNT ((ULONGLONG){len(entries)}ULL)\n"
        f"#define FSV_PAYLOAD_UNCOMPRESSED_BYTES ((ULONGLONG){total_bytes}ULL)\n"
        # The build mode is baked in rather than derived from the payload size:
        # the wizard paints its first page before it reads the trailer, so a
        # size comparison showed the online wording on the offline installer's
        # opening screen.
        f"#define FSV_ONLINE_BUILD {1 if online else 0}\n",
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
    archive: Path | None,
    version: str,
    installer_source: Path,
    icon_source: Path,
    vsdevcmd: Path,
    compile_root: Path,
    *,
    online: bool = False,
) -> Path:
    source_root, zlib_root, lzma_root = _prepare_native_sources(installer_source)
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
    lzma_compile_root = compile_root / "lzma"
    lzma_compile_root.mkdir(parents=True, exist_ok=True)
    for name in (*LZMA_HEADERS, *LZMA_SOURCES):
        shutil.copy2(lzma_root / name, lzma_compile_root / name)
    shutil.copy2(icon_source, compile_root / "icon.ico")
    installer_font = compile_root / HARMONY_FONT_SUBSET_NAME
    create_installer_font_subset(installer_font)
    shutil.copy2(installer_font, compile_root / "HarmonyOS_Sans_SC_Bold.ttf")
    _write_installer_theme_header(compile_root / "installer_theme.h")
    _write_resource_urls_header(compile_root / "resource_urls.h", version)
    _write_payload_info_header(
        payload, archive, compile_root / "payload_info.h", online=online
    )
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
            '/I"lzma"',
            "/Fe:FlyingSnowVelvetInstaller.base.exe",
            '"main.c"',
            '"zip_extract.c"',
            '"lzma\\LzmaDec.c"',
            '"lzma\\Lzma2Dec.c"',
            '"zlibstatic.lib"',
            '"installer.res"',
            "/Brepro",
            "/link",
            "/Brepro",
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


def create_online_marker_archive(archive: Path) -> None:
    """写出在线版 EXE 内置的小归档：只声明「完整资源要另外下载」。

    条目时间戳必须固定：``ZipFile.writestr`` 收到字符串名时会把当前时间写进条目，那样同一份
    payload 连打两次就不是同一份字节（见 ``_verify_reproducible``）。
    """
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as marker:
        for name, content in (
            (".fsv-online-resource-required", b"1\n"),
            (MARKER_NAME, MARKER_BYTES),
        ):
            entry = zipfile.ZipInfo(name, ARCHIVE_ENTRY_DATE_TIME)
            entry.compress_type = zipfile.ZIP_STORED
            marker.writestr(entry, content)


def _package_once(
    payload: Path,
    output: Path,
    *,
    version: str,
    installer_source: Path,
    icon_source: Path,
    vsdevcmd: Path,
    compile_root: Path,
    online: bool,
    resource_sharded: bool,
) -> tuple[Path, ...]:
    """按给定输出路径完整打一次包，返回这一次产出的文件。

    离线版产出安装器，在线版另外产出资源包 ZIP。同一份 payload 连打两次必须得到逐字节
    相同的文件，所以编译带 ``/Brepro``（链接器不再写入编译时间），归档顺序完全由 payload
    的排序决定，打包自己生成的文件按 ``PACKAGED_FILE_MTIME`` 写归档条目；调用方用两次
    产物的哈希比对来证明这一点（见 ``_verify_reproducible``）。
    """
    workspace = payload.parent
    compile_payload_binaries(
        payload,
        installer_source,
        icon_source,
        vsdevcmd,
        compile_root / "payload-binaries",
    )
    ensure_payload_marker(workspace, payload)
    # 在线版不带内置归档，只塞一个几百字节的 bootstrap marker，所以完全不建分片归档：
    # 那一步要把整份 payload 压一遍（发行构建里最长的一段），在线版压完就丢。
    archive = None
    if not online:
        archive = workspace / "build" / "payload.zip"
        create_archive(payload, archive)
    base_executable = compile_installer(
        payload,
        archive,
        version,
        installer_source,
        icon_source,
        vsdevcmd,
        compile_root / "installer",
        online=online,
    )
    # The online build deliberately carries only a tiny marker archive.  Full
    # desktop/runtime files are distributed through the resource ZIP produced
    # alongside this executable.
    if online:
        online_archive = workspace / "build" / "online-marker.zip"
        create_online_marker_archive(online_archive)
        append_payload(base_executable, online_archive, output)
        resource = output.parent / f"FlyingSnowVelvet-{version}-Resources.zip"
        create_resource_archive(payload, resource, sharded=resource_sharded)
        return (output, resource)
    append_payload(base_executable, archive, output)
    return (output,)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_reproducible(
    first: tuple[Path, ...], second: tuple[Path, ...]
) -> dict[str, str]:
    """两次打包的产物必须逐字节相同，返回保留下来的哈希。"""
    if [path.name for path in first] != [path.name for path in second]:
        raise SystemExit(
            "两次打包产出的文件不一致：" + ", ".join(path.name for path in first)
        )
    digests: dict[str, str] = {}
    for left, right in zip(first, second):
        left_digest = _sha256_file(left)
        right_digest = _sha256_file(right)
        if left_digest != right_digest:
            raise SystemExit(
                f"两次打包的结果不一致：{left.name} {left_digest} != {right_digest}"
            )
        digests[left.name] = left_digest
    return digests


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--version",
        help="发行版本；默认读取 workspace/manifest.json 中的 version",
    )
    parser.add_argument("--vsdevcmd", type=Path)
    parser.add_argument(
        "--online",
        action="store_true",
        help="生成在线版安装器（资源归档另行发布为 ZIP）",
    )
    parser.add_argument(
        "--resource-sharded",
        action="store_true",
        help="在线资源包改用 LZMA2 分片布局（需等所有在用客户端都能读分片）",
    )
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
    parser.add_argument(
        "--skip-launch-check",
        action="store_true",
        help="跳过打包前对 payload 的真实启动与功能自检",
    )
    parser.add_argument(
        "--skip-reproducible-check",
        action="store_true",
        help="只打一次包；默认连打两次并比对哈希，只保留一份",
    )
    args = parser.parse_args(argv)

    workspace = args.workspace.resolve()
    payload = workspace / "payload"
    compile_root = workspace / "build" / ".installer-compile"
    try:
        workspace_manifest = json.loads((workspace / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        raise SystemExit(f"发行版 manifest 无效：{workspace / 'manifest.json'}") from exc
    manifest_version = str(workspace_manifest.get("version") or "").strip()
    version = str(args.version or manifest_version).strip()
    if not version or not re.fullmatch(r"[A-Za-z0-9._+-]+", version):
        raise SystemExit(f"发行版本不能用于安装器文件名：{version!r}")
    if manifest_version and version != manifest_version:
        raise SystemExit(
            "命令行版本与 workspace manifest 不一致："
            f"{version!r} != {manifest_version!r}"
        )
    suffix = "Online-Installer" if args.online else "Offline-Installer"
    output = (args.output or PRODUCT_ROOT / "dist" / f"FlyingSnowVelvet-{version}-{suffix}.exe").resolve()
    installer_source = args.installer_source.resolve()
    icon_source = args.icon.resolve()
    if not payload.is_dir():
        raise SystemExit(f"缺少 payload：{payload}")
    if not icon_source.is_file():
        raise SystemExit(f"缺少程序图标：{icon_source}")
    if not args.skip_launch_check:
        # 打包之前先让 payload 自己跑一遍：真实启动 + 工作台、办公窗口、论坛、粒子与
        # 各项服务。它比逐文件哈希更能说明这份包能不能用。
        if str(PRODUCT_ROOT) not in sys.path:
            # 直接跑 ``py -3 scripts/build_offline_installer.py`` 时 sys.path[0] 是
            # ``scripts/``，按模块名引仓库代码要先补上仓库根。
            sys.path.insert(0, str(PRODUCT_ROOT))
        from scripts.verify_payload_runtime import verify_payload_runtime

        verify_payload_runtime(workspace, log=print)
    _prepare_native_sources(installer_source)
    vsdevcmd = find_vsdevcmd(args.vsdevcmd.resolve() if args.vsdevcmd else None)
    if compile_root.exists():
        shutil.rmtree(compile_root)
    compile_root.mkdir(parents=True)
    staging = output.parent / f".fsv-package-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        first = _package_once(
            payload,
            staging / output.name,
            version=version,
            installer_source=installer_source,
            icon_source=icon_source,
            vsdevcmd=vsdevcmd,
            compile_root=compile_root / "run-1",
            online=args.online,
            resource_sharded=args.resource_sharded,
        )
        digests = {path.name: _sha256_file(path) for path in first}
        if not args.skip_reproducible_check:
            # 打包两次对比哈希：一致就说明打包本身是确定的（也说明这次没打出残缺的包），
            # 比逐文件算 SHA-256 快得多，也不会被扫描/杀毒软件拖住。
            second = _package_once(
                payload,
                staging / "second" / output.name,
                version=version,
                installer_source=installer_source,
                icon_source=icon_source,
                vsdevcmd=vsdevcmd,
                compile_root=compile_root / "run-2",
                online=args.online,
                resource_sharded=args.resource_sharded,
            )
            digests = _verify_reproducible(first, second)
            print("两次打包结果一致，只保留第一份。")
        kept: list[Path] = []
        for path in first:
            target = output if path.name == output.name else output.parent / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, target)
            kept.append(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    print(f"已生成安装器：{output}")
    for target in kept:
        print(f"产出：{target} ({target.stat().st_size} bytes) sha256={digests[target.name]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
