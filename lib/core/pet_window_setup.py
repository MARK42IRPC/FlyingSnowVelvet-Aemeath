"""PetWindow 窗口初始化辅助。"""

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtWidgets import QApplication

from config.config import ANIMATION
from lib.core.topmost_manager import TOPMOST_PRIORITY_MAIN_PET, get_topmost_manager


def setup_pet_window(owner) -> None:
    """配置主桌宠窗口位置与基础窗口属性。"""
    screen = QApplication.primaryScreen().geometry()
    width, height = ANIMATION['pet_size']
    owner.move(
        screen.width() // 2 - width // 2,
        screen.height() // 2 - height // 2,
    )

    owner.setWindowFlags(
        Qt.FramelessWindowHint
        | Qt.WindowStaysOnTopHint
        | Qt.Tool
        | Qt.WindowSystemMenuHint
    )
    owner.setAttribute(Qt.WA_TranslucentBackground)
    owner.setAttribute(Qt.WA_NoSystemBackground)
    owner.setFixedSize(*ANIMATION['pet_size'])
    owner.setCursor(Qt.ArrowCursor)


def finalize_pet_window_startup(owner) -> None:
    """显示主窗口并完成启动后的 UI 预热。"""
    owner.show()
    owner._startup_voice_sound.play()
    owner._move_particle_last_pos = QPoint(owner.frameGeometry().topLeft())
    owner._move_particle_enabled = True
    get_topmost_manager().register(owner, priority=TOPMOST_PRIORITY_MAIN_PET)
    owner.update()
    owner._preload_ui()
