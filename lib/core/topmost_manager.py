"""极高绘制优先级管理器

在 Windows 平台通过 ctypes 直接调用 Win32 API
    SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE)
定期对项目全部已注册窗口重申置顶层级，防止其他应用程序（全屏游戏、视频播放器等）遮挡桌宠。

非 Windows 平台降级为 QWidget.raise_()。

用法：
    from lib.core.topmost_manager import get_topmost_manager
    # 在窗口 __init__ 中注册（在 self.show() 之后或之前均可）：
    get_topmost_manager().register(self)
    # 在 PetWindow 的 FRAME 事件中驱动（每 ENFORCE_INTERVAL 帧执行一次）：
    get_topmost_manager().enforce_on_frame()
"""

import sys
import weakref

_INSTANCE = None

TOPMOST_PRIORITY_DEFAULT = 0
TOPMOST_PRIORITY_QR_DIALOG = 90
TOPMOST_PRIORITY_MAIN_PET = 100


def get_topmost_manager() -> 'TopmostManager':
    """返回全局单例 TopmostManager。"""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = TopmostManager()
    return _INSTANCE


class TopmostManager:
    """
    集中管理项目全部窗口的 z-order 置顶状态。

    - register(widget)   : 注册需要保持极高优先级的窗口
    - enforce_on_frame() : 由 PetWindow 的 FRAME 事件驱动，每 ENFORCE_INTERVAL
                           帧对全部存活窗口调用一次 Win32 SetWindowPos(HWND_TOPMOST)
    - pause() / resume() : 暂停/恢复强制置顶（穿透模式下使用，避免干扰全屏游戏）
    """

    # 每隔多少帧执行一次强制置顶（60 fps → 30 帧 ≈ 0.5 秒）
    ENFORCE_INTERVAL: int = 30

    # Win32 SetWindowPos flags: SWP_NOSIZE(0x01) | SWP_NOMOVE(0x02) | SWP_NOACTIVATE(0x10)
    _SWP_FLAGS: int = 0x0013

    # HWND_TOPMOST 特殊句柄值（-1）
    _HWND_TOPMOST: int = -1

    def __init__(self) -> None:
        self._windows: list[tuple[int, int, weakref.ref]] = []
        self._register_seq: int = 0
        self._counter: int = 0
        self._paused: bool = False  # 穿透模式下暂停强制置顶

        if sys.platform == 'win32':
            import ctypes
            self._user32 = ctypes.windll.user32
        else:
            self._user32 = None

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def register(self, widget, priority: int = TOPMOST_PRIORITY_DEFAULT) -> None:
        """注册一个需要保持极高绘制优先级的窗口（弱引用，不阻止 GC）。"""
        self._register_seq += 1
        self._windows.append((int(priority), self._register_seq, weakref.ref(widget)))

    def enforce_on_frame(self) -> None:
        """
        由 FRAME 事件每帧调用。
        每 ENFORCE_INTERVAL 帧对全部存活且可见的窗口执行一次强制置顶。
        如果处于暂停状态（穿透模式），则跳过强制置顶，避免干扰全屏游戏。
        """
        if self._paused:
            return

        self._counter += 1
        if self._counter % self.ENFORCE_INTERVAL == 0:
            self._enforce_all()

    def pause(self) -> None:
        """暂停强制置顶（穿透模式时调用）"""
        self._paused = True

    def resume(self) -> None:
        """恢复强制置顶（退出穿透模式时调用）"""
        self._paused = False
        # 恢复后立即执行一次强制置顶
        self._enforce_all()

    def bring_to_front(self, widget) -> None:
        """立即将指定窗口提升到当前项目窗口栈顶。"""
        if widget is None or not widget.isVisible():
            return
        self._set_topmost(widget)

    def enforce_now(self) -> None:
        """立即按注册优先级重申全部可见窗口层级。"""
        if self._paused:
            return
        self._enforce_all()

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _enforce_all(self) -> None:
        """对所有存活窗口调用 SetWindowPos(HWND_TOPMOST) 重申最高置顶层级。"""
        alive: list[tuple[int, int, weakref.ref]] = []
        for priority, seq, ref in sorted(self._windows, key=lambda item: (item[0], item[1])):
            widget = ref()
            if widget is not None:
                alive.append((priority, seq, ref))
                if widget.isVisible():
                    self._set_topmost(widget)
        # 同步清理已销毁的弱引用
        self._windows = alive

    def _set_topmost(self, widget) -> None:
        """
        Windows：调用 Win32 SetWindowPos 将窗口置于 HWND_TOPMOST 层，
                  静默置顶（不移动、不改变大小、不激活）。
        其他平台：调用 QWidget.raise_()。
        """
        if self._user32 is not None:
            self._user32.SetWindowPos(
                int(widget.winId()),
                self._HWND_TOPMOST,
                0, 0, 0, 0,
                self._SWP_FLAGS,
            )
        else:
            widget.raise_()
