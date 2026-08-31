"""启动/退出动画管理器 - 流式加载版本"""
import os
import sys

from lib.core.event.center import get_event_center, EventType, Event
from lib.core.logger import get_logger
from config.config import ANIMATION

logger = get_logger(__name__)


class StartExitAnimation:
    """启动/退出动画管理器（流式加载版本）

    动画在独立进程中播放，采用边播放边加载的方式：
    - 使用后台线程预加载帧到缓冲区
    - 播放时从缓冲区获取帧
    - 缓冲区为空时等待加载
    - 实现了流式加载，大幅减少启动延迟
    """

    def __init__(self):
        # 事件中心
        self._event_center = get_event_center()

        # 订阅预启动事件和退出事件
        self._event_center.subscribe(EventType.APP_PRE_START, self._handle_pre_start)
        self._event_center.subscribe(EventType.APP_EXIT, self._handle_exit)

    def _handle_pre_start(self, event):
        """处理预启动事件"""
        self.play_start()

    def _handle_exit(self, event):
        """处理退出事件"""
        self.play_exit()

    def _is_enabled(self) -> bool:
        """读取启动/退出动画总开关。"""
        return bool(ANIMATION.get('start_exit_enabled', True))

    def _play_animation(self, animation_type: str):
        """
        播放动画（独立进程，流式加载）

        Args:
            animation_type: 动画类型 ('start' 或 'exit')
        """
        import subprocess

        # 创建动画播放脚本
        player_script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            'lib', 'script', 'ui', 'animation_player.py'
        )

        # 启动子进程播放动画（流式加载）
        subprocess.Popen(
            [sys.executable, player_script, animation_type],
            creationflags=subprocess.CREATE_NO_WINDOW
        )

    def play_start(self):
        """主动播放启动动画。"""
        if not self._is_enabled():
            logger.info("[StartExitAnimation] 启动/退出动画已关闭，跳过启动动画")
            return
        logger.info("[StartExitAnimation] 播放启动动画（流式加载）")
        self._play_animation('start')

    def play_exit(self):
        """主动播放退出动画。"""
        if not self._is_enabled():
            logger.info("[StartExitAnimation] 启动/退出动画已关闭，跳过退出动画")
            return
        logger.info("[StartExitAnimation] 播放退出动画（流式加载）")
        self._play_animation('exit')

    def cleanup(self):
        """清理资源"""
        self._event_center.unsubscribe(EventType.APP_PRE_START, self._handle_pre_start)
        self._event_center.unsubscribe(EventType.APP_EXIT, self._handle_exit)


# 全局单例
_start_exit_animation = None


def get_start_exit_animation() -> StartExitAnimation:
    """获取启动/退出动画实例（单例模式）"""
    global _start_exit_animation
    if _start_exit_animation is None:
        _start_exit_animation = StartExitAnimation()
    return _start_exit_animation


def cleanup_start_exit_animation():
    """清理启动/退出动画实例"""
    global _start_exit_animation
    if _start_exit_animation is not None:
        _start_exit_animation.cleanup()
        _start_exit_animation = None
