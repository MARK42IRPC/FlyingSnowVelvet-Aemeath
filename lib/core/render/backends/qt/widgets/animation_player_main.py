"""独立动画播放器的进程入口（档位 D）。

`lib/script/SEanima/animation.py` 以子进程方式启动动画播放器：原实现用
`sys.executable lib/script/ui/animation_player.py <type>`。迁档后控件落在
`lib/core/render/backends/qt/widgets/animation_player.py`，但该宿主不以 `__main__`
方式运行（产品垫片要保持零 PyQt5）；因此这里提供真正的进程入口，播放器子进程改为启动
本文件。

用法：`python lib/core/render/backends/qt/widgets/animation_player_main.py <start|exit>`
"""

from __future__ import annotations

import logging
import os
import sys
import time


def _ensure_qt_platform_plugins() -> None:
    """设置 Qt 平台插件路径（与原版播放器一致）。"""
    if 'QT_QPA_PLATFORM_PLUGIN_PATH' in os.environ:
        return
    try:
        import PyQt5.QtCore

        qt_path = os.path.dirname(PyQt5.QtCore.__file__)
        platforms_path = os.path.join(qt_path, 'Qt5', 'plugins', 'platforms')
        if os.path.exists(platforms_path):
            os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = platforms_path
            logging.getLogger(__name__).info(
                "[AnimationPlayer] Qt平台插件路径: %s", platforms_path
            )
    except Exception:
        pass


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if len(argv) < 2:
        return 1

    animation_type = argv[1]
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    logger.info("[AnimationPlayer] 启动流式解码播放器: %s", animation_type)

    _ensure_qt_platform_plugins()

    from PyQt5.QtWidgets import QApplication

    from lib.core.render.backends.qt.widgets.animation_player import AnimationWindow

    t0 = time.time()
    app = QApplication(argv)
    app.setQuitOnLastWindowClosed(False)
    logger.info("[AnimationPlayer] QApplication 创建耗时: %.2fs", time.time() - t0)

    window = AnimationWindow(animation_type)
    window.start()

    logger.info("[AnimationPlayer] 总启动耗时: %.2fs，进入事件循环", time.time() - t0)
    return app.exec_()


if __name__ == '__main__':
    sys.exit(main())
