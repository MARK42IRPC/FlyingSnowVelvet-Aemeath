"""桌面宠物主程序 (PyQt5版)"""
import os
import sys
import traceback


def _show_startup_error(message: str) -> None:
    """输出启动错误，并在 Windows 下弹窗提示。"""
    try:
        print(message, file=sys.stderr)
    except Exception:
        pass

    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, "飞行雪绒 启动失败", 0x10)
    except Exception:
        pass


def _build_missing_dependency_message(missing_module: str, install_bat: str) -> str:
    return (
        f"缺少 Python 依赖模块：{missing_module}\n\n"
        f"请先运行：{install_bat}\n"
        "然后重新启动程序。\n\n"
        f"也可手动执行：python -m pip install {missing_module}"
    )


def _preload_optional_onnx_runtime() -> None:
    """Load ONNX native DLLs before Qt can bind conflicting dependencies."""
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        pass


# 添加项目根目录到 Python 路径（向上三级，从 lib/core 到根目录）
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == '--fsv-update-helper':
        try:
            import json
            from lib.script.app.update_installer import run_update_installer

            payload = json.loads(sys.argv[2])
            if not isinstance(payload, dict):
                raise ValueError('invalid update payload')
            sys.exit(run_update_installer(payload))
        except Exception as exc:
            _show_startup_error('更新辅助进程启动失败：\n\n' + str(exc))
            sys.exit(1)
    if len(sys.argv) >= 4 and sys.argv[1] == '--fsv-restart-helper':
        try:
            import json
            from lib.script.app.restart import run_restart_helper

            parent_pid = int(sys.argv[2])
            command = json.loads(sys.argv[3])
            if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
                raise ValueError('invalid restart command')
            sys.exit(run_restart_helper(parent_pid, command))
        except Exception as exc:
            _show_startup_error('重启辅助进程启动失败：\n\n' + str(exc))
            sys.exit(1)
    try:
        _preload_optional_onnx_runtime()
        from lib.script.app.qt_backend_bootstrap import configure_selected_desktop_backend

        backend_selection = configure_selected_desktop_backend()
        from lib.core.desktop_backend import get_desktop_backend_bundle
        from lib.script.main import main
    except ModuleNotFoundError as e:
        missing = getattr(e, "name", None) or "unknown"
        install_bat = os.path.join(project_root, "安装依赖.bat")
        _show_startup_error(_build_missing_dependency_message(missing, install_bat))
        sys.exit(1)
    except Exception:
        _show_startup_error("程序启动失败：\n\n" + traceback.format_exc())
        sys.exit(1)

    main(
        backend_selection=backend_selection,
        backend_bundle=get_desktop_backend_bundle(),
    )
