"""用包内解释器跑一次 payload 的真实启动与功能自检。

这个脚本取代了打包期逐文件 SHA-256：与其把上千兆文件重新读一遍算哈希，不如让发行包
自己启动一次（``payload_selftest.py --mode startup``）并真实构造工作台、办公窗口、论坛、
粒子与各项服务（``--mode features``）。跑完后由调用方（``build_offline_installer``）继续
打包，包体是否可复现再交给「打两次包比哈希」去证明。

环境与 ``installer/windows/src/launcher.c`` 的 ``configure_environment`` 对齐：清掉外部
PYTHONHOME / PYTHONPATH / NODE_PATH / Qt 覆盖，PATH 只用包内 Python、包内 Qt bin 与系统
目录，用户根指向临时目录，因此不会读写用户真正的 ``%SystemDrive%\\AemeathDeskPet``。
Qt 插件路径显式指向包内 PyQt5 自带的 ``Qt5/plugins``：桌宠运行时会自己设
（``lib/core/qt_bridge/application_runtime.py`` 的 ``_ensure_qt_plugin_paths``），离屏自检
没有那一步，缺了它连 ``qoffscreen.dll`` 都找不到。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


CLEARED_ENVIRONMENT_VARIABLES = (
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "NODE_PATH",
    "QT_PLUGIN_PATH",
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    "QML2_IMPORT_PATH",
    "OPENSSL_CONF",
    "PLAYWRIGHT_BROWSERS_PATH",
)
APP_ROOT_ENVIRONMENT_VARIABLE = "FSV_APP_ROOT"


def payload_paths(workspace: Path) -> tuple[Path, Path, Path]:
    """返回 ``(payload, app_root, python_root)``，缺一个就报错。

    路径一律取绝对形式：``QT_QPA_PLATFORM_PLUGIN_PATH`` 这类要传给子进程的值必须是绝对的，
    子进程的 cwd 是 payload 的 app 根，相对路径会指向别处。
    """
    payload = (Path(workspace) / "payload").resolve()
    app_root = payload / "app"
    python_root = payload / "runtime" / "python311"
    for required in (app_root, python_root):
        if not required.is_dir():
            raise SystemExit(f"payload 不完整，缺少：{required}")
    return payload, app_root, python_root


def _selftest_environment(
    app_root: Path, python_root: Path, *, home: Path, visible: bool
) -> dict[str, str]:
    environment = dict(os.environ)
    for name in CLEARED_ENVIRONMENT_VARIABLES:
        environment.pop(name, None)
    qt_root = python_root / "Lib" / "site-packages" / "PyQt5" / "Qt5"
    qt_bin = qt_root / "bin"
    qt_plugins = qt_root / "plugins"
    node = app_root / "resc" / "node-24.13.0-win-x64" / "node.exe"
    for name, value in (
        (APP_ROOT_ENVIRONMENT_VARIABLE, str(app_root)),
        ("QT_PLUGIN_PATH", str(qt_plugins)),
        ("QT_QPA_PLATFORM_PLUGIN_PATH", str(qt_plugins / "platforms")),
        ("FSV_OFFLINE_DISTRIBUTION", "1"),
        ("PYTHONNOUSERSITE", "1"),
        ("PYTHONUTF8", "1"),
        ("PYTHONIOENCODING", "utf-8"),
        ("NODE_ENV", "production"),
        ("PLAYWRIGHT_NODEJS_PATH", str(node)),
        ("AEMEATH_DESK_PET_HOME", str(home)),
        ("QT_QPA_PLATFORM", "windows" if visible else "offscreen"),
    ):
        environment[name] = value
    environment["PATH"] = os.pathsep.join(
        [
            str(python_root),
            str(python_root / "DLLs"),
            str(qt_bin),
            str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"),
            str(Path(os.environ.get("SystemRoot", r"C:\Windows"))),
        ]
    )
    return environment


def _run_stage(
    *,
    python: Path,
    driver: Path,
    mode: str,
    app_root: Path,
    report: Path,
    environment: dict[str, str],
    ready_timeout: float,
    settle: float,
    timeout: float,
) -> tuple[dict, str]:
    # 子进程的 cwd 就是 payload 的 app 根，这里一律传绝对路径。
    command = [
        str(Path(python).resolve()),
        "-I",
        str(Path(driver).resolve()),
        "--mode",
        mode,
        "--app-root",
        str(Path(app_root).resolve()),
        "--report",
        str(Path(report).resolve()),
        "--ready-timeout",
        str(ready_timeout),
        "--settle",
        str(settle),
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=str(app_root),
            env=environment,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise SystemExit(f"payload 自检（{mode}）超过 {timeout:.0f} 秒仍未结束")
    output = (completed.stdout or b"").decode("utf-8", "replace") + (
        completed.stderr or b""
    ).decode("utf-8", "replace")
    if not report.is_file():
        raise SystemExit(
            f"payload 自检（{mode}）没有写出报告，退出码 {completed.returncode}：\n{output[-4000:]}"
        )
    data = json.loads(report.read_text(encoding="utf-8"))
    data["exit_code"] = int(completed.returncode)
    data["seconds"] = round(time.monotonic() - started, 3)
    return data, output


def verify_payload_runtime(
    workspace: Path,
    *,
    visible: bool = False,
    ready_timeout: float = 180.0,
    settle: float = 3.0,
    timeout: float = 420.0,
    home: Path | None = None,
    log=None,
) -> dict:
    """对工作区里的 payload 跑一次真实启动与功能自检；失败直接抛 ``SystemExit``。"""
    report_lines = log if callable(log) else (lambda message: print(message, flush=True))
    payload, app_root, python_root = payload_paths(Path(workspace))
    python = python_root / "python.exe"
    if not python.is_file():
        raise SystemExit(f"payload 里没有解释器：{python}")
    driver = Path(__file__).resolve().parent / "payload_selftest.py"
    if not driver.is_file():
        raise SystemExit(f"缺少自检脚本：{driver}")
    temporary_home = None
    if home is None:
        temporary_home = Path(tempfile.mkdtemp(prefix="fsv-selftest-home-"))
        home = temporary_home
    report_lines(f"payload 自检：{app_root}")
    reports: dict[str, dict] = {}
    try:
        for mode in ("startup", "features"):
            report = Path(home).parent / f".fsv-selftest-{mode}.json"
            environment = _selftest_environment(
                app_root, python_root, home=Path(home), visible=visible
            )
            data, output = _run_stage(
                python=python,
                driver=driver,
                mode=mode,
                app_root=app_root,
                report=report,
                environment=environment,
                ready_timeout=ready_timeout,
                settle=settle,
                timeout=timeout,
            )
            report.unlink(missing_ok=True)
            reports[mode] = data
            for line in output.strip().splitlines()[-25:]:
                report_lines(f"  {line}")
            failed = [item for item in data.get("checks", []) if not item.get("ok")]
            if not data.get("ok") or failed or int(data.get("exit_code", 1)) != 0:
                detail = "; ".join(
                    f"{item['name']}: {item.get('detail', '')}" for item in failed
                )
                raise SystemExit(
                    f"payload 自检失败（{mode}，退出码 {data.get('exit_code')}）"
                    + (f"：{detail}" if detail else "")
                )
            report_lines(
                f"  自检 {mode} 通过（{data.get('seconds')}s，"
                f"{len(data.get('checks', []))} 项）"
            )
    finally:
        if temporary_home is not None:
            shutil.rmtree(temporary_home, ignore_errors=True)
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--visible", action="store_true", help="显示真实窗口，默认离屏")
    parser.add_argument("--ready-timeout", type=float, default=180.0)
    parser.add_argument("--settle", type=float, default=3.0)
    parser.add_argument("--timeout", type=float, default=420.0)
    parser.add_argument("--home", type=Path, help="用户根目录，默认用临时目录")
    args = parser.parse_args(argv)
    reports = verify_payload_runtime(
        args.workspace,
        visible=args.visible,
        ready_timeout=args.ready_timeout,
        settle=args.settle,
        timeout=args.timeout,
        home=args.home,
    )
    for mode, data in reports.items():
        print(
            f"{mode}: ok={data.get('ok')} exit={data.get('exit_code')} "
            f"seconds={data.get('seconds')}"
        )
    print("payload 真实启动与功能自检通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
