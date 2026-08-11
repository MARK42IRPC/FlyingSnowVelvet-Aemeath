"""ONNX voice package discovery, installation, and legacy cleanup."""

from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import json
import locale
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable

import requests

from config.shared_storage import get_shared_root_dir
from config.user_storage_paths import get_user_state_dir
from lib.core.logger import get_logger
from lib.script.gsvmove.rar_backend import ensure_bundled_unrar


logger = get_logger(__name__)

VOICE_PACKAGE_FORMAT = "aemeath-gpt-sovits-onnx"
VOICE_PACKAGE_FORMAT_VERSION = 2
VOICE_PACKAGE_RUNTIME_REVISION = 6
VOICE_PACKAGE_DIR_NAME = "ONNX_aimisiV2"

_MODELSCOPE_ARCHIVE_BASE = (
    "https://www.modelscope.cn/models/Mark42IRPC/GSV_onnx_Aemeath_Pack/resolve/master"
)
_HUGGINGFACE_ARCHIVE_BASE = (
    "https://huggingface.co/Mark42IRP/Aemeath_onnx_GSV_model/resolve/main"
)
_INSTALL_STAGING_OVERHEAD_BYTES = 512 * 1024 * 1024

# The second UI bar covers all work after download. Keep each stage inside one
# monotonic scale so extraction cannot report 100% before validation/activation.
_INSTALL_PROGRESS_TOTAL = 1000
_INSTALL_EXTRACT_END = 600
_INSTALL_VERIFY_END = 940
_INSTALL_RUNTIME_END = 970
_INSTALL_ACTIVATE_END = 990
_INSTALL_CLEANUP_END = 999

_STATE_FILE_NAME = "voice_package.json"
_DOWNLOAD_TIMEOUT = (10.0, 90.0)
_METADATA_TIMEOUT = (3.0, 8.0)
_METADATA_HEADERS = {"Accept-Encoding": "identity"}
_DOWNLOAD_CHUNK_SIZE = 1024 * 1024
_OPTIONAL_CHECKSUM_PREFIXES = ("validation/",)
_G2PW_MODEL_DIR = "common/G2P/G2PW"
_RUNTIME_MODULES = (
    "genie_tts",
    "numpy",
    "onnx",
    "onnxruntime",
    "opencc",
    "rarfile",
    "soundfile",
    "soxr",
)

_REQUIRED_PACKAGE_FILES = (
    "manifest.json",
    "infer.py",
    "reference.wav",
    "reference.txt",
    "requirements.txt",
    "SHA256SUMS.txt",
    "character/t2s_encoder_fp32.onnx",
    "character/t2s_first_stage_decoder_fp32.onnx",
    "character/t2s_stage_decoder_fp32.onnx",
    "character/vits_v2pro.onnx",
    "common/chinese-hubert-base/chinese-hubert-base.onnx",
    "common/speaker_encoder.onnx",
    "common/RoBERTa/RoBERTa.onnx",
    "common/RoBERTa/roberta_tokenizer/tokenizer.json",
    "common/G2P/G2PW/g2pw_frontend.py",
    "common/G2P/G2PW/bopomofo_to_pinyin_wo_tune_dict.json",
    "common/G2P/G2PW/char_bopomofo_dict.json",
    "common/G2P/G2PW/POLYPHONIC_CHARS.txt",
    "common/G2P/G2PW/MONOPHONIC_CHARS.txt",
    "common/G2P/G2PW/config.py",
    "common/G2P/G2PW/polyphonic.rep",
    "common/G2P/G2PW/polyphonic-fix.rep",
    "common/G2P/EnglishG2P/checkpoint20.npz",
)

_PROFILE_REQUIRED_PACKAGE_FILES = {
    "fp32": (
        "character/t2s_encoder_fp32.bin",
        "character/t2s_shared_fp16.bin",
        "common/chinese-hubert-base/chinese-hubert-base_weights_fp16.bin",
    ),
    "fp16": (
        "character/t2s_encoder_fp16.bin",
        "character/t2s_shared_fp16.bin",
        "character/vits_v2pro_fp16.bin",
        "common/chinese-hubert-base/chinese-hubert-base_weights_fp16.bin",
        "common/speaker_encoder_fp16.bin",
    ),
    "int8": (
        "character/vits_v2pro_fp16.bin",
        "common/speaker_encoder_fp16.bin",
    ),
}

_LEGACY_BATCH_SET_PATTERN = re.compile(
    r'(?im)^\s*set\s+"?(?P<name>[A-Za-z_]\w*)=(?P<value>[^\r\n"]*)"?\s*$'
)
_LEGACY_BATCH_FIND_ROOT_PATTERN = re.compile(
    r'(?im)^\s*call\s+:find_root\s+"(?P<path>[^"\r\n]+)"'
)
_LEGACY_BATCH_EXPAND_ROUNDS = 8


class VoicePackageError(RuntimeError):
    """Base error shown by the voice package installer."""


class VoicePackageCancelled(VoicePackageError):
    """Raised when the user cancels an active installation."""


@dataclass(frozen=True)
class VoicePackageValidation:
    valid: bool
    reason: str
    manifest: dict | None = None


@dataclass(frozen=True)
class VoicePackageStatus:
    kind: str
    reason: str
    package_root: Path | None = None
    legacy_root: Path | None = None

    @property
    def install_required(self) -> bool:
        return self.kind != "installed"


@dataclass(frozen=True)
class VoicePackageProfile:
    key: str
    title: str
    detail: str
    archive_name: str
    archive_bytes: int  # Offline estimate; online UI/install prefers mirror metadata.
    extracted_bytes: int

    def required_free_bytes_for(self, archive_bytes: int | None = None) -> int:
        """Return the space estimate using a remote size when available."""
        size = self.archive_bytes if archive_bytes is None else int(archive_bytes)
        if size <= 0:
            size = self.archive_bytes
        return size + self.extracted_bytes + _INSTALL_STAGING_OVERHEAD_BYTES

    @property
    def required_free_bytes(self) -> int:
        return self.required_free_bytes_for()

    @property
    def mirrors(self) -> tuple[tuple[str, str], ...]:
        return (
            ("ModelScope", f"{_MODELSCOPE_ARCHIVE_BASE}/{self.archive_name}"),
            ("Hugging Face", f"{_HUGGINGFACE_ARCHIVE_BASE}/{self.archive_name}"),
        )


@dataclass(frozen=True)
class VoicePackageRemoteSize:
    """Archive size read from a public package mirror response."""

    profile_key: str
    archive_bytes: int
    source_name: str
    url: str


VOICE_PACKAGE_PROFILES = {
    "fp32": VoicePackageProfile(
        "fp32",
        "完全包",
        "最高质量 · G2PW 混合前端",
        "Aemeath_ONNX_GSV_Complete_FP32.rar",
        1_750_536_512,
        2_077_867_114,
    ),
    "fp16": VoicePackageProfile(
        "fp16",
        "中等包",
        "均衡体积 · G2PW 混合前端",
        "Aemeath_ONNX_GSV_Medium_FP16.rar",
        1_390_864_984,
        1_532_429_768,
    ),
    "int8": VoicePackageProfile(
        "int8",
        "节约包",
        "强烈推荐 · G2PW 混合前端",
        "Aemeath_ONNX_GSV_Saver_INT8.rar",
        1_114_551_038,
        1_283_891_403,
    ),
}
DEFAULT_VOICE_PACKAGE_PROFILE = "fp16"


@dataclass(frozen=True)
class VoiceInstallResult:
    package_root: Path
    source_name: str
    warnings: tuple[str, ...] = ()


ProgressCallback = Callable[[str, int, int, str], None]
InfoCallback = Callable[[str], None]


def get_voice_package_state_path() -> Path:
    return get_user_state_dir(_STATE_FILE_NAME)


def get_gsvmove_launcher_path() -> Path:
    """Return the legacy launcher path without initializing the service."""
    return get_shared_root_dir() / "start_gsvmove.bat"


def is_gsvmove_launcher_available() -> bool:
    try:
        return get_gsvmove_launcher_path().is_file()
    except Exception:
        return False


def get_voice_package_profile(
    profile: str | VoicePackageProfile | None = None,
) -> VoicePackageProfile:
    if isinstance(profile, VoicePackageProfile):
        return profile
    key = str(profile or DEFAULT_VOICE_PACKAGE_PROFILE).strip().lower()
    try:
        return VOICE_PACKAGE_PROFILES[key]
    except KeyError as exc:
        raise ValueError(f"unknown voice package profile: {profile!r}") from exc


def get_voice_package_urls(
    profile: str | VoicePackageProfile | None = None,
) -> tuple[str, ...]:
    return tuple(url for _source, url in get_voice_package_profile(profile).mirrors)


def _response_header(response: object, name: str) -> str:
    headers = getattr(response, "headers", None)
    if headers is None:
        return ""
    try:
        return str(headers.get(name) or headers.get(name.lower()) or "").strip()
    except Exception:
        return ""


def _response_archive_bytes(response: object) -> int | None:
    """Read a full archive size from Content-Range or Content-Length."""
    content_range = _response_header(response, "Content-Range")
    if content_range:
        match = re.search(r"/\s*(\d+)\s*$", content_range)
        if match:
            total = int(match.group(1))
            if total > 0:
                return total

    content_length = _response_header(response, "Content-Length")
    try:
        total = int(content_length)
    except (TypeError, ValueError):
        return None
    return total if total > 0 else None


def _close_http_response(response: object | None) -> None:
    if response is None:
        return
    try:
        response.close()
    except Exception:
        pass


def _probe_archive_size(session: requests.Session, url: str) -> int | None:
    """Probe one mirror without downloading the archive body."""
    head_error: Exception | None = None
    head_response_received = False
    response = None
    try:
        response = session.head(
            url,
            headers=_METADATA_HEADERS,
            allow_redirects=True,
            timeout=_METADATA_TIMEOUT,
        )
        head_response_received = True
        response.raise_for_status()
        size = _response_archive_bytes(response)
        if size is not None:
            return size
    except Exception as exc:
        head_error = exc
    finally:
        _close_http_response(response)

    if head_error is not None and not head_response_received:
        raise head_error

    # Some package hosts reject HEAD or use chunked responses. A one-byte
    # range still returns the complete size in Content-Range without pulling
    # the archive into memory.
    response = None
    range_headers = dict(_METADATA_HEADERS)
    range_headers["Range"] = "bytes=0-0"
    try:
        response = session.get(
            url,
            headers=range_headers,
            stream=True,
            allow_redirects=True,
            timeout=_METADATA_TIMEOUT,
        )
        response.raise_for_status()
        if _response_header(response, "Content-Range"):
            return _response_archive_bytes(response)
        if getattr(response, "status_code", None) == 206:
            return None
        return _response_archive_bytes(response)
    except Exception as exc:
        if head_error is not None:
            raise exc from head_error
        raise
    finally:
        _close_http_response(response)


def _fetch_remote_archive_size(
    session: requests.Session,
    profile: VoicePackageProfile,
) -> VoicePackageRemoteSize | None:
    errors: list[str] = []
    for source_name, url in profile.mirrors:
        try:
            archive_bytes = _probe_archive_size(session, url)
            if archive_bytes is not None:
                return VoicePackageRemoteSize(
                    profile.key,
                    archive_bytes,
                    source_name,
                    url,
                )
            errors.append(f"{source_name}: response did not include archive size")
        except Exception as exc:
            errors.append(f"{source_name}: {exc}")
            logger.debug("语音包大小探测失败 [%s]: %s", source_name, exc)
    logger.debug("无法读取语音包远端大小 [%s]: %s", profile.key, "；".join(errors))
    return None


def fetch_voice_package_size(
    profile: str | VoicePackageProfile | None = None,
) -> VoicePackageRemoteSize | None:
    """Read one profile's archive size from ModelScope or Hugging Face."""
    resolved = get_voice_package_profile(profile)
    session = requests.Session()
    try:
        return _fetch_remote_archive_size(session, resolved)
    finally:
        session.close()


def fetch_voice_package_sizes(
    profiles: tuple[VoicePackageProfile, ...] | None = None,
) -> dict[str, VoicePackageRemoteSize]:
    """Read all profile sizes while reusing one short-lived HTTP session."""
    selected = profiles or tuple(VOICE_PACKAGE_PROFILES.values())
    session = requests.Session()
    try:
        result: dict[str, VoicePackageRemoteSize] = {}
        for profile in selected:
            remote = _fetch_remote_archive_size(session, profile)
            if remote is not None:
                result[profile.key] = remote
        return result
    finally:
        session.close()


def _read_text_best_effort(path: Path) -> str:
    data = path.read_bytes()
    encodings: list[str] = []
    preferred = locale.getpreferredencoding(False)
    for encoding in ("utf-8", "utf-8-sig", preferred, "mbcs", "gbk"):
        if encoding and encoding not in encodings:
            encodings.append(encoding)
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except Exception:
            continue
    return data.decode(errors="ignore")


def _write_text_best_effort(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encodings: list[str] = []
    preferred = locale.getpreferredencoding(False)
    for encoding in (preferred, "mbcs", "utf-8"):
        if encoding and encoding not in encodings:
            encodings.append(encoding)
    last_error: Exception | None = None
    for encoding in encodings:
        try:
            path.write_text(text, encoding=encoding)
            return
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error


def _expand_batch_value(value: str, variables: dict[str, str], script_dir: Path) -> str:
    expanded = str(value or "").replace("%~dp0", f"{script_dir}{os.sep}")
    for _ in range(_LEGACY_BATCH_EXPAND_ROUNDS):
        def replace(match: re.Match[str]) -> str:
            key = match.group(1).strip().upper()
            if key in variables:
                return variables[key]
            env_value = os.environ.get(key)
            return env_value if env_value is not None else match.group(0)

        updated = re.sub(r"%([^%]+)%", replace, expanded)
        if updated == expanded:
            break
        expanded = updated
    return expanded.strip().strip('"')


def _parse_batch_variables(text: str, script_dir: Path) -> dict[str, str]:
    variables = {"SCRIPT_DIR": f"{script_dir}{os.sep}"}
    for match in _LEGACY_BATCH_SET_PATTERN.finditer(text):
        name = match.group("name").upper()
        variables[name] = _expand_batch_value(match.group("value"), variables, script_dir)
    return variables


def is_valid_legacy_gsvmove_root(root: Path | None) -> bool:
    if root is None:
        return False
    candidate = Path(root)
    return (
        candidate.is_dir()
        and (candidate / "start.bat").is_file()
        and (candidate / "api.py").is_file()
        and (candidate / "configs" / "tts_infer.yaml").is_file()
        and (candidate / ".venv" / "Scripts" / "python.exe").is_file()
    )


def _legacy_root_file(launcher_path: Path, launcher_text: str) -> Path:
    variables = _parse_batch_variables(launcher_text, launcher_path.parent)
    configured = variables.get("ROOT_FILE", "")
    return Path(configured) if configured else launcher_path.parent / "config" / "gsvmove_root.txt"


def _legacy_search_bases(launcher_path: Path, launcher_text: str) -> list[Path]:
    variables = _parse_batch_variables(launcher_text, launcher_path.parent)
    result: list[Path] = []
    seen: set[str] = set()
    for match in _LEGACY_BATCH_FIND_ROOT_PATTERN.finditer(launcher_text):
        raw = _expand_batch_value(match.group("path"), variables, launcher_path.parent)
        if not raw:
            continue
        candidate = Path(raw)
        key = os.path.normcase(os.path.abspath(str(candidate)))
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def _find_legacy_root_in_base(base: Path) -> Path | None:
    if not base.exists():
        return None
    skip_dirs = {".git", ".hg", ".svn", ".venv", "__pycache__", "node_modules"}
    for current_root, dirnames, filenames in os.walk(base):
        dirnames[:] = [item for item in dirnames if item not in skip_dirs]
        if "start.bat" not in filenames:
            continue
        candidate = Path(current_root)
        if is_valid_legacy_gsvmove_root(candidate):
            return candidate
    return None


def resolve_legacy_gsvmove_root(
    launcher_path: Path | None = None,
    *,
    scan_search_bases: bool = True,
) -> tuple[Path | None, Path | None]:
    launcher = Path(launcher_path) if launcher_path is not None else get_gsvmove_launcher_path()
    if not launcher.is_file():
        return None, None
    try:
        launcher_text = _read_text_best_effort(launcher)
    except Exception as exc:
        logger.warning("读取旧 GSVmove 启动脚本失败: %s", exc)
        return None, None

    root_file = _legacy_root_file(launcher, launcher_text)
    if root_file.is_file():
        try:
            configured_text = _read_text_best_effort(root_file).strip().strip('"')
            configured_root = Path(configured_text) if configured_text else None
        except Exception:
            configured_root = None
        if is_valid_legacy_gsvmove_root(configured_root):
            return configured_root, root_file

    if not scan_search_bases:
        return None, root_file

    for base in _legacy_search_bases(launcher, launcher_text):
        candidate = _find_legacy_root_in_base(base)
        if candidate is None:
            continue
        try:
            _write_text_best_effort(root_file, str(candidate))
        except Exception as exc:
            logger.debug("回写旧 GSVmove 路径失败: %s", exc)
        return candidate, root_file
    return None, root_file


def _safe_relative_path(raw_path: str) -> Path:
    normalized = str(raw_path or "").strip().replace("\\", "/")
    pure = PurePosixPath(normalized)
    if (
        not normalized
        or pure.is_absolute()
        or ".." in pure.parts
        or re.match(r"^[A-Za-z]:", normalized)
    ):
        raise VoicePackageError(f"压缩包包含不安全路径：{raw_path}")
    return Path(*pure.parts)


def _find_g2pw_model_files(package_root: Path) -> tuple[str, ...]:
    """Return package-relative G2PW ONNX models without assuming precision."""
    model_dir = Path(package_root).joinpath(*_G2PW_MODEL_DIR.split("/"))
    try:
        candidates = tuple(model_dir.iterdir())
    except OSError:
        return ()

    return tuple(
        path.relative_to(package_root).as_posix()
        for path in sorted(candidates, key=lambda item: item.name.lower())
        if path.is_file()
        and path.suffix.lower() == ".onnx"
        and path.stem.lower().startswith("g2pw")
    )


def validate_voice_package(
    package_root: Path,
    *,
    verify_hashes: bool = False,
    progress_callback: Callable[[int, int], None] | None = None,
) -> VoicePackageValidation:
    root = Path(package_root)
    if not root.is_dir():
        return VoicePackageValidation(False, "语音包目录不存在")

    try:
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return VoicePackageValidation(False, f"manifest.json 无法读取：{exc}")

    if manifest.get("format") != VOICE_PACKAGE_FORMAT:
        return VoicePackageValidation(False, "语音包格式标识不匹配", manifest)
    if manifest.get("format_version") != VOICE_PACKAGE_FORMAT_VERSION:
        return VoicePackageValidation(False, "语音包格式版本不受支持", manifest)
    try:
        runtime_revision = int(manifest.get("runtime_revision") or 0)
    except (TypeError, ValueError):
        runtime_revision = 0
    if runtime_revision < VOICE_PACKAGE_RUNTIME_REVISION:
        return VoicePackageValidation(False, "语音包版本过旧，请安装最新语音包", manifest)
    languages = {str(item).lower() for item in manifest.get("languages", [])}
    if not {"zh", "en"}.issubset(languages):
        return VoicePackageValidation(False, "语音包缺少中英文前端", manifest)
    try:
        profile_required = _PROFILE_REQUIRED_PACKAGE_FILES[
            str(manifest.get("precision_profile") or "").strip().lower()
        ]
    except KeyError:
        return VoicePackageValidation(False, "语音包精度档位不受支持，请安装最新语音包", manifest)
    g2pw_models = _find_g2pw_model_files(root)
    if not g2pw_models:
        return VoicePackageValidation(
            False,
            f"语音包缺少 G2PW ONNX 模型：{_G2PW_MODEL_DIR}/g2pW*.onnx",
            manifest,
        )
    required_files = _REQUIRED_PACKAGE_FILES + profile_required + g2pw_models

    for relative in required_files:
        try:
            path = root / _safe_relative_path(relative)
            if not path.is_file() or path.stat().st_size <= 0:
                return VoicePackageValidation(False, f"语音包缺少必要文件：{relative}", manifest)
        except OSError as exc:
            return VoicePackageValidation(False, f"检查语音包文件失败：{exc}", manifest)

    if not verify_hashes:
        return VoicePackageValidation(True, "语音包可用", manifest)

    try:
        checksum_lines = (root / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
        checksum_entries: list[tuple[str, Path, str]] = []
        checksummed_paths: set[str] = set()
        for line in checksum_lines:
            match = re.fullmatch(r"([0-9a-fA-F]{64})\s+(.+)", line.strip())
            if match is None:
                if line.strip():
                    raise VoicePackageError("SHA256SUMS.txt 包含无效行")
                continue
            relative_text = match.group(2).replace("\\", "/")
            relative = _safe_relative_path(relative_text)
            checksummed_paths.add(relative_text)
            path = root / relative
            if not path.is_file():
                if relative_text.startswith(_OPTIONAL_CHECKSUM_PREFIXES):
                    continue
                raise VoicePackageError(f"校验清单中的文件缺失：{relative_text}")
            checksum_entries.append((relative_text, path, match.group(1).lower()))

        for required in required_files:
            if required not in checksummed_paths and required != "SHA256SUMS.txt":
                raise VoicePackageError(f"必要文件未纳入 SHA-256 清单：{required}")

        total = sum(path.stat().st_size for _, path, _ in checksum_entries)
        current = 0
        for relative_text, path, expected in checksum_entries:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while True:
                    chunk = stream.read(_DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    digest.update(chunk)
                    current += len(chunk)
                    if progress_callback is not None:
                        progress_callback(current, total)
            if digest.hexdigest().lower() != expected:
                raise VoicePackageError(f"语音包文件校验失败：{relative_text}")
    except Exception as exc:
        return VoicePackageValidation(False, str(exc), manifest)

    return VoicePackageValidation(True, "语音包校验通过", manifest)


def _load_install_state() -> dict:
    path = get_voice_package_state_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_install_state(package_root: Path, manifest: dict) -> None:
    path = get_voice_package_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": VOICE_PACKAGE_FORMAT,
        "format_version": VOICE_PACKAGE_FORMAT_VERSION,
        "runtime_revision": VOICE_PACKAGE_RUNTIME_REVISION,
        "package_root": str(package_root.resolve()),
        "package_name": str(manifest.get("name") or VOICE_PACKAGE_DIR_NAME),
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, path)


def list_fixed_drive_roots() -> tuple[Path, ...]:
    if os.name != "nt":
        return (Path("/"),)
    roots: list[Path] = []
    try:
        bitmask = int(ctypes.windll.kernel32.GetLogicalDrives())  # type: ignore[attr-defined]
        get_drive_type = ctypes.windll.kernel32.GetDriveTypeW  # type: ignore[attr-defined]
        for index in range(26):
            if not bitmask & (1 << index):
                continue
            root = Path(f"{chr(ord('A') + index)}:\\")
            if int(get_drive_type(str(root))) == 3 and root.exists():
                roots.append(root)
    except Exception as exc:
        logger.debug("枚举固定磁盘失败: %s", exc)
    return tuple(roots)


def _standard_package_candidates() -> tuple[Path, ...]:
    candidates: list[Path] = []
    state_root = str(_load_install_state().get("package_root") or "").strip()
    if state_root:
        candidates.append(Path(state_root))
    candidates.extend(_managed_voice_package_roots())

    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(os.path.abspath(str(candidate)))
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    return tuple(result)


def _managed_voice_package_roots() -> tuple[Path, ...]:
    return tuple(
        drive / "AemeathDeskPet" / "voice" / VOICE_PACKAGE_DIR_NAME
        for drive in list_fixed_drive_roots()
    )


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _same_path(left: Path, right: Path) -> bool:
    """Compare logical and resolved forms while both path parents still exist."""
    if _path_key(left) == _path_key(right):
        return True
    try:
        return _path_key(left.resolve(strict=False)) == _path_key(right.resolve(strict=False))
    except (OSError, RuntimeError):
        return False


def get_voice_package_status() -> VoicePackageStatus:
    invalid_reason = ""
    invalid_root: Path | None = None
    for candidate in _standard_package_candidates():
        validation = validate_voice_package(candidate)
        if validation.valid:
            return VoicePackageStatus("installed", validation.reason, candidate)
        if candidate.exists() and not invalid_reason:
            invalid_reason = validation.reason
            invalid_root = candidate

    launcher = get_gsvmove_launcher_path()
    if launcher.is_file():
        legacy_root, _ = resolve_legacy_gsvmove_root(launcher, scan_search_bases=False)
        return VoicePackageStatus(
            "legacy",
            "检测到旧版 GSVmove 语音服务",
            legacy_root=legacy_root,
        )
    if invalid_reason:
        return VoicePackageStatus("invalid", invalid_reason, invalid_root)
    return VoicePackageStatus("missing", "尚未安装 ONNX 语音包")


def remove_voice_package(package_root: Path) -> Path:
    """Delete only a voice package rooted in the installer-managed layout."""
    target = Path(package_root)
    allowed = {_path_key(candidate) for candidate in _managed_voice_package_roots()}
    if target.name != VOICE_PACKAGE_DIR_NAME or _path_key(target) not in allowed:
        raise VoicePackageError("拒绝删除不属于桌宠管理目录的语音包")
    if target.is_symlink():
        raise VoicePackageError("拒绝删除符号链接形式的语音包目录")
    if not target.is_dir():
        raise VoicePackageError("语音包目录不存在")

    # State records the resolved install path. Capture the match before removing
    # the directory so Windows junctions in CI/user storage can still resolve.
    state_path = get_voice_package_state_path()
    state_root = str(_load_install_state().get("package_root") or "").strip()
    state_matches_target = bool(state_root) and _same_path(Path(state_root), target)

    try:
        shutil.rmtree(target)
    except Exception as exc:
        raise VoicePackageError(f"删除语音包失败：{exc}") from exc
    if target.exists():
        raise VoicePackageError("删除语音包后目录仍然存在")

    if state_matches_target:
        try:
            state_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("语音包已删除，但安装状态清理失败: %s", exc)
    logger.info("已删除 ONNX 语音包: %s", target)
    return target


def missing_runtime_modules() -> tuple[str, ...]:
    return tuple(name for name in _RUNTIME_MODULES if importlib.util.find_spec(name) is None)


def _hidden_console_kwargs() -> dict:
    if os.name != "nt":
        return {}
    result: dict = {}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if flags:
        result["creationflags"] = flags
    startupinfo_class = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_class is not None:
        startupinfo = startupinfo_class()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        result["startupinfo"] = startupinfo
    return result


def _extract_command(
    unrar_path: Path,
    archive: Path,
    extract_root: Path,
) -> list[str]:
    return [
        str(unrar_path), "x", "-y", "-o+", "-p-", "-idq",
        str(archive), f"{extract_root}{os.sep}",
    ]


def _list_archive_members(archive: Path, unrar_path: Path) -> tuple[str, ...]:
    try:
        import rarfile

        rarfile.UNRAR_TOOL = str(unrar_path)
        with rarfile.RarFile(archive) as rar:
            if rar.needs_password():
                raise VoicePackageError("语音包归档不应包含密码")
            members = tuple(info.filename for info in rar.infolist())
    except VoicePackageError:
        raise
    except Exception as exc:
        raise VoicePackageError(f"无法读取语音包归档：{exc}") from exc
    if not members:
        raise VoicePackageError("语音包中没有可解压文件")
    for member in members:
        _safe_relative_path(member)
    return members


def _terminate_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                check=False,
                **_hidden_console_kwargs(),
            )
        else:
            proc.terminate()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _is_safe_legacy_delete_target(legacy_root: Path, active_package: Path) -> bool:
    try:
        root = legacy_root.resolve()
        active = active_package.resolve()
        shared = get_shared_root_dir().resolve()
    except OSError:
        return False
    if not is_valid_legacy_gsvmove_root(root):
        return False
    if root == Path(root.anchor) or root == shared or root == active:
        return False
    if root in active.parents or active in root.parents:
        return False
    return len(root.parts) >= 3


def remove_legacy_gsvmove_runtime(
    legacy_root: Path | None,
    active_package: Path,
    root_file: Path | None = None,
) -> tuple[str, ...]:
    warnings: list[str] = []
    if legacy_root is not None:
        if _is_safe_legacy_delete_target(legacy_root, active_package):
            try:
                shutil.rmtree(legacy_root)
                logger.info("已删除旧 GSVmove 运行时: %s", legacy_root)
            except Exception as exc:
                warnings.append(f"旧 GSVmove 目录未能删除：{exc}")
        elif Path(legacy_root).exists():
            warnings.append("旧 GSVmove 路径未通过安全检查，已保留")

    launcher = get_gsvmove_launcher_path()
    try:
        launcher.unlink(missing_ok=True)
    except OSError as exc:
        warnings.append(f"旧启动脚本未能删除：{exc}")

    if root_file is not None:
        try:
            resolved_root_file = root_file.resolve()
            shared = get_shared_root_dir().resolve()
            if shared == resolved_root_file.parent or shared in resolved_root_file.parents:
                resolved_root_file.unlink(missing_ok=True)
        except OSError as exc:
            warnings.append(f"旧路径记录未能删除：{exc}")
    return tuple(warnings)


class VoicePackageInstaller:
    """Transactional installer for one ONNX voice-package archive."""

    def __init__(
        self,
        *,
        progress_callback: ProgressCallback | None = None,
        info_callback: InfoCallback | None = None,
    ) -> None:
        self._progress_callback = progress_callback or (lambda *_args: None)
        self._info_callback = info_callback or (lambda _message: None)
        self._cancel_event = threading.Event()
        self._process_lock = threading.Lock()
        self._active_process: subprocess.Popen | None = None
        self._network_lock = threading.Lock()
        self._active_sessions: dict[object, set[requests.Session]] = {}
        self._profile = get_voice_package_profile()

    def cancel(self) -> None:
        self._cancel_event.set()
        with self._process_lock:
            proc = self._active_process
        if proc is not None:
            _terminate_process(proc)
        self._close_download_sessions()

    def close(self) -> None:
        self.cancel()

    def _check_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise VoicePackageCancelled("安装已取消")

    def _report(self, phase: str, current: int, total: int, message: str) -> None:
        self._progress_callback(phase, max(0, int(current)), max(0, int(total)), message)

    def _report_install_stage(
        self,
        current: int,
        total: int,
        start: int,
        end: int,
        message: str,
    ) -> None:
        if total <= 0:
            value = start
        else:
            ratio = max(0.0, min(1.0, float(current) / float(total)))
            value = int(round(start + ((end - start) * ratio)))
        self._report("extract", value, _INSTALL_PROGRESS_TOTAL, message)

    def _register_download_session(self, token: object, session: requests.Session) -> None:
        with self._network_lock:
            self._active_sessions.setdefault(token, set()).add(session)

    def _discard_download_session(self, token: object, session: requests.Session) -> None:
        with self._network_lock:
            sessions = self._active_sessions.get(token)
            if sessions is None:
                return
            sessions.discard(session)
            if not sessions:
                self._active_sessions.pop(token, None)

    def _close_download_sessions(self, token: object | None = None) -> None:
        with self._network_lock:
            if token is None:
                sessions = tuple(
                    session
                    for group in self._active_sessions.values()
                    for session in group
                )
                self._active_sessions.clear()
            else:
                sessions = tuple(self._active_sessions.pop(token, ()))
        for session in sessions:
            try:
                session.close()
            except Exception:
                pass

    def _download_archive(
        self,
        source_name: str,
        url: str,
        profile: VoicePackageProfile,
        download_dir: Path,
    ) -> Path:
        self._check_cancelled()
        final_path = download_dir / profile.archive_name
        partial_path = final_path.with_suffix(final_path.suffix + ".download")
        partial_path.unlink(missing_ok=True)
        message = f"正在从 {source_name} 下载{profile.title}"
        self._info_callback(message)
        session = requests.Session()
        token = object()
        self._register_download_session(token, session)
        try:
            self._check_cancelled()
            with session.get(url, stream=True, timeout=_DOWNLOAD_TIMEOUT) as response:
                response.raise_for_status()
                response_bytes = _response_archive_bytes(response)
                progress_total = response_bytes or profile.archive_bytes
                if response_bytes is not None:
                    self._profile = replace(self._profile, archive_bytes=response_bytes)
                with partial_path.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=_DOWNLOAD_CHUNK_SIZE):
                        self._check_cancelled()
                        if not chunk:
                            continue
                        output.write(chunk)
                        self._report(
                            "download",
                            output.tell(),
                            progress_total,
                            message,
                        )
            with partial_path.open("rb") as stream:
                if stream.read(8) != b"Rar!\x1a\x07\x01\x00":
                    raise VoicePackageError("下载文件不是有效的 RAR5 语音包")
            partial_path.replace(final_path)
            return final_path
        finally:
            self._discard_download_session(token, session)
            session.close()
            partial_path.unlink(missing_ok=True)

    def _download_mirror(
        self,
        source_name: str,
        url: str,
        profile: VoicePackageProfile,
        download_dir: Path,
    ) -> Path:
        self._report(
            "download",
            0,
            0,
            f"正在连接 {source_name}，准备下载{profile.title}",
        )
        return self._download_archive(source_name, url, profile, download_dir)

    def _download_parts(
        self,
        download_dir: Path,
        profile: VoicePackageProfile,
    ) -> tuple[str, Path]:
        errors: list[str] = []
        for source_name, url in profile.mirrors:
            self._check_cancelled()
            for child in download_dir.glob("*"):
                if child.is_file():
                    child.unlink(missing_ok=True)
            try:
                return source_name, self._download_mirror(source_name, url, profile, download_dir)
            except VoicePackageCancelled:
                raise
            except Exception as exc:
                errors.append(f"{source_name}: {exc}")
                logger.warning("语音包镜像下载失败 [%s]: %s", source_name, exc)
        raise VoicePackageError("所有语音包下载源均失败：" + "；".join(errors))

    def _prepare_backend_and_download(
        self,
        download_dir: Path,
        profile: VoicePackageProfile,
    ) -> tuple[Path, str, Path]:
        self._info_callback("正在启动下载，同时校验桌宠内置 RAR 解压后端")
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="voice_prepare") as executor:
            download_future = executor.submit(self._download_parts, download_dir, profile)
            try:
                unrar_path = ensure_bundled_unrar()
            except Exception as exc:
                self.cancel()
                try:
                    download_future.result()
                except Exception:
                    pass
                raise VoicePackageError(f"准备桌宠 RAR 解压后端失败：{exc}") from exc
            self._info_callback("内置 RAR 解压后端已就绪，语音包正在下载")
            source_name, archive = download_future.result()
        return unrar_path, source_name, archive

    def _extract(self, unrar_path: Path, archive: Path, extract_root: Path) -> None:
        _list_archive_members(archive, unrar_path)
        command = _extract_command(unrar_path, archive, extract_root)
        self._info_callback("正在解压语音包")
        # A solid archive writes a growing tree of large model files. Walking that
        # tree during each polling interval competes with UnRAR's metadata I/O and
        # can make the Qt UI appear frozen. SHA-256 verification reports exact
        # byte progress after extraction has completed.
        self._report(
            "extract",
            0,
            0,
            "正在解压角色模型与公共模型",
        )
        proc = subprocess.Popen(
            command,
            cwd=str(archive.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **_hidden_console_kwargs(),
        )
        with self._process_lock:
            self._active_process = proc
        try:
            while proc.poll() is None:
                self._check_cancelled()
                time.sleep(0.2)
            if proc.returncode != 0:
                raise VoicePackageError(f"解压器返回错误代码 {proc.returncode}")
        finally:
            with self._process_lock:
                if self._active_process is proc:
                    self._active_process = None
        self._report_install_stage(
            self._profile.extracted_bytes,
            self._profile.extracted_bytes,
            0,
            _INSTALL_EXTRACT_END,
            "语音包解压完成，正在校验",
        )

    @staticmethod
    def _locate_extracted_package(extract_root: Path) -> Path:
        direct = extract_root / VOICE_PACKAGE_DIR_NAME
        if (direct / "manifest.json").is_file():
            return direct
        if (extract_root / "manifest.json").is_file():
            return extract_root
        candidates = [
            path.parent
            for path in extract_root.glob("*/manifest.json")
            if path.is_file()
        ]
        if len(candidates) == 1:
            return candidates[0]
        raise VoicePackageError("解压结果中未找到唯一的 ONNX_aimisiV2 目录")

    def _ensure_runtime_dependencies(self, package_root: Path) -> None:
        missing = missing_runtime_modules()
        if not missing:
            self._report(
                "extract",
                _INSTALL_RUNTIME_END,
                _INSTALL_PROGRESS_TOTAL,
                "轻量 ONNX 推理组件已就绪",
            )
            return
        self._check_cancelled()
        self._info_callback("正在安装轻量 ONNX 推理组件")
        self._report(
            "extract",
            _INSTALL_VERIFY_END,
            _INSTALL_PROGRESS_TOTAL,
            "正在安装轻量 ONNX 推理组件",
        )
        python_executable = Path(sys.executable)
        if python_executable.name.lower() == "pythonw.exe":
            console_python = python_executable.with_name("python.exe")
            if console_python.is_file():
                python_executable = console_python
        command = [
            str(python_executable), "-m", "pip", "install",
            "--only-binary", "opencc-python-reimplemented",
            "-r", str(package_root / "requirements.txt"),
        ]
        started_at = time.monotonic()
        with tempfile.TemporaryFile() as output:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                **_hidden_console_kwargs(),
            )
            with self._process_lock:
                self._active_process = proc
            try:
                while proc.poll() is None:
                    self._check_cancelled()
                    if time.monotonic() - started_at >= 1200:
                        _terminate_process(proc)
                        raise VoicePackageError("ONNX 推理组件安装超时")
                    time.sleep(0.2)
                self._check_cancelled()
            finally:
                with self._process_lock:
                    if self._active_process is proc:
                        self._active_process = None

            if proc.returncode != 0:
                output.seek(0)
                detail = output.read().decode("utf-8", errors="replace").strip()
                raise VoicePackageError(f"ONNX 推理组件安装失败：{detail[-500:]}")
        still_missing = missing_runtime_modules()
        if still_missing:
            raise VoicePackageError("ONNX 推理组件仍然缺失：" + ", ".join(still_missing))
        self._report(
            "extract",
            _INSTALL_RUNTIME_END,
            _INSTALL_PROGRESS_TOTAL,
            "轻量 ONNX 推理组件安装完成",
        )

    @staticmethod
    def _activate_package(source_root: Path, target_root: Path, manifest: dict) -> None:
        target_root.parent.mkdir(parents=True, exist_ok=True)
        backup = target_root.with_name(f".{target_root.name}.previous-{uuid.uuid4().hex}")
        moved_old = False
        activated = False
        try:
            if target_root.exists():
                target_root.replace(backup)
                moved_old = True
            source_root.replace(target_root)
            _write_install_state(target_root, manifest)
            activated = True
        except Exception:
            if target_root.exists():
                shutil.rmtree(target_root, ignore_errors=True)
            if moved_old and backup.exists():
                backup.replace(target_root)
            raise
        finally:
            if activated and backup.exists():
                shutil.rmtree(backup, ignore_errors=True)

    def install(
        self,
        drive_root: Path,
        *,
        profile: str | VoicePackageProfile | None = None,
        archive_bytes: int | None = None,
        before_activate: Callable[[], None] | None = None,
    ) -> VoiceInstallResult:
        selected_profile = get_voice_package_profile(profile)
        if archive_bytes is not None:
            try:
                remote_size = int(archive_bytes)
            except (TypeError, ValueError):
                remote_size = 0
            if remote_size > 0:
                selected_profile = replace(selected_profile, archive_bytes=remote_size)
        self._profile = selected_profile
        drive = Path(drive_root)
        if not drive.is_dir():
            raise VoicePackageError(f"所选磁盘不可用：{drive}")
        free_bytes = shutil.disk_usage(drive).free
        if free_bytes < self._profile.required_free_bytes:
            free_gib = free_bytes / (1024 ** 3)
            required_gib = self._profile.required_free_bytes / (1024 ** 3)
            raise VoicePackageError(
                f"磁盘空间不足：{self._profile.title}安装时约需 {required_gib:.1f} GiB，"
                f"当前可用 {free_gib:.1f} GiB"
            )

        self._info_callback("磁盘空间检查完成，正在创建安装目录")
        if importlib.util.find_spec("rarfile") is None:
            raise VoicePackageError("缺少轻量 RAR 解析依赖，请先运行安装依赖脚本")
        install_base = drive / "AemeathDeskPet" / "voice"
        target_root = install_base / VOICE_PACKAGE_DIR_NAME
        stage_root = install_base / f".voice-install-{uuid.uuid4().hex}"
        download_dir = stage_root / "downloads"
        extract_root = stage_root / "extract"
        download_dir.mkdir(parents=True, exist_ok=False)
        extract_root.mkdir(parents=True, exist_ok=False)

        try:
            unrar_path, source_name, archive = self._prepare_backend_and_download(
                download_dir,
                self._profile,
            )
            self._check_cancelled()
            self._extract(unrar_path, archive, extract_root)
            self._check_cancelled()

            package_root = self._locate_extracted_package(extract_root)
            self._info_callback("正在执行 SHA-256 完整性校验")
            self._report(
                "extract",
                _INSTALL_EXTRACT_END,
                _INSTALL_PROGRESS_TOTAL,
                "正在执行 SHA-256 完整性校验",
            )
            validation = validate_voice_package(
                package_root,
                verify_hashes=True,
                progress_callback=lambda current, total: self._report_install_stage(
                    current,
                    total,
                    _INSTALL_EXTRACT_END,
                    _INSTALL_VERIFY_END,
                    "正在执行 SHA-256 完整性校验",
                ),
            )
            if not validation.valid or validation.manifest is None:
                raise VoicePackageError(validation.reason)
            self._report(
                "extract",
                _INSTALL_VERIFY_END,
                _INSTALL_PROGRESS_TOTAL,
                "语音包完整性校验通过",
            )
            self._ensure_runtime_dependencies(package_root)
            self._check_cancelled()

            self._report(
                "extract",
                _INSTALL_RUNTIME_END,
                _INSTALL_PROGRESS_TOTAL,
                "正在准备激活新语音包",
            )
            if before_activate is not None:
                before_activate()
            self._activate_package(package_root, target_root, validation.manifest)
            self._info_callback("新语音包已激活，正在清理已记录的旧运行时")
            self._report(
                "extract",
                _INSTALL_ACTIVATE_END,
                _INSTALL_PROGRESS_TOTAL,
                "新语音包已激活，正在清理已记录的旧运行时",
            )
            legacy_root, legacy_root_file = resolve_legacy_gsvmove_root(
                scan_search_bases=False
            )
            warnings = remove_legacy_gsvmove_runtime(
                legacy_root,
                target_root,
                legacy_root_file,
            )
            self._report(
                "extract",
                _INSTALL_CLEANUP_END,
                _INSTALL_PROGRESS_TOTAL,
                "旧运行时清理完成，正在收尾",
            )
            self._report(
                "extract",
                _INSTALL_PROGRESS_TOTAL,
                _INSTALL_PROGRESS_TOTAL,
                "ONNX 语音包安装完成",
            )
            return VoiceInstallResult(target_root, source_name, warnings)
        finally:
            shutil.rmtree(stage_root, ignore_errors=True)
            self._close_download_sessions()
