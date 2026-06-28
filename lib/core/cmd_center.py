"""CMD命令中心 - 订阅输入事件，分发处理逻辑"""
import subprocess

from lib.core.logger import get_logger
logger = get_logger(__name__)

from PyQt5.QtCore import QObject, pyqtSignal

from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.hash_cmd_registry import get_hash_cmd_registry
from config.config import TIMEOUTS


class _ResultSignal(QObject):
    """线程安全信号：后台线程通过此信号将命令结果传回主线程"""
    ready = pyqtSignal(str)


class CmdCenter:
    """
    CMD命令中心

    - INPUT_COMMAND（/前缀）：在后台线程执行 shell 命令，输出发布为 INFORMATION 事件
    - INPUT_HASH  （#前缀）：调试日志 + 未知命令失败气泡；具体命令由各管理器直接订阅处理
    - INPUT_CHAT  （无前缀）：由 ChatHandler 处理，此处不再重复处理
    """

    def __init__(self):
        self._event_center = get_event_center()
        self._event_center.subscribe(EventType.INPUT_COMMAND, self._on_input_command)
        self._event_center.subscribe(EventType.INPUT_HASH,    self._on_input_hash)
        # INPUT_CHAT 由 ChatHandler 处理，此处不再订阅

        # 线程安全信号：后台线程完成后通过此信号回到主线程
        self._signal = _ResultSignal()
        self._signal.ready.connect(self._on_result_ready)

    # ------------------------------------------------------------------
    # 处理器
    # ------------------------------------------------------------------

    def _on_input_command(self, event: Event):
        """处理 / 命令：启动守护线程执行，不阻塞主线程"""
        cmd = event.data.get('text', '').strip()
        if not cmd:
            return
        get_compute_hub().submit_io(self._run_command, cmd)

    def _run_command(self, cmd: str):
        """在后台线程中执行命令（超时配置化，不阻塞 Qt 主线程）"""
        timeout_val = TIMEOUTS['cmd_exec']
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, timeout=timeout_val
            )
            raw = result.stdout or result.stderr or b''
            output = raw.decode('gbk', errors='replace').strip() or '命令执行完成'
        except subprocess.TimeoutExpired:
            output = f'命令超时（{timeout_val}s）'
        except Exception as e:
            output = f'错误: {e}'

        logger.debug('[CmdCenter] /%s  →  %s', cmd, output[:80])
        # 通过 Qt 信号安全传回主线程（PyQt5 跨线程自动使用队列连接）
        self._signal.ready.emit(output)

    def _on_result_ready(self, output: str):
        """在主线程中处理命令结果"""
        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': output,
            'min':  10,
            'max':  100,
            'align': 'left',  # /命令输出左对齐
        }))

    def _on_input_hash(self, event: Event):
        """处理 # 命令：记录调试信息，并对未知命令显示失败气泡"""
        text = event.data.get('text', '').strip()  # 已去掉 '#'，如 "雪豹 3"
        if not text:
            return

        # 调试日志
        logger.debug('[CmdCenter] #%s', text)

        # 按命令名前缀检查是否匹配已注册命令
        all_cmds = get_hash_cmd_registry().get_all()
        is_known = any(text.startswith(name) for name, _, _ in all_cmds)

        if not is_known:
            # 未知命令：显示失败气泡并列出可用命令
            cmd_name = text.split()[0]
            if all_cmds:
                available = ' '.join(f'#{name}' for name, _, _ in all_cmds)
                output = f'未知命令 #{cmd_name}，可用：{available}'
            else:
                output = f'未知命令：#{cmd_name}'

            self._event_center.publish(Event(EventType.INFORMATION, {
                'text':  output,
                'min':   10,
                'max':   120,
            }))

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------

    def cleanup(self):
        """取消所有事件订阅，断开信号"""
        self._event_center.unsubscribe(EventType.INPUT_COMMAND, self._on_input_command)
        self._event_center.unsubscribe(EventType.INPUT_HASH,    self._on_input_hash)
        self._signal.ready.disconnect(self._on_result_ready)


# ----------------------------------------------------------------------
# 全局单例
# ----------------------------------------------------------------------

_cmd_center: CmdCenter | None = None


def get_cmd_center() -> CmdCenter:
    """获取全局 CmdCenter 实例（单例）"""
    global _cmd_center
    if _cmd_center is None:
        _cmd_center = CmdCenter()
    return _cmd_center


def cleanup_cmd_center():
    """清理全局 CmdCenter 实例"""
    global _cmd_center
    if _cmd_center is not None:
        _cmd_center.cleanup()
        _cmd_center = None
