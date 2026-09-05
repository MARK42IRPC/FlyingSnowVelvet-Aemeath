# -*- coding: utf-8 -*-
# ruff: noqa: F401,F403,F405
"""Dependency installer orchestration and compatibility facade."""

import os
import functools
import inspect
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

from lib.core import dsh_runtime_contract as dsh_config
from lib.core import voice_runtime_contract as directml_config

from . import dsh_runtime as _dsh_runtime_installer
from . import bootstrap, catalog, console, dependencies, progress, resources, voice_runtime
from .bootstrap import *
from .catalog import *
from .console import *
from .dependencies import *
from .progress import *
from .resources import *
from .voice_runtime import *


_COMPAT_MODULES = (
    bootstrap,
    catalog,
    console,
    dependencies,
    progress,
    resources,
    voice_runtime,
)
_COMPAT_NAMES = {
    name
    for module in _COMPAT_MODULES
    for name in getattr(module, "__all__", ())
}
_COMPAT_NAMES.update(name for name in vars(catalog) if name.isupper())
_COMPAT_DEFAULTS = {
    name: globals()[name]
    for name in _COMPAT_NAMES
    if name in globals()
}
_COMPAT_WRAPPERS: dict[str, object] = {}


def _sync_compat_exports() -> None:
    """Forward legacy facade patches to the module that owns each helper."""
    for name, default in _COMPAT_DEFAULTS.items():
        current = globals().get(name, default)
        resolved = default if current is _COMPAT_WRAPPERS.get(name) else current
        for module in _COMPAT_MODULES:
            if hasattr(module, name):
                setattr(module, name, resolved)


def _compat_function(name: str, function):
    @functools.wraps(function)
    def forwarded(*args, **kwargs):
        _sync_compat_exports()
        return function(*args, **kwargs)

    _COMPAT_WRAPPERS[name] = forwarded
    return forwarded


def _compat_class(name: str, class_type):
    @functools.wraps(class_type)
    def construct(*args, **kwargs):
        _sync_compat_exports()
        return class_type(*args, **kwargs)

    _COMPAT_WRAPPERS[name] = construct
    return construct


for _compat_name, _compat_value in _COMPAT_DEFAULTS.items():
    if inspect.isfunction(_compat_value):
        globals()[_compat_name] = _compat_function(_compat_name, _compat_value)
    elif inspect.isclass(_compat_value):
        globals()[_compat_name] = _compat_class(_compat_name, _compat_value)


def _node_version_from_result(result) -> str:
    if result is None or result.returncode != 0:
        return ""
    lines = [line.strip() for line in str(result.stdout or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""

def _node_tree_ready(root: Path) -> tuple[bool, str]:
    """Validate the generated Node tree without invoking npm from the app."""
    root = Path(root)
    node = root / "node.exe"
    npm_cli = root / "node_modules" / "npm" / "bin" / "npm-cli.js"
    if not node.is_file() or not npm_cli.is_file():
        return False, "Node/npm 文件不完整"

    node_result = _run([str(node), "--version"], timeout=30)
    if _node_version_from_result(node_result) != dsh_config.NODE_VERSION_TEXT:
        return False, f"Node 版本不匹配（需要 {dsh_config.NODE_VERSION_TEXT}）"

    npm_result = _run([str(node), str(npm_cli), "--version"], timeout=30)
    if _node_version_from_result(npm_result) != dsh_config.NPM_VERSION:
        return False, f"npm 版本不匹配（需要 {dsh_config.NPM_VERSION}）"
    return True, ""

def _dsh_runtime_ready() -> tuple[bool, str]:
    """Return whether the installed Node and locked DSH tree are usable."""
    source_error = dsh_config.runtime_source_error(PROJECT_ROOT)
    if source_error:
        return False, source_error
    node_root = dsh_config.node_root(PROJECT_ROOT)
    ready, detail = _node_tree_ready(node_root)
    if not ready:
        return False, detail
    installed_error = dsh_config.installed_runtime_error(PROJECT_ROOT)
    return (False, installed_error) if installed_error else (True, "")

def _dsh_node_urls() -> tuple[str, ...]:
    """Use the resource manifest first, then keep official URLs as a fallback."""
    manifest_urls = ()
    try:
        manifest_urls = tuple(_resource_urls(dsh_config.NODE_ARCHIVE_NAME))
    except Exception:
        manifest_urls = ()
    urls = tuple(dict.fromkeys((*manifest_urls, *dsh_config.NODE_DOWNLOAD_URLS)))
    return _order_node_urls(urls)

def _run_dsh_npm_ci() -> tuple[bool, str]:
    return _dsh_runtime_installer.run_npm_ci(sys.modules[__name__])

def ensure_dsh_office_runtime() -> bool:
    return _dsh_runtime_installer.ensure_runtime(sys.modules[__name__])

def _should_install_dsh() -> bool:
    return _dsh_runtime_installer.should_install()

def launch(python_exe):
    """Launch main script, prefer pythonw if available."""
    _print_stage(10, "启动飞行雪绒桌宠...")

    main_script = PROJECT_ROOT / "lib" / "core" / "qt_desktop_pet.py"
    if not main_script.exists():
        print(f"  main script not found: {main_script}")
        return False

    launcher = _resolve_pythonw_path(python_exe, fallback=python_exe)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)

    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        subprocess.Popen(
            [launcher, str(main_script)],
            cwd=str(PROJECT_ROOT),
            env=env,
            creationflags=create_no_window,
        )
        print("  launched in background")
        return True
    except Exception as e:
        print(f"  launch failed: {e}")
        return False

def main():
    print("=" * 56)
    print(" Flying Snow Velvet LTS - Install and Launch")
    print("=" * 56)
    print()

    try:
        python_exe, pip_ok = select_best_python()

        if not pip_ok:
            if not ensure_pip(python_exe):
                input(_fmt_color("\n[错误] 无法自动安装 pip，按回车退出...", "error"))
                sys.exit(1)

        save_config(python_exe)

        mirrors = benchmark_mirrors()

        if not install_all(python_exe, mirrors):
            _print_warn("依赖未全部安装，可能影响部分功能")

        if _should_install_dsh():
            if not ensure_dsh_office_runtime():
                _print_warn("DSH 办公运行时未准备完成，办公模式将提示重新运行安装依赖")
        else:
            print("\n已跳过 DSH 办公运行时安装；办公模式需改用已接入的其它后端。", flush=True)

        _print_stage(5, "准备 ONNX 语音通用 GPU 运行时...")
        if not ensure_directml_hybrid_runtime(python_exe, mirrors):
            _print_warn("DirectML 混合推理环境未准备完成，ONNX 语音将使用 CPU")

        if _microphone_runtime_ready(python_exe):
            if not ensure_vosk_models():
                _print_warn("部分 Vosk 模型缺失，语音识别可能无法正常工作")
        else:
            _print_stage(6, "跳过 Vosk 模型下载（sounddevice/vosk 未就绪）")

        if not ensure_seanima_assets():
            _print_warn("启动/退出动画资源未准备完成，将按程序兼容逻辑继续启动")

        if os.environ.get("FLYING_SNOW_INSTALLER") == "1":
            print("\n安装器模式：依赖与资源准备完成，交由安装器启动桌宠。", flush=True)
            return
        if launch(python_exe):
            print("\nLauncher will close in 3 seconds...")
            time.sleep(3)
        else:
            input("\nPress Enter to exit...")
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n[cancelled] interrupted by user")
    except Exception as e:
        _print_error(f"\n发生未预期异常: {e}")
        import traceback

        traceback.print_exc()
        input("Press Enter to exit...")
        sys.exit(1)
