"""Game package registry, install, export, and extension loading."""

from __future__ import annotations

import importlib
import importlib.util
import hashlib
import json
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.shared_storage import ensure_shared_config_ready
from config.shared_storage_paths import get_shared_config_path, get_shared_root_dir
from lib.core.logger import get_logger
from lib.script.plugin_registry import effect_registry, particle_registry

_logger = get_logger(__name__)

_PACKAGE_FORMAT_VERSION = 1
_RUNTIME_API_VERSION = 1
_GAME_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,63}$")
_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_CLASS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class GamePackageError(RuntimeError):
    """Raised when a game package is malformed or incompatible."""


@dataclass(frozen=True)
class ExtensionSpec:
    kind: str
    module: str
    class_name: str
    local_id: str

    @property
    def qualified_id(self) -> str:
        return str(self.local_id).strip()


@dataclass(frozen=True)
class GamePackageManifest:
    game_id: str
    name: str
    version: str
    summary: str
    entry_module: str
    entry_class: str
    package_type: str = "game"
    package_format_version: int = _PACKAGE_FORMAT_VERSION
    runtime_api_version: int = _RUNTIME_API_VERSION
    official: bool = False
    author: str = ""
    description: str = ""
    command_aliases: tuple[str, ...] = ()
    default_width: int = 1000
    default_height: int = 800
    minimum_width: int = 600
    minimum_height: int = 480
    aspect_width: int = 10
    aspect_height: int = 8
    bgm_keyword: str = ""
    bgm_artist: str = ""
    particle_extensions: tuple[ExtensionSpec, ...] = ()
    effect_extensions: tuple[ExtensionSpec, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GamePackageManifest":
        window = data.get("window") if isinstance(data.get("window"), dict) else {}
        extensions = data.get("extensions") if isinstance(data.get("extensions"), dict) else {}

        def _parse_specs(kind: str) -> tuple[ExtensionSpec, ...]:
            raw_items = extensions.get(kind)
            if not isinstance(raw_items, list):
                return ()
            specs: list[ExtensionSpec] = []
            for index, raw in enumerate(raw_items):
                if not isinstance(raw, dict):
                    raise GamePackageError(f"{kind} 扩展项 #{index + 1} 不是对象")
                module = str(raw.get("module") or "").strip().replace("\\", "/")
                class_name = str(raw.get("class_name") or "").strip()
                local_id = str(raw.get("local_id") or "").strip()
                if not module or not class_name or not local_id:
                    raise GamePackageError(f"{kind} 扩展项 #{index + 1} 缺少 module/class_name/local_id")
                specs.append(ExtensionSpec(kind=kind, module=module, class_name=class_name, local_id=local_id))
            return tuple(specs)

        return cls(
            package_type=str(data.get("package_type") or "game").strip() or "game",
            package_format_version=int(data.get("package_format_version") or _PACKAGE_FORMAT_VERSION),
            runtime_api_version=int(data.get("runtime_api_version") or _RUNTIME_API_VERSION),
            game_id=str(data.get("game_id") or "").strip(),
            name=str(data.get("name") or "").strip(),
            version=str(data.get("version") or "").strip(),
            summary=str(data.get("summary") or "").strip(),
            entry_module=str(data.get("entry_module") or "").strip(),
            entry_class=str(data.get("entry_class") or "").strip(),
            official=bool(data.get("official", False)),
            author=str(data.get("author") or "").strip(),
            description=str(data.get("description") or "").strip(),
            command_aliases=tuple(
                alias
                for alias in (
                    str(item).strip()
                    for item in (data.get("command_aliases") if isinstance(data.get("command_aliases"), list) else [])
                )
                if alias
            ),
            default_width=max(320, int(window.get("default_width") or 1000)),
            default_height=max(240, int(window.get("default_height") or 800)),
            minimum_width=max(320, int(window.get("minimum_width") or 600)),
            minimum_height=max(240, int(window.get("minimum_height") or 480)),
            aspect_width=max(1, int(window.get("aspect_width") or 10)),
            aspect_height=max(1, int(window.get("aspect_height") or 8)),
            bgm_keyword=str(data.get("bgm_keyword") or "").strip(),
            bgm_artist=str(data.get("bgm_artist") or "").strip(),
            particle_extensions=_parse_specs("particles"),
            effect_extensions=_parse_specs("effects"),
        )


@dataclass(frozen=True)
class InstalledGame:
    manifest: GamePackageManifest
    install_dir: Path
    source: str

    @property
    def game_id(self) -> str:
        return self.manifest.game_id

    @property
    def code_root(self) -> Path:
        return self.install_dir / "code"

    @property
    def assets_root(self) -> Path:
        return self.install_dir / "assets"

    @property
    def data_root(self) -> Path:
        return _game_data_root() / self.game_id

    @property
    def cache_root(self) -> Path:
        return _game_cache_root() / self.game_id


@dataclass(frozen=True)
class GameContext:
    manifest: GamePackageManifest
    install_dir: Path
    data_root: Path
    cache_root: Path

    @property
    def code_root(self) -> Path:
        return self.install_dir / "code"

    @property
    def assets_root(self) -> Path:
        return self.install_dir / "assets"

    def asset_path(self, *parts: str) -> Path:
        return self.assets_root.joinpath(*parts)

    def qualify_particle_id(self, local_id: str) -> str:
        return qualify_game_extension_id(self.manifest.game_id, local_id)

    def qualify_effect_id(self, local_id: str) -> str:
        return qualify_game_extension_id(self.manifest.game_id, local_id)


def _game_home() -> Path:
    return get_shared_root_dir() / "games"


def _installed_root() -> Path:
    return _game_home() / "installed"


def _game_data_root() -> Path:
    return _game_home() / "data"


def _game_cache_root() -> Path:
    return _game_home() / "cache"


def _game_inbox_root() -> Path:
    return _game_home() / "inbox"


def _game_archive_root() -> Path:
    return _game_home() / "archive"


def _game_rejected_root() -> Path:
    return _game_home() / "rejected"


def _registry_path() -> Path:
    return get_shared_config_path("games", "registry.json")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _official_gamepack_root() -> Path:
    return _project_root() / "gamepack" / "official"


def _official_source_root() -> Path:
    return Path(__file__).resolve().parents[1] / "packages" / "official"


def qualify_game_extension_id(game_id: str, local_id: str) -> str:
    return f"{str(game_id).strip()}.{str(local_id).strip()}"


def _bind_extension_class(script_class, *, id_attr: str, qualified_id: str):
    attrs = {
        id_attr: str(qualified_id).strip(),
        "__module__": getattr(script_class, "__module__", __name__),
        "__qualname__": f"{script_class.__qualname__}Bound",
    }
    return type(f"{script_class.__name__}Bound", (script_class,), attrs)


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=False)
    else:
        path.unlink()


def _safe_file_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "").strip()) or "package"


def _choose_package_root(extracted_root: Path) -> Path:
    direct_manifest = extracted_root / "manifest.json"
    if direct_manifest.exists():
        return extracted_root
    children = [child for child in extracted_root.iterdir() if child.is_dir()]
    if len(children) == 1 and (children[0] / "manifest.json").exists():
        return children[0]
    raise GamePackageError("ZIP 根目录缺少 manifest.json")


class GamePackageService:
    """Manage installed game packages and their runtime extensions."""

    def __init__(self) -> None:
        ensure_shared_config_ready()
        self._installed: dict[str, InstalledGame] = {}
        self._registered_extensions: dict[str, dict[str, tuple[str, ...]]] = {}
        self._ensure_layout()
        self.refresh()
        self.bootstrap_official_packages()

    def _ensure_layout(self) -> None:
        for path in (
            _game_home(),
            _installed_root(),
            _game_data_root(),
            _game_cache_root(),
            _game_inbox_root(),
            _game_archive_root(),
            _game_rejected_root(),
            _registry_path().parent,
        ):
            _ensure_dir(path)

    def inbox_dir(self) -> Path:
        return _game_inbox_root()

    def archive_dir(self) -> Path:
        return _game_archive_root()

    def rejected_dir(self) -> Path:
        return _game_rejected_root()

    def installed_dir(self) -> Path:
        return _installed_root()

    def refresh(self) -> list[InstalledGame]:
        registry = self._load_registry()
        installed: dict[str, InstalledGame] = {}
        for game_root in sorted((path for path in _installed_root().iterdir() if path.is_dir()), key=lambda p: p.name):
            version_dirs = sorted((path for path in game_root.iterdir() if path.is_dir()), key=lambda p: p.name)
            manifest_dir = next((path for path in reversed(version_dirs) if (path / "manifest.json").exists()), None)
            if manifest_dir is None:
                continue
            manifest = self._load_manifest(manifest_dir / "manifest.json")
            source = str(registry.get(manifest.game_id, {}).get("source") or ("official" if manifest.official else "manual"))
            installed[manifest.game_id] = InstalledGame(manifest=manifest, install_dir=manifest_dir, source=source)
        self._installed = installed
        self._sync_registry()
        self._reload_extensions()
        return self.list_installed_games()

    def list_installed_games(self) -> list[InstalledGame]:
        return sorted(self._installed.values(), key=lambda item: (item.manifest.official is False, item.manifest.name.lower(), item.manifest.game_id))

    def get_installed_game(self, game_id: str) -> InstalledGame | None:
        return self._installed.get(str(game_id).strip())

    def bootstrap_official_packages(self) -> None:
        installed_from_gamepack: set[str] = set()
        zip_root = _official_gamepack_root()
        if zip_root.exists():
            for zip_path in sorted(zip_root.glob("*.zip"), key=lambda p: p.name.lower()):
                try:
                    manifest, source_signature = self._inspect_zip_package(zip_path)
                except Exception as exc:
                    _logger.warning("[GamePackages] 读取官方 ZIP 包失败 %s: %s", zip_path, exc)
                    continue
                installed = self._installed.get(manifest.game_id)
                if installed is not None:
                    try:
                        installed_signature = self._package_signature(installed.install_dir)
                    except Exception as exc:
                        _logger.warning("[GamePackages] 计算官方 ZIP 包签名失败 %s: %s", zip_path, exc)
                    else:
                        if installed_signature == source_signature:
                            installed_from_gamepack.add(manifest.game_id)
                            continue
                try:
                    self.install_from_zip(zip_path, source="official")
                except Exception as exc:
                    _logger.warning("[GamePackages] 安装官方 ZIP 包失败 %s: %s", zip_path, exc)
                    continue
                installed_from_gamepack.add(manifest.game_id)

        source_root = _official_source_root()
        if not source_root.exists():
            return
        for source_dir in sorted((path for path in source_root.iterdir() if path.is_dir()), key=lambda p: p.name):
            try:
                manifest = self._load_manifest(source_dir / "manifest.json")
            except Exception as exc:
                _logger.warning("[GamePackages] 读取官方包失败 %s: %s", source_dir, exc)
                continue
            if manifest.game_id in installed_from_gamepack:
                continue
            installed = self._installed.get(manifest.game_id)
            if installed is not None:
                try:
                    source_signature = self._package_signature(source_dir)
                    installed_signature = self._package_signature(installed.install_dir)
                except Exception as exc:
                    _logger.warning("[GamePackages] 计算官方包签名失败 %s: %s", source_dir, exc)
                else:
                    if installed_signature == source_signature:
                        continue
            try:
                self.install_from_source_dir(source_dir, source="official")
            except Exception as exc:
                _logger.warning("[GamePackages] 安装官方包失败 %s: %s", source_dir, exc)

    def install_from_zip(self, zip_path: Path, *, source: str = "zip") -> InstalledGame:
        if not zip_path.exists():
            raise GamePackageError(f"未找到 ZIP：{zip_path}")
        with tempfile.TemporaryDirectory(prefix="gamepkg_", dir=str(_game_cache_root())) as temp_dir:
            temp_root = Path(temp_dir) / "extract"
            _ensure_dir(temp_root)
            self._extract_zip(zip_path, temp_root)
            package_root = _choose_package_root(temp_root)
            installed = self.install_from_source_dir(package_root, source=source)
        return installed

    def install_from_source_dir(self, package_dir: Path, *, source: str = "directory") -> InstalledGame:
        package_dir = package_dir.resolve()
        manifest = self._load_manifest(package_dir / "manifest.json")
        self._validate_package_dir(package_dir, manifest)

        game_root = _installed_root() / manifest.game_id
        target_dir = game_root / manifest.version
        staging_root = _game_cache_root() / "_staging"
        _ensure_dir(staging_root)
        staging_dir = staging_root / f"{manifest.game_id}-{_safe_file_name(manifest.version)}"
        _remove_path(staging_dir)
        shutil.copytree(package_dir, staging_dir)
        self._validate_package_dir(staging_dir, manifest)

        if game_root.exists():
            _remove_path(game_root)
        _ensure_dir(game_root)
        shutil.move(str(staging_dir), str(target_dir))

        data_root = _game_data_root() / manifest.game_id
        cache_root = _game_cache_root() / manifest.game_id
        _ensure_dir(data_root)
        _ensure_dir(cache_root)
        self._write_registry_entry(manifest, target_dir, source)
        self.refresh()
        installed = self.get_installed_game(manifest.game_id)
        if installed is None:
            raise GamePackageError(f"安装 {manifest.game_id} 后未能重新加载")
        return installed

    def _inspect_zip_package(self, zip_path: Path) -> tuple[GamePackageManifest, str]:
        with tempfile.TemporaryDirectory(prefix="gamepkg_inspect_", dir=str(_game_cache_root())) as temp_dir:
            temp_root = Path(temp_dir) / "extract"
            _ensure_dir(temp_root)
            self._extract_zip(zip_path, temp_root)
            package_root = _choose_package_root(temp_root)
            manifest = self._load_manifest(package_root / "manifest.json")
            self._validate_package_dir(package_root, manifest)
            signature = self._package_signature(package_root)
        return manifest, signature

    def uninstall_game(self, game_id: str) -> None:
        game_id = str(game_id).strip()
        installed = self.get_installed_game(game_id)
        if installed is None:
            raise GamePackageError(f"未安装游戏：{game_id}")
        self._unregister_game_extensions(game_id)
        _remove_path(_installed_root() / game_id)
        registry = self._load_registry()
        if game_id in registry:
            registry.pop(game_id, None)
            self._save_registry(registry)
        self.refresh()

    def export_game_zip(self, game_id: str, output_path: Path) -> Path:
        installed = self.get_installed_game(game_id)
        if installed is None:
            raise GamePackageError(f"未安装游戏：{game_id}")
        output_path = output_path.resolve()
        _ensure_dir(output_path.parent)
        if output_path.exists():
            output_path.unlink()
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in self._iter_package_files(installed.install_dir):
                archive.write(path, path.relative_to(installed.install_dir).as_posix())
        return output_path

    def scan_inbox(self) -> list[str]:
        messages: list[str] = []
        for zip_path in sorted(_game_inbox_root().glob("*.zip"), key=lambda p: p.name.lower()):
            try:
                installed = self.install_from_zip(zip_path, source="inbox")
            except Exception as exc:
                target = self._unique_destination(_game_rejected_root(), zip_path.name)
                shutil.move(str(zip_path), str(target))
                messages.append(f"{zip_path.name}: 安装失败，已移至 rejected（{exc}）")
                continue
            target = self._unique_destination(_game_archive_root(), zip_path.name)
            shutil.move(str(zip_path), str(target))
            messages.append(f"{zip_path.name}: 已安装 {installed.manifest.name} {installed.manifest.version}")
        if not messages:
            messages.append("收件箱中没有待安装 ZIP")
        return messages

    def load_game_entry(self, game_id: str) -> tuple[InstalledGame, GameContext, object]:
        installed = self.get_installed_game(game_id)
        if installed is None:
            raise GamePackageError(f"未安装游戏：{game_id}")
        context = GameContext(
            manifest=installed.manifest,
            install_dir=installed.install_dir,
            data_root=installed.data_root,
            cache_root=installed.cache_root,
        )
        _ensure_dir(context.data_root)
        _ensure_dir(context.cache_root)

        top_package = installed.manifest.entry_module.split(".", 1)[0]
        for module_name in [name for name in list(sys.modules) if name == top_package or name.startswith(f"{top_package}.")]:
            sys.modules.pop(module_name, None)

        code_root = str(installed.code_root)
        sys.path.insert(0, code_root)
        try:
            importlib.invalidate_caches()
            module = importlib.import_module(installed.manifest.entry_module)
        finally:
            try:
                sys.path.remove(code_root)
            except ValueError:
                pass

        entry_class = getattr(module, installed.manifest.entry_class, None)
        if entry_class is None:
            raise GamePackageError(
                f"{installed.manifest.game_id} 缺少入口类 {installed.manifest.entry_class}"
            )
        entry = entry_class(context)
        return installed, context, entry

    def _load_manifest(self, path: Path) -> GamePackageManifest:
        if not path.exists():
            raise GamePackageError(f"缺少 manifest.json：{path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GamePackageError(f"manifest.json 解析失败：{exc}") from exc
        if not isinstance(data, dict):
            raise GamePackageError("manifest.json 根对象必须是 JSON object")
        manifest = GamePackageManifest.from_dict(data)
        self._validate_manifest(manifest)
        return manifest

    def _validate_manifest(self, manifest: GamePackageManifest) -> None:
        if manifest.package_type != "game":
            raise GamePackageError(f"不支持的 package_type：{manifest.package_type}")
        if manifest.package_format_version != _PACKAGE_FORMAT_VERSION:
            raise GamePackageError(
                f"包格式版本不兼容：{manifest.package_format_version}，当前支持 {_PACKAGE_FORMAT_VERSION}"
            )
        if manifest.runtime_api_version != _RUNTIME_API_VERSION:
            raise GamePackageError(
                f"运行时 API 版本不兼容：{manifest.runtime_api_version}，当前支持 {_RUNTIME_API_VERSION}"
            )
        if not _GAME_ID_RE.fullmatch(manifest.game_id):
            raise GamePackageError(f"非法 game_id：{manifest.game_id}")
        if not manifest.name:
            raise GamePackageError("manifest.name 不能为空")
        if not manifest.version:
            raise GamePackageError("manifest.version 不能为空")
        if not manifest.summary:
            raise GamePackageError("manifest.summary 不能为空")
        if not _MODULE_RE.fullmatch(manifest.entry_module):
            raise GamePackageError(f"非法 entry_module：{manifest.entry_module}")
        if not _CLASS_RE.fullmatch(manifest.entry_class):
            raise GamePackageError(f"非法 entry_class：{manifest.entry_class}")
        for spec in (*manifest.particle_extensions, *manifest.effect_extensions):
            if not _CLASS_RE.fullmatch(spec.class_name):
                raise GamePackageError(f"非法扩展类名：{spec.class_name}")
            if not spec.local_id:
                raise GamePackageError("扩展 local_id 不能为空")

    def _validate_package_dir(self, package_dir: Path, manifest: GamePackageManifest) -> None:
        code_root = package_dir / "code"
        if not code_root.exists():
            raise GamePackageError("包内缺少 code 目录")
        entry_rel = Path(*manifest.entry_module.split("."))
        entry_py = code_root / f"{entry_rel}.py"
        entry_pkg = code_root / entry_rel / "__init__.py"
        if not entry_py.exists() and not entry_pkg.exists():
            raise GamePackageError(f"未找到入口模块文件：{manifest.entry_module}")
        for spec in (*manifest.particle_extensions, *manifest.effect_extensions):
            ext_path = package_dir / Path(spec.module)
            if not ext_path.exists():
                raise GamePackageError(f"缺少扩展模块文件：{spec.module}")

    def _extract_zip(self, zip_path: Path, target_dir: Path) -> None:
        try:
            with zipfile.ZipFile(zip_path, "r") as archive:
                for info in archive.infolist():
                    rel = Path(str(info.filename).replace("\\", "/"))
                    if not rel.parts:
                        continue
                    if rel.is_absolute() or ".." in rel.parts:
                        raise GamePackageError(f"ZIP 中存在越界路径：{info.filename}")
                    dest = target_dir.joinpath(*rel.parts)
                    if info.is_dir():
                        _ensure_dir(dest)
                        continue
                    _ensure_dir(dest.parent)
                    with archive.open(info, "r") as src, dest.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
        except zipfile.BadZipFile as exc:
            raise GamePackageError(f"ZIP 文件损坏：{zip_path}") from exc

    def _iter_package_files(self, root: Path):
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if "__pycache__" in rel.parts or path.suffix.lower() in {".pyc", ".pyo"}:
                continue
            yield path

    def _package_signature(self, root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(self._iter_package_files(root), key=lambda item: item.relative_to(root).as_posix()):
            rel = path.relative_to(root).as_posix()
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(64 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
            digest.update(b"\0")
        return digest.hexdigest()

    def _load_registry(self) -> dict[str, dict[str, Any]]:
        path = _registry_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        games = data.get("games") if isinstance(data, dict) else None
        return dict(games) if isinstance(games, dict) else {}

    def _save_registry(self, games: dict[str, dict[str, Any]]) -> None:
        path = _registry_path()
        _ensure_dir(path.parent)
        payload = {"games": games}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_registry_entry(self, manifest: GamePackageManifest, install_dir: Path, source: str) -> None:
        registry = self._load_registry()
        registry[manifest.game_id] = {
            "game_id": manifest.game_id,
            "name": manifest.name,
            "version": manifest.version,
            "install_dir": str(install_dir),
            "source": source,
            "official": bool(manifest.official),
        }
        self._save_registry(registry)

    def _sync_registry(self) -> None:
        registry: dict[str, dict[str, Any]] = {}
        for record in self.list_installed_games():
            registry[record.game_id] = {
                "game_id": record.game_id,
                "name": record.manifest.name,
                "version": record.manifest.version,
                "install_dir": str(record.install_dir),
                "source": record.source,
                "official": bool(record.manifest.official),
            }
        self._save_registry(registry)

    def _reload_extensions(self) -> None:
        active_ids = set(self._installed.keys())
        for game_id in list(self._registered_extensions.keys()):
            if game_id not in active_ids:
                self._unregister_game_extensions(game_id)
        for record in self.list_installed_games():
            self._register_game_extensions(record)

    def _register_game_extensions(self, record: InstalledGame) -> None:
        from lib.script.effects.manager import get_effect_script_manager
        from lib.script.practical.manager import get_particle_script_manager

        self._unregister_game_extensions(record.game_id)
        registered_particles: list[str] = []
        registered_effects: list[str] = []
        particle_manager = get_particle_script_manager()
        effect_manager = get_effect_script_manager()

        for spec in record.manifest.particle_extensions:
            particle_id = qualify_game_extension_id(record.game_id, spec.local_id)
            cls = self._load_extension_class(
                record.install_dir / spec.module,
                spec.class_name,
                game_id=record.game_id,
                local_id=spec.local_id,
                kind="particle",
            )
            particle_manager._instances.pop(particle_id, None)
            particle_manager.register_script(cls)
            registered_particles.append(particle_id)

        for spec in record.manifest.effect_extensions:
            effect_id = qualify_game_extension_id(record.game_id, spec.local_id)
            cls = self._load_extension_class(
                record.install_dir / spec.module,
                spec.class_name,
                game_id=record.game_id,
                local_id=spec.local_id,
                kind="effect",
            )
            effect_manager._instances.pop(effect_id, None)
            effect_manager.register_script(cls)
            registered_effects.append(effect_id)

        self._registered_extensions[record.game_id] = {
            "particles": tuple(registered_particles),
            "effects": tuple(registered_effects),
        }

    def _unregister_game_extensions(self, game_id: str) -> None:
        from lib.script.effects.manager import get_effect_script_manager
        from lib.script.practical.manager import get_particle_script_manager

        registered = self._registered_extensions.pop(str(game_id).strip(), None)
        if not registered:
            return
        particle_manager = get_particle_script_manager()
        for particle_id in registered.get("particles", ()):
            particle_manager._scripts.pop(particle_id, None)
            particle_manager._instances.pop(particle_id, None)
            try:
                particle_registry.unregister(particle_id)
            except Exception:
                pass
        effect_manager = get_effect_script_manager()
        for effect_id in registered.get("effects", ()):
            effect_manager._scripts.pop(effect_id, None)
            effect_manager._instances.pop(effect_id, None)
            try:
                effect_registry.unregister(effect_id)
            except Exception:
                pass

    def _load_extension_class(self, module_path: Path, class_name: str, *, game_id: str, local_id: str, kind: str):
        module_name = f"gamepkg_ext_{_safe_file_name(game_id)}_{_safe_file_name(local_id)}"
        spec = importlib.util.spec_from_file_location(module_name, str(module_path))
        if spec is None or spec.loader is None:
            raise GamePackageError(f"无法加载扩展模块：{module_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        script_class = getattr(module, class_name, None)
        if script_class is None:
            raise GamePackageError(f"扩展 {module_path.name} 缺少类 {class_name}")
        qualified_id = qualify_game_extension_id(game_id, local_id)
        id_attr = "PARTICLE_ID" if str(kind).strip().lower() == "particle" else "EFFECT_ID"
        return _bind_extension_class(script_class, id_attr=id_attr, qualified_id=qualified_id)

    def _unique_destination(self, root: Path, name: str) -> Path:
        candidate = root / _safe_file_name(name)
        if not candidate.exists():
            return candidate
        stem = candidate.stem
        suffix = candidate.suffix
        index = 2
        while True:
            probe = root / f"{stem}-{index}{suffix}"
            if not probe.exists():
                return probe
            index += 1


_service: GamePackageService | None = None


def get_game_package_service() -> GamePackageService:
    global _service
    if _service is None:
        _service = GamePackageService()
    return _service


def cleanup_game_package_service() -> None:
    global _service
    if _service is not None:
        for game_id in list(_service._registered_extensions.keys()):
            _service._unregister_game_extensions(game_id)
        _service = None
