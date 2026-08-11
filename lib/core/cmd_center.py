"""CMD命令中心 - 订阅输入事件，分发处理逻辑"""
import subprocess
from collections.abc import Callable

from lib.core.logger import get_logger
logger = get_logger(__name__)

from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.hash_cmd_registry import get_hash_cmd_registry
from config.config import TIMEOUTS


_BACKEND_ALIASES = {
    'dx': 'directx',
    'directx': 'directx',
    'qt': 'qt',
}


def _save_render_backend(backend_id: str) -> None:
    from config.general_user_settings import save_general_values

    save_general_values({'UI': {'render_backend': backend_id}})


class CmdCenter:
    """
    CMD命令中心

    - INPUT_COMMAND（/前缀）：在后台线程执行 shell 命令，输出发布为 INFORMATION 事件
    - INPUT_HASH  （#前缀）：调试日志 + 未知命令失败气泡；具体命令由各管理器直接订阅处理
    - INPUT_CHAT  （无前缀）：由 ChatHandler 处理，此处不再重复处理
    """

    def __init__(
        self,
        *,
        event_center=None,
        compute_hub=None,
        backend_saver: Callable[[str], None] | None = None,
    ):
        self._event_center = event_center or get_event_center()
        self._compute_hub = compute_hub
        self._backend_saver = backend_saver or _save_render_backend
        self._cleaned = False
        self._event_center.subscribe(EventType.INPUT_COMMAND, self._on_input_command)
        self._event_center.subscribe(EventType.INPUT_HASH,    self._on_input_hash)
        # INPUT_CHAT 由 ChatHandler 处理，此处不再订阅
        get_hash_cmd_registry().register('图层', '', '查看当前窗口图层快照')
        get_hash_cmd_registry().register(
            '后端',
            '[dx/qt]',
            '实验性切换绘制后端并重启',
        )

    # ------------------------------------------------------------------
    # 处理器
    # ------------------------------------------------------------------

    def _on_input_command(self, event: Event):
        """处理 / 命令：启动守护线程执行，不阻塞主线程"""
        cmd = event.data.get('text', '').strip()
        if not cmd:
            return
        compute_hub = self._compute_hub or get_compute_hub()
        compute_hub.submit_io(self._run_command, cmd)

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
        # EventCenter 会通过注入的 EventPump 把后台发布切回所属线程。
        self._on_result_ready(output)

    def _on_result_ready(self, output: str):
        """发布命令结果；线程切换由 EventCenter 负责。"""
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

        if text == '图层':
            from lib.core.layer_manager import get_layer_manager

            self._event_center.publish(Event(EventType.INFORMATION, {
                'text': get_layer_manager().describe_snapshot(),
                'min': 10,
                'max': 160,
                'align': 'left',
            }))
            return

        parts = text.split(None, 1)
        command = parts[0]
        argument = parts[1] if len(parts) > 1 else ''
        if command == '后端':
            self._change_render_backend(argument)
            return

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

    def _change_render_backend(self, argument: str) -> None:
        requested = str(argument or '').strip().lower()
        backend_id = _BACKEND_ALIASES.get(requested)
        if backend_id is None:
            self._event_center.publish(Event(EventType.INFORMATION, {
                'text': '用法：#后端 dx 或 #后端 qt',
                'min': 10,
                'max': 120,
            }))
            return

        from config.config import UI

        current = _BACKEND_ALIASES.get(
            str(UI.get('render_backend', 'qt')).strip().lower(),
            str(UI.get('render_backend', 'qt')).strip().lower(),
        )
        if current == backend_id:
            self._event_center.publish(Event(EventType.INFORMATION, {
                'text': f'当前已是 {"DX" if backend_id == "directx" else "Qt"} 后端',
                'min': 10,
                'max': 100,
            }))
            return

        compute_hub = self._compute_hub or get_compute_hub()
        try:
            future = compute_hub.submit_interactive_io(
                self._backend_saver,
                backend_id,
            )
        except Exception as exc:
            self._publish_backend_change_failure(exc)
            return
        future.add_done_callback(
            lambda completed, target=backend_id: self._finish_backend_change(
                target,
                completed,
            )
        )

    def _finish_backend_change(self, backend_id: str, future) -> None:
        if self._cleaned:
            return
        try:
            future.result()
        except Exception as exc:
            self._publish_backend_change_failure(exc)
            return

        label = 'DX' if backend_id == 'directx' else 'Qt'
        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': f'绘制后端已切换为 {label}，正在重启',
            'min': 10,
            'max': 100,
        }))
        self._event_center.publish(Event(EventType.APP_QUIT, {
            'restart': True,
            'source': 'hash_backend_command',
            'render_backend': backend_id,
        }))

    def _publish_backend_change_failure(self, error: BaseException) -> None:
        logger.error('保存绘制后端失败: %s', error)
        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': f'切换绘制后端失败：{error}',
            'min': 10,
            'max': 140,
        }))

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------

    def cleanup(self):
        """取消所有事件订阅，可重复调用。"""
        if self._cleaned:
            return
        self._cleaned = True
        self._event_center.unsubscribe(EventType.INPUT_COMMAND, self._on_input_command)
        self._event_center.unsubscribe(EventType.INPUT_HASH,    self._on_input_hash)
        registry = get_hash_cmd_registry()
        registry.unregister('图层')
        registry.unregister('后端')


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
