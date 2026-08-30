"""清理命令处理器 - 清除场上的雪豹、沙发、摩托、闹钟、音响、雪堆等 obj 类物体"""
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.hash_cmd_registry import get_hash_cmd_registry
from lib.script.plugin_registry import get_manager
from lib.core.logger import get_logger

_logger = get_logger(__name__)


def log(msg: str):
    _logger.debug("[CleanupHandler] %s", msg)


class CleanupHandler:
    """
    清理命令处理器。

    订阅 INPUT_HASH 事件，解析 "#清理" 命令，
    清除场上所有雪豹、沙发、摩托、闹钟、音响、雪堆等 obj 类物体。
    """

    def __init__(self):
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.INPUT_HASH, self._on_hash_command)

        get_hash_cmd_registry().register('清理', '', '清理其余游戏物体')

        log("已初始化")

    def _on_hash_command(self, event: Event):
        """
        处理 INPUT_HASH 事件。

        命令格式：#清理
        """
        text = event.data.get('text', '').strip()
        if text != '清理':
            return

        self.run_cleanup()

    def run_cleanup(self):
        """执行一次全场 obj 清理。"""
        log("收到清理命令，开始清理所有 obj 类物体")

        cleared_counts = {}

        # 清理雪豹
        snow_leopard_mgr = get_manager('snow_leopard')
        if snow_leopard_mgr and hasattr(snow_leopard_mgr, "clear_all_leopards"):
            count = int(snow_leopard_mgr.clear_all_leopards(fadeout=True))
            cleared_counts['雪豹'] = count

        # 清理沙发
        sofa_mgr = get_manager('sofa')
        if sofa_mgr and hasattr(sofa_mgr, "clear_all_sofas"):
            count = int(sofa_mgr.clear_all_sofas(fadeout=True))
            cleared_counts['沙发'] = count

        # 清理摩托
        mortor_mgr = get_manager('mortor')
        if mortor_mgr and hasattr(mortor_mgr, "clear_all_mortors"):
            count = int(mortor_mgr.clear_all_mortors(fadeout=True))
            cleared_counts['摩托'] = count

        # 清理闹钟
        clock_mgr = get_manager('clock')
        if clock_mgr and hasattr(clock_mgr, "clear_all_clocks"):
            count = int(clock_mgr.clear_all_clocks(fadeout=True))
            cleared_counts['闹钟'] = count

        # 清理音响
        speaker_mgr = get_manager('speaker')
        if speaker_mgr and hasattr(speaker_mgr, "clear_all_speakers"):
            count = int(speaker_mgr.clear_all_speakers(fadeout=True))
            cleared_counts['音响'] = count

            if count > 0:
                self._event_center.publish(Event(EventType.MUSIC_PLAY_PAUSE, {
                    'playing': False,
                }))

        # 清理雪堆
        snow_pile_mgr = get_manager('snow_pile')
        if snow_pile_mgr and hasattr(snow_pile_mgr, "clear_all_piles"):
            count = int(snow_pile_mgr.clear_all_piles())
            cleared_counts['雪堆'] = count

        # 生成清理报告
        report_parts = [f'{name}{count}个' for name, count in cleared_counts.items() if count > 0]
        if report_parts:
            report = f"已清理：{'、'.join(report_parts)}"
        else:
            report = "场上没有需要清理的物体"

        log(f"清理完成: {cleared_counts}")

        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': report,
            'min': 20,
            'max': 100,
        }))

    def cleanup(self):
        """取消事件订阅"""
        self._event_center.unsubscribe(EventType.INPUT_HASH, self._on_hash_command)
        log("已清理")


# ── 全局单例 ─────────────────────────────────────────────────────────────

_instance: CleanupHandler | None = None


def get_cleanup_handler() -> CleanupHandler:
    """获取全局 CleanupHandler 单例"""
    global _instance
    if _instance is None:
        _instance = CleanupHandler()
    return _instance


def cleanup_cleanup_handler():
    """清理全局单例"""
    global _instance
    if _instance is not None:
        _instance.cleanup()
        _instance = None
