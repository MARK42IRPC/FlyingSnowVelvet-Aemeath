"""打包前对 payload 跑一次真实启动与功能自检。

``scripts/verify_payload_runtime.py`` 用包内解释器（``runtime/python311/python.exe``）
启动本脚本，所以验证的是**发行包**本身：包内 Python、包内 wheel、包内 DLL、包内 Node 与
资源树。``scripts/`` 不进 payload（见 ``build_offline_installer._archive_entries``），因此
这里可以按模块名引用仓库源码，跑起来的却是包内依赖。

两个阶段各占一个进程，避免销毁 QApplication 之后再建一个：

- ``--mode startup``：与 ``启动飞行雪绒.exe`` 同一条命令、同一套环境变量，走
  ``ApplicationState`` 的完整启动路径，等 ``APP_INIT_READY`` 之后让主循环有序退出。
- ``--mode features``：在离屏 Qt 里构造工作台全部页面、独立办公窗口、论坛窗口与粒子，
  并逐个探测服务、资源与用户存储。

结果写成 JSON（``--report``）；任何一项失败时进程退出码非 0。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path


def _prepare_app_root(app_root: Path) -> None:
    os.chdir(app_root)
    if str(app_root) not in sys.path:
        sys.path.insert(0, str(app_root))


def _record(report: dict, name: str, ok: bool, detail: str, seconds: float) -> None:
    report["checks"].append(
        {
            "name": name,
            "ok": bool(ok),
            "detail": str(detail)[:400],
            "seconds": round(float(seconds), 3),
        }
    )
    marker = "通过" if ok else "失败"
    print(f"[自检] {name}：{marker} — {detail}", flush=True)


def _run_check(report: dict, name: str, function) -> None:
    started = time.monotonic()
    try:
        detail = function()
        _record(report, name, True, detail if detail else "ok", time.monotonic() - started)
    except Exception as exc:  # noqa: BLE001 - 自检要把失败原样带回报告
        detail = f"{type(exc).__name__}: {exc}"
        _record(report, name, False, detail, time.monotonic() - started)
        report["checks"][-1]["traceback"] = traceback.format_exc()[-2000:]


def _run_startup(args) -> dict:
    """真实启动：完整走一遍桌宠的启动路径，就绪后自己退出。"""
    report = {"mode": "startup", "checks": [], "ok": False}
    from PyQt5.QtCore import QTimer

    import lib.script.main as app_main
    from lib.core.event.center import EventType, get_event_center
    from lib.core.qt_desktop_pet import (
        _apply_pending_update_overlay,
        _preload_optional_onnx_runtime,
    )
    from lib.script.app.qt_backend_bootstrap import configure_selected_desktop_backend

    # 构建机上不该留下一个指向临时 payload 的桌面快捷方式。
    app_main._new_ensure_desktop_shortcut = lambda *_args, **_kwargs: None
    # 与 lib/core/qt_desktop_pet.py 的 __main__ 完全同一条路：先补装上次的待替换
    # 文件、预载可选推理运行时，再挑后端、建 ApplicationState。单实例锁不走：
    # 构建机上可能正开着桌宠，那会让自检直接退出。
    _apply_pending_update_overlay()
    _preload_optional_onnx_runtime()
    backend_selection = configure_selected_desktop_backend()
    from lib.core.desktop_backend import get_desktop_backend_bundle

    center = get_event_center()
    ready_at: list[float] = []

    def _on_ready(_event=None) -> None:
        ready_at.append(time.monotonic())

    center.subscribe(EventType.APP_INIT_READY, _on_ready)
    started = time.monotonic()
    deadline = started + float(args.ready_timeout)
    application = app_main.ApplicationState(
        backend_selection=backend_selection,
        backend_bundle=get_desktop_backend_bundle(),
    )
    application.start()
    timer = QTimer()
    timer.setInterval(200)

    def _tick() -> None:
        now = time.monotonic()
        if ready_at and now - ready_at[0] >= float(args.settle):
            timer.stop()
            application.request_exit(0)
        elif now >= deadline:
            timer.stop()
            report["timed_out"] = True
            application.request_exit(4)

    timer.timeout.connect(_tick)
    timer.start()
    exit_code = application.run_event_loop()
    exit_code = application.finalize_after_event_loop(exit_code)
    center.unsubscribe(EventType.APP_INIT_READY, _on_ready)
    elapsed = time.monotonic() - started
    ready = bool(ready_at)
    report.update(
        {
            "ready": ready,
            "ready_seconds": round(ready_at[0] - started, 3) if ready else None,
            "exit_code": int(exit_code),
            "seconds": round(elapsed, 3),
        }
    )
    if not ready:
        _record(report, "startup_ready", False, "等不到 APP_INIT_READY", elapsed)
    elif int(exit_code) != 0:
        _record(report, "startup_exit", False, f"退出码 {exit_code}", elapsed)
    else:
        _record(
            report,
            "startup_ready",
            True,
            f"就绪 {report['ready_seconds']}s，退出码 0，共 {report['seconds']}s",
            elapsed,
        )
    report["ok"] = all(item["ok"] for item in report["checks"])
    return report


def _feature_workbench_pages() -> str:
    from lib.script.ui.ai_settings_panel import AISettingsPanel
    from lib.script.ui.workbench_window import WorkbenchWindow
    from lib.script.workbench.builtin_pages import builtin_tool_page_specs

    panel = AISettingsPanel(lazy_workbench_pages=True)
    window = WorkbenchWindow(lambda: panel, extra_page_specs=list(builtin_tool_page_specs()))
    ids = [page_id for page_id, _title in panel.get_workbench_page_specs()]
    ids.extend(spec.page_id for spec in builtin_tool_page_specs())
    built = 0
    for page_id in dict.fromkeys(ids):
        window.show_page(page_id)
        built += 1
    window.hide()
    return f"{built} 个工作台页面全部构造成功"


def _feature_office_window() -> str:
    from lib.script.ui.office_page import OfficeWorkbenchPage

    window = OfficeWorkbenchPage()
    size = window.size()
    window.close()
    return f"独立办公窗口 {size.width()}x{size.height()}"


def _feature_forum_window() -> str:
    from lib.script.ui.forum_window import ForumWindow

    window = ForumWindow()
    window.close()
    return "雪绒论坛窗口构造成功"


def _feature_particles() -> str:
    from lib.script.practical.star_streak_particle import (
        StarStreakParticle,
        StarStreakParticleScript,
    )

    script = StarStreakParticleScript()
    config = script.request_config()
    particles = [StarStreakParticle(100.0, 100.0, config) for _ in range(3)]
    blooms = [round(float(item.bloom), 2) for item in particles]
    return f"星空粒子 {len(particles)} 颗，bloom={blooms}"


def _feature_services() -> str:
    from lib.core.voice.core import get_voice_core  # noqa: F401
    from lib.script.music import service as music_service
    from lib.script.music.service import configure_music_player_factory  # noqa: F401

    assert callable(getattr(music_service, "configure_music_player_factory", None))
    return "语音核心与音乐服务模块可导入"


def _feature_assets() -> str:
    from lib.core.qt_bridge import font as font_module

    app_root = Path(os.environ["FSV_APP_ROOT"])
    fonts = sorted((app_root / "resc" / "FRONTS").glob("*.ttf"))
    assert fonts, "包内没有 UI 字体"
    font_module.init_font_config()
    family = font_module.get_ui_font_family()
    assert family, "UI 字体没有装载"
    frames = sorted((app_root / "resc" / "GIF" / "SEanima").rglob("*.webp"))
    assert frames, "包内没有退出动画序列帧"
    models = sorted((app_root / "resc" / "models").glob("vosk-model-*"))
    assert models, "包内没有 Vosk 模型目录"
    node = app_root / "resc" / "node-24.13.0-win-x64" / "node.exe"
    assert node.is_file(), "包内没有 Node 运行时"
    return f"字体 {len(fonts)} 个（{family}）、序列帧 {len(frames)} 张、Vosk 模型 {len(models)} 个"


def _feature_storage() -> str:
    from config.shared_storage_io import write_bytes_atomic
    from config.user_storage_paths import get_user_state_dir

    target = get_user_state_dir("selftest.json")
    write_bytes_atomic(target, json.dumps({"ok": True}).encode("utf-8"))
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload.get("ok") is True
    target.unlink(missing_ok=True)
    return f"用户存储可写可读：{target.parent}"


def _run_features() -> dict:
    report = {"mode": "features", "checks": [], "ok": False}
    from PyQt5.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    for name, function in (
        ("workbench_pages", _feature_workbench_pages),
        ("office_window", _feature_office_window),
        ("forum_window", _feature_forum_window),
        ("particles", _feature_particles),
        ("services", _feature_services),
        ("assets", _feature_assets),
        ("storage", _feature_storage),
    ):
        _run_check(report, name, function)
    application.processEvents()
    report["ok"] = all(item["ok"] for item in report["checks"])
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("startup", "features"), required=True)
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--ready-timeout", type=float, default=180.0)
    parser.add_argument("--settle", type=float, default=3.0)
    args = parser.parse_args(argv)

    _prepare_app_root(args.app_root.resolve())
    try:
        report = _run_startup(args) if args.mode == "startup" else _run_features()
    except Exception as exc:  # noqa: BLE001 - 启动期异常也要落进报告
        report = {
            "mode": args.mode,
            "checks": [
                {
                    "name": "unhandled",
                    "ok": False,
                    "detail": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc()[-2000:],
                }
            ],
            "ok": False,
        }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
