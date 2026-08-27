"""主程序入口模块 - 使用动态发现机制初始化模块"""
import sys
import os
import threading
import time
from dataclasses import replace

from config.config import GIF_FILES, DRAW, ANIMATION
from lib.core.application_runtime import ApplicationRuntime
from lib.core.application_ui import ApplicationUiHost
from lib.core.backend_router import BackendSelection, get_active_backend_selection
from lib.core.desktop_backend import (
    DesktopBackendBundle,
    get_desktop_backend_bundle,
)
from lib.core.graphics.gif_loader import GifLoader

from lib.core.event.center import get_event_center, EventType, Event, cleanup_event_center
from lib.core.logger import initialize as initialize_app_logger, cleanup as cleanup_app_logger, get_logger
from lib.core.cmd_center import get_cmd_center, cleanup_cmd_center
from lib.core.compute_hub import cleanup_compute_hub
from lib.core.compute_hub import get_compute_hub
from lib.core.clickthrough_state import is_clickthrough_enabled
from lib.core.tray_host import TrayCommand, TrayMenuState
from lib.script.chat.ollama import get_ollama_manager, cleanup_ollama_manager
from lib.script.chat.handler import get_chat_handler, cleanup_chat_handler
from lib.script.chat.memory import get_stream_memory, cleanup_stream_memory
from lib.script.office import (
    cleanup_interaction_mode_service,
    cleanup_office_service,
    get_interaction_mode_service,
    get_office_service,
)
from lib.script.tool_dispatcher import get_tool_dispatcher, cleanup_tool_dispatcher
from lib.script.gsvmove import get_gsvmove_service, cleanup_gsvmove_service
from lib.script.bug_tracker import get_bug_tracker_service, cleanup_bug_tracker_service
from lib.script.microphone_stt import (
    cleanup_microphone_push_to_talk_manager,
    cleanup_microphone_stt_service,
    get_microphone_push_to_talk_manager,
    get_microphone_stt_service,
)
from lib.script.voice.handler import get_voice_request_handler, cleanup_voice_request_handler
from lib.script.app.game_mode_service import get_game_mode_service, cleanup_game_mode_service
from lib.script.app.tray_actions import (
    TrayActionResult,
    cleanup_music_cache,
    cleanup_music_history,
    open_author_page,
    prepare_autostart_state,
    set_autostart_enabled,
)
from lib.core.plugin_registry import (
    discover_all, init_all_managers, cleanup_all_managers, get_manager
)
from lib.script.app.single_instance import (
    acquire_single_instance_lock as _new_acquire_single_instance_lock,
    notify_already_running as _new_notify_already_running,
    release_single_instance_lock as _new_release_single_instance_lock,
)
from lib.script.app.startup_probe import log_startup_hardware_info as _new_log_startup_hardware_info
from lib.script.app.desktop_shortcut import ensure_desktop_shortcut as _new_ensure_desktop_shortcut
from lib.script.app.restart import launch_current_application as _launch_current_application

logger = get_logger(__name__)
_SHUTDOWN_FORCE_TIMEOUT_MS = 15000
_SHUTDOWN_QUIT_RETRY_MS = 500
_SHUTDOWN_THREAD_DRAIN_SECONDS = 2.0

class ApplicationState:
    """应用程序状态管理"""

    def __init__(
        self,
        application_runtime: ApplicationRuntime | None = None,
        application_ui_host: ApplicationUiHost | None = None,
        backend_bundle: DesktopBackendBundle | None = None,
        backend_selection: BackendSelection | None = None,
    ):
        bundle = backend_bundle or get_desktop_backend_bundle()
        if bundle is None:
            raise RuntimeError("desktop backend is not configured")
        selection = backend_selection or get_active_backend_selection()
        if selection is None:
            raise RuntimeError("desktop backend selection is not configured")

        scheduler_factory = bundle.scheduler_factory
        screen_capture_factory = bundle.screen_capture_factory
        self._backend_selection = selection
        self._pet_window_factory = bundle.pet_window_factory
        self._particle_overlay_factory = bundle.particle_overlay_factory
        self._effect_overlay_factory = bundle.effect_overlay_factory
        self._tray_host_factory = bundle.tray_host_factory
        self._backend_cleanup = bundle.cleanup
        self._backend_cleaned = False
        self._application_runtime = application_runtime or bundle.application_runtime_factory()
        self._application_ui = application_ui_host or bundle.application_ui_host_factory()
        self._event_center = get_event_center()
        self._app = None
        self._pet = None
        self._gifs = None
        self._particles = None
        self._effects = None
        # 管理器实例字典（由动态发现机制填充）
        self._managers = {}
        # 清理命令处理器
        self._cleanup_handler = None
        # 工具调度器
        self._tool_dispatcher = None
        self._game_mode = get_game_mode_service()
        self._tray_menu_state = TrayMenuState(
            game_mode_enabled=bool(getattr(self._game_mode, 'is_enabled', lambda: False)()),
            clickthrough_enabled=bool(is_clickthrough_enabled()),
        )
        self._tray_action_lock = threading.Lock()
        self._pending_tray_actions: set[TrayCommand] = set()
        # 工作目录
        self._script_dir = None
        # 初始化完成标志
        self._init_ready = False
        # 系统托盘图标
        self._tray_host = None
        self._exit_requested = False
        self._restart_requested = False
        self._restart_helper_started = False
        self._exit_in_progress = False
        self._exit_completed = False
        self._components_cleaned = False
        self._logger_cleaned = False
        self._exit_code = 0
        self._shutdown_steps = []
        self._shutdown_step_index = 0
        self._shutdown_force_quit_armed = False
        self._shutdown_force_timer = None
        self._shutdown_clean_exit_confirmed = False
        self._runtime_exit_requested = False
        self._runtime_exit_acknowledged = False
        self._app_exit_event_published = False

        # 音频核心在事件中心初始化后立即创建，以便订阅 APP_PRE_START 完成 MCI 预热
        from lib.core.voice.core import get_voice_core
        self._voice = get_voice_core()
        # ONNX 文本转语音桥接：主界面就绪后在隔离 Worker 中加载本地模型。
        self._gsvmove = get_gsvmove_service()
        self._bug_tracker = get_bug_tracker_service()
        self._microphone_stt = get_microphone_stt_service()
        self._microphone_push_to_talk = get_microphone_push_to_talk_manager()
        # 语音抽象层：接收 VOICE_REQUEST 并路由到底层声音系统
        self._voice_script = get_voice_request_handler()
        # CmdCenter 在事件中心初始化后立即注册，确保捕获所有输入事件
        self._cmd_center = get_cmd_center()

        # 普通文本先经过交互模式路由；办公服务与聊天服务各自持有独立调度器。
        self._interaction_mode = get_interaction_mode_service()
        self._office = get_office_service(scheduler=scheduler_factory())

        # OllamaManager 需在 APP_PRE_START 前注册（订阅该事件以尝试启动服务）。
        # 调度器和截图服务只在桌面组合边界创建，聊天业务模块仅依赖核心协议。
        get_ollama_manager(scheduler=scheduler_factory())
        self._chat_handler = get_chat_handler(
            scheduler=scheduler_factory(),
            screen_capture=screen_capture_factory(),
            mode_service=self._interaction_mode,
        )
        self._stream_memory = get_stream_memory(mode_service=self._interaction_mode)

        # 订阅事件
        self._event_center.subscribe(EventType.APP_PRE_START, self._on_pre_start)
        self._event_center.subscribe(EventType.APP_INIT_READY, self._on_init_ready)
        self._event_center.subscribe(EventType.APP_QUIT, self._on_app_quit)
        self._event_center.subscribe(EventType.GAME_MODE_STATUS_CHANGE, self._on_game_mode_status_change)
        self._event_center.subscribe(EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_status_change)
        self._event_center.subscribe(EventType.AUTOSTART_STATUS_CHANGE, self._on_autostart_status_change)
        self._events_subscribed = True

    def _publish_event(self, event_type: EventType, data: dict = None):
        """发布事件"""
        event = Event(event_type, data or {})
        self._event_center.publish(event)

    def _on_pre_start(self, event: Event):
        """预启动事件回调 - 执行初始化并启动3秒非阻塞等待"""
        self._script_dir = event.data.get('working_dir', '')

        # ── 动态发现模块（扫描管理器和粒子脚本）──────────────────────
        discover_all()

        # 启动延时与启动动画开关绑定：关闭动画时跳过延时。
        startup_delay_ms = 3000 if bool(ANIMATION.get('start_exit_enabled', True)) else 0
        if startup_delay_ms > 0:
            logger.info('等待 3 秒初始化...')
        else:
            logger.info('启动/退出动画已关闭，跳过启动延时，立即初始化')
        self._application_runtime.schedule_once(startup_delay_ms, self._on_init_timer)

    def _on_init_timer(self):
        """3秒定时器回调 - 发布初始化就绪事件"""
        logger.info('初始化就绪！')
        self._init_ready = True
        self._publish_event(EventType.APP_INIT_READY, {
            'working_dir': self._script_dir
        })

    def _on_init_ready(self, event: Event):
        """初始化就绪事件回调 - 创建主窗口和初始化管理器"""
        # 发布启动事件
        self._publish_event(EventType.APP_START, {
            'working_dir': self._script_dir
        })

        # 宠物窗口
        self._pet = self._pet_window_factory(self._gifs, self._particles)

        # ── 使用动态发现机制初始化所有管理器 ────────────────────────────
        # 管理器会在模块加载时自动注册，这里统一初始化
        self._managers = init_all_managers(self._pet)

        # ── 初始化清理命令处理器 ────────────────────────────────────────
        from lib.script.practical.cleanup_handler import get_cleanup_handler
        self._cleanup_handler = get_cleanup_handler()

        # ── 初始化工具调度器 ────────────────────────────────────────────
        self._tool_dispatcher = get_tool_dispatcher(mode_service=self._interaction_mode)

        self._game_mode.configure_runtime(self._pet, self._particles, self._effects)
        self._application_ui.prepare_runtime()

        # 发布main事件，进入main状态
        self._publish_event(EventType.APP_MAIN, {
            'gifs_loaded': len(self._gifs)
        })

        # 初始化系统托盘图标
        self._tray_host = self._tray_host_factory()
        self._tray_menu_state = replace(
            self._tray_menu_state,
            autostart_enabled=prepare_autostart_state(),
        )
        self._tray_host.set_menu_state(self._tray_menu_state)
        self._tray_host.disconnect_quit_requested(self._on_tray_quit)
        self._tray_host.connect_quit_requested(self._on_tray_quit)
        self._tray_host.disconnect_announcement_requested(self._on_tray_announcement)
        self._tray_host.connect_announcement_requested(self._on_tray_announcement)
        self._tray_host.disconnect_command_requested(self._on_tray_command)
        self._tray_host.connect_command_requested(self._on_tray_command)
        self._publish_event(EventType.AUTOSTART_STATUS_CHANGE, {
            'enabled': self._tray_menu_state.autostart_enabled,
            'source': 'tray_init',
        })

        if self._tray_host.initialize():
            logger.info('系统托盘图标初始化成功')
        else:
            logger.warning('系统托盘图标初始化未立即成功，已转入后台重试')

        self._application_ui.start_runtime(self._app)

        logger.info('桌面宠物启动成功！')
        logger.info('  左键点击 → 随机动作 + 粒子特效')
        logger.info('  右键点击 → 打开/关闭 CMD 输入框')
        logger.info('  鼠标悬停 → 显示关闭按钮（右上角）')
        logger.info('  系统托盘 → 右键菜单退出')

        # 办公模式启动预热
        self._warmup_office_runtime_if_enabled()

    def _warmup_office_runtime_if_enabled(self) -> None:
        """如果启用了办公模式启动预热，则在桌宠启动时预热运行时。"""
        try:
            import config.ollama_config as oc
            if not oc.OFFICE_MODE.get("warmup_on_startup", True):
                return
            logger.info("[Office] 启动预热已启用，开始预热办公运行时")
            self._office.warmup_runtime()
        except Exception as exc:
            logger.debug("[Office] 启动预热失败: %s", exc)

    def _on_tray_quit(self):
        """托盘菜单退出回调"""
        # 调用 exit 方法进行正常退出流程
        self.request_exit(0)

    def _on_tray_announcement(self):
        """托盘菜单公告回调。"""
        self._application_ui.open_announcement()

    def _publish_information(self, text: str) -> None:
        self._publish_event(EventType.INFORMATION, {
            'text': str(text),
            'min': 0,
            'max': 60,
        })

    def _set_tray_menu_state(self, **changes) -> None:
        self._tray_menu_state = replace(self._tray_menu_state, **changes)
        tray = self._tray_host
        if tray is not None:
            try:
                tray.set_menu_state(self._tray_menu_state)
            except Exception:
                logger.exception('同步托盘菜单状态失败')

    def _on_game_mode_status_change(self, event: Event) -> None:
        data = event.data if isinstance(event.data, dict) else {}
        self._set_tray_menu_state(game_mode_enabled=bool(data.get('enabled', False)))

    def _on_clickthrough_status_change(self, event: Event) -> None:
        data = event.data if isinstance(event.data, dict) else {}
        self._set_tray_menu_state(clickthrough_enabled=bool(data.get('enabled', False)))

    def _on_autostart_status_change(self, event: Event) -> None:
        data = event.data if isinstance(event.data, dict) else {}
        self._set_tray_menu_state(autostart_enabled=bool(data.get('enabled', False)))

    def _submit_tray_action(self, command: TrayCommand, worker) -> None:
        with self._tray_action_lock:
            if command in self._pending_tray_actions:
                return
            self._pending_tray_actions.add(command)
        try:
            future = get_compute_hub().submit_interactive_io(worker)
        except Exception as exc:
            with self._tray_action_lock:
                self._pending_tray_actions.discard(command)
            logger.error('提交托盘操作失败 command=%s: %s', command.name, exc)
            self._publish_information('操作暂不可用，请稍后重试')
            return

        def complete(done_future) -> None:
            with self._tray_action_lock:
                self._pending_tray_actions.discard(command)
            if self._exit_in_progress or self._exit_completed:
                return
            try:
                result = done_future.result()
            except Exception as exc:
                logger.exception('托盘操作失败 command=%s', command.name)
                self._publish_information(f'托盘操作失败：{exc}')
                return
            if isinstance(result, TrayActionResult):
                self._publish_information(result.message)
                if command == TrayCommand.TOGGLE_AUTOSTART:
                    self._publish_event(EventType.AUTOSTART_STATUS_CHANGE, {
                        'enabled': bool(result.enabled),
                        'source': 'tray_menu',
                    })

        future.add_done_callback(complete)

    def _on_tray_command(self, command: TrayCommand, checked: bool | None = None) -> None:
        try:
            command = TrayCommand(command)
        except (TypeError, ValueError):
            logger.warning('忽略未知托盘命令: %r', command)
            return
        if command == TrayCommand.OPEN_CMD:
            self._publish_event(EventType.UI_OPEN_CMD_WINDOW, {'entity': None})
        elif command == TrayCommand.OPEN_SETTINGS:
            try:
                self._application_ui.open_settings()
            except Exception as exc:
                logger.exception('打开控制面板失败')
                self._publish_information(f'打开控制面板失败：{exc}')
        elif command == TrayCommand.TOGGLE_GAME_MODE:
            target = bool(checked) if checked is not None else not self._tray_menu_state.game_mode_enabled
            self._publish_event(
                EventType.GAME_MODE_SET if target else EventType.GAME_MODE_EXIT,
                {'source': 'tray_menu'},
            )
        elif command == TrayCommand.TOGGLE_CLICKTHROUGH:
            target = bool(checked) if checked is not None else not self._tray_menu_state.clickthrough_enabled
            self._publish_event(EventType.UI_CLICKTHROUGH_TOGGLE, {'enabled': target})
            self._publish_information('鼠标穿透已开启' if target else '鼠标穿透已关闭')
        elif command == TrayCommand.TOGGLE_AUTOSTART:
            target = bool(checked) if checked is not None else not self._tray_menu_state.autostart_enabled
            self._submit_tray_action(command, lambda: set_autostart_enabled(target))
        elif command == TrayCommand.CLEANUP_DESKTOP:
            self._publish_event(EventType.INPUT_HASH, {'text': '清理'})
        elif command == TrayCommand.CLEANUP_CACHE:
            self._submit_tray_action(command, cleanup_music_cache)
        elif command == TrayCommand.CLEANUP_HISTORY:
            self._submit_tray_action(command, cleanup_music_history)
        elif command == TrayCommand.OPEN_AUTHOR_PAGE:
            self._submit_tray_action(command, open_author_page)

    def _on_app_quit(self, event: Event):
        """统一接管 APP_QUIT，避免组件直接强退 Qt 事件循环。"""
        event.mark_handled()
        data = event.data or {}
        if data.get('restart'):
            self.request_restart()
            return
        self.request_exit(int(data.get('exit_code', 0)))

    @property
    def restart_requested(self) -> bool:
        return self._restart_requested

    def request_restart(self):
        """先创建独立重启 helper，再通过正常退出链路完成清理。"""
        if not self._restart_helper_started:
            try:
                _launch_current_application()
            except Exception as exc:
                logger.error('创建重启 helper 失败，取消退出: %s', exc)
                return False
            self._restart_helper_started = True
        self._restart_requested = True
        self.request_exit(0)
        return True

    def start(self):
        """启动状态 - 初始化应用程序"""
        # 切换到项目根目录
        if getattr(sys, 'frozen', False):
            script_dir = os.path.dirname(sys.executable)
        else:
            # 获取项目根目录（向上两级，从 lib/script 到根目录）
            script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        os.chdir(script_dir)
        self._script_dir = script_dir

        # ── 初始化日志系统（最早执行，确保捕获全部输出）──────────────
        # initialize 内部会自动清理旧日志，只保留最新 5 个
        initialize_app_logger(script_dir)
        if self._backend_selection.fallback_used:
            logger.warning(
                "请求的渲染后端 %s 不可用，已回退到 %s: %s",
                self._backend_selection.requested_backend or "<empty>",
                self._backend_selection.active_backend,
                self._backend_selection.reason,
            )
        elif self._backend_selection.experimental:
            logger.warning(
                "实验性渲染后端已启用: %s；可能存在兼容性、性能或设备相关问题",
                self._backend_selection.active_backend,
            )
        else:
            logger.info("渲染后端已启用: %s", self._backend_selection.active_backend)
        _new_log_startup_hardware_info(logger, DRAW)

        # ── 检查并创建桌面快捷方式（日志初始化后执行，便于记录错误）────
        _new_ensure_desktop_shortcut(script_dir)

        logger.info('工作目录: %s', script_dir)

        # 创建桌面应用（需要在发布事件前创建，以便运行时调度工作）
        self._app = self._application_runtime.create_application(logger, sys.argv)
        self._application_runtime.connect_exit_acknowledged(
            self._app,
            self._on_runtime_exit_acknowledged,
        )

        self._application_ui.prepare_application(self._app)

        # 加载 GIF
        loader = GifLoader(GIF_FILES)
        self._gifs = loader.load_all()

        # 粒子覆盖层（全局单例）
        self._particles = self._particle_overlay_factory()
        self._effects = self._effect_overlay_factory()

        # 发布预启动事件，触发初始化流程
        self._publish_event(EventType.APP_PRE_START, {
            'working_dir': script_dir
        })

    def run_event_loop(self):
        """运行桌面后端事件循环。"""
        return self._application_runtime.run_event_loop(self._app)

    def request_exit(self, exit_code: int = 0):
        self._exit_requested = True

        if self._exit_in_progress or self._exit_completed:
            if exit_code and self._exit_code == 0:
                self._exit_code = exit_code
            return

        self._exit_code = exit_code
        self._exit_in_progress = True
        logger.info('收到退出请求，开始分阶段关闭组件')
        self._application_ui.begin_shutdown()
        if self._particles is not None:
            try:
                self._particles.flush_immediately()
            except Exception:
                pass
        if self._effects is not None:
            try:
                self._effects.flush_immediately()
            except Exception:
                pass
        self._process_pending_events()
        if self._tray_host is not None:
            try:
                self._tray_host.begin_shutdown()
            except Exception:
                pass

        if self._app is None:
            self._perform_component_cleanup()
            self._exit_completed = True
            return

        # 退出动画要尽早拉起，避免被后续耗时清理/强退兜底抢先中断。
        self._shutdown_play_exit_animation()
        self._arm_force_quit_fallback()
        self._shutdown_steps = [
            ('stop_primary_windows', self._shutdown_stop_primary_windows, 20),
            ('cleanup_runtime_services', self._shutdown_cleanup_runtime_services, 30),
            ('cleanup_visual_components', self._shutdown_cleanup_visual_components, 20),
            ('quit_application', self._shutdown_quit_application, 0),
        ]
        self._shutdown_step_index = 0
        self._application_runtime.schedule_once(0, self._run_next_shutdown_step)

    def _run_next_shutdown_step(self):
        if self._exit_completed or self._shutdown_step_index >= len(self._shutdown_steps):
            return

        step_index = self._shutdown_step_index
        step_name, step_func, delay_ms = self._shutdown_steps[step_index]
        self._shutdown_step_index += 1

        logger.info('退出阶段 %s/%s: %s', step_index + 1, len(self._shutdown_steps), step_name)
        try:
            step_func()
        except Exception:
            import traceback
            logger.error('退出阶段 %s 执行失败:\n%s', step_name, traceback.format_exc())

        if not self._exit_completed and self._shutdown_step_index < len(self._shutdown_steps):
            self._application_runtime.schedule_once(delay_ms, self._run_next_shutdown_step)

    def _process_pending_events(self):
        if self._app is None:
            return
        try:
            self._application_runtime.process_events(self._app)
        except Exception:
            pass

    def _arm_force_quit_fallback(self):
        if self._app is None or self._shutdown_force_quit_armed:
            return
        self._shutdown_force_quit_armed = True
        timer = threading.Timer(
            _SHUTDOWN_FORCE_TIMEOUT_MS / 1000.0,
            self._force_quit_if_still_pending,
        )
        timer.name = 'application-shutdown-watchdog'
        timer.daemon = True
        self._shutdown_force_timer = timer
        timer.start()

    def _cancel_force_quit_fallback(self):
        timer = self._shutdown_force_timer
        self._shutdown_force_timer = None
        self._shutdown_force_quit_armed = False
        if timer is not None:
            timer.cancel()

    def _force_quit_if_still_pending(self):
        if self._shutdown_clean_exit_confirmed:
            return
        logger.critical('优雅退出超过 %d ms，执行最终强退兜底', _SHUTDOWN_FORCE_TIMEOUT_MS)
        self._shutdown_force_quit_application()

    def _shutdown_stop_primary_windows(self):
        if self._tray_host:
            self._tray_host.disconnect_quit_requested(self._on_tray_quit)
            self._tray_host.disconnect_announcement_requested(self._on_tray_announcement)
            self._tray_host.disconnect_command_requested(self._on_tray_command)

        if self._pet:
            try:
                self._pet.shutdown_host()
            except Exception:
                pass

            self._pet = None

        self._process_pending_events()

    def _shutdown_cleanup_runtime_services(self):
        self._perform_component_cleanup(skip_visual_cleanup=True)
        self._process_pending_events()

    def _shutdown_cleanup_visual_components(self):
        self._cleanup_visual_components()
        self._process_pending_events()

    def _shutdown_play_exit_animation(self):
        self._publish_app_exit_once()

    def _publish_app_exit_once(self):
        if self._app_exit_event_published:
            return
        self._app_exit_event_published = True
        self._publish_event(EventType.APP_EXIT, {
            'exit_code': self._exit_code,
        })

    def _shutdown_quit_application(self):
        if not self._app:
            return
        self._runtime_exit_requested = True
        self._application_runtime.schedule_once(
            _SHUTDOWN_QUIT_RETRY_MS,
            self._retry_runtime_exit,
        )
        try:
            self._application_runtime.request_exit(self._app, self._exit_code)
        except Exception:
            import traceback
            logger.error('触发应用运行时退出失败:\n%s', traceback.format_exc())

    def _retry_runtime_exit(self):
        if self._runtime_exit_acknowledged or self._app is None:
            return
        logger.warning('应用退出请求尚未确认，关闭残留窗口并再次请求退出')
        try:
            self._application_runtime.close_all_windows(self._app)
            self._application_runtime.request_exit(self._app, self._exit_code)
        except Exception:
            import traceback
            logger.error('重试应用运行时退出失败:\n%s', traceback.format_exc())

    def _on_runtime_exit_acknowledged(self):
        self._runtime_exit_acknowledged = True
        logger.info('应用事件循环已确认退出请求')

    def _shutdown_force_quit_application(self):
        self._exit_completed = True
        os._exit(int(self._exit_code))

    @staticmethod
    def _wait_for_non_daemon_threads(timeout: float) -> list[str]:
        current = threading.current_thread()
        deadline = time.monotonic() + max(0.0, float(timeout))
        threads = [
            thread
            for thread in threading.enumerate()
            if thread is not current and thread.is_alive() and not thread.daemon
        ]
        for thread in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)
        return [thread.name for thread in threads if thread.is_alive()]

    def _perform_component_cleanup(self, skip_visual_cleanup: bool = False):
        if self._components_cleaned:
            if not skip_visual_cleanup:
                self._cleanup_visual_components()
            return

        self._application_ui.stop_runtime()

        cleanup_all_managers()
        self._managers.clear()

        if self._cleanup_handler:
            from lib.script.practical.cleanup_handler import cleanup_cleanup_handler
            cleanup_cleanup_handler()
            self._cleanup_handler = None

        cleanup_chat_handler()
        cleanup_stream_memory()
        cleanup_tool_dispatcher()
        cleanup_office_service()
        cleanup_interaction_mode_service()
        cleanup_game_mode_service()
        cleanup_ollama_manager()
        cleanup_cmd_center()
        cleanup_voice_request_handler()
        cleanup_gsvmove_service()
        cleanup_bug_tracker_service()
        cleanup_microphone_push_to_talk_manager()
        cleanup_microphone_stt_service()

        self._components_cleaned = True

        if not skip_visual_cleanup:
            self._cleanup_visual_components()

    def _cleanup_visual_components(self):
        from lib.core.draw_core import cleanup_draw_core
        from lib.core.audio_meter import cleanup_audio_meter
        from lib.core.voice.core import cleanup_voice_core

        self._application_ui.cleanup()

        if self._tray_host is not None:
            self._tray_host.cleanup()
        self._tray_host = None

        cleanup_audio_meter()
        cleanup_voice_core()
        cleanup_compute_hub()

        self._gifs = None

        if self._particles:
            try:
                self._particles.cleanup()
            except Exception:
                pass
            self._particles = None

        if self._effects:
            try:
                self._effects.cleanup()
            except Exception:
                pass
            self._effects = None

        cleanup_draw_core()

    def finalize_after_event_loop(self, exit_code: int) -> int:
        final_exit_code = self._exit_code if self._exit_requested else exit_code

        if self._exit_requested:
            if self._runtime_exit_requested and not self._runtime_exit_acknowledged:
                logger.warning('应用事件循环已返回，但未收到退出确认')
            else:
                logger.info('应用事件循环已正常返回，开始最终收尾')

        if (
            self._exit_requested
            and not self._exit_completed
            and not self._app_exit_event_published
            and self._application_ui.has_exit_animation()
        ):
            self._publish_app_exit_once()

        if not self._components_cleaned:
            logger.warning('应用事件循环已经结束，但组件仍未完全清理，开始兜底收尾')
            self._perform_component_cleanup()

        self._application_ui.finalize()
        self._cleanup_backend()
        self._unsubscribe_lifecycle_events()
        cleanup_event_center()

        self._app = None
        self._exit_completed = True
        self._exit_in_progress = False
        self._shutdown_steps = []
        self._app_exit_event_published = False

        remaining_threads = self._wait_for_non_daemon_threads(_SHUTDOWN_THREAD_DRAIN_SECONDS)
        if remaining_threads:
            logger.error('退出收尾后仍有非守护线程存活: %s', ', '.join(remaining_threads))
        else:
            self._shutdown_clean_exit_confirmed = True
            self._cancel_force_quit_fallback()
            logger.info('优雅退出完成，未发现阻塞进程结束的线程')

        if not self._logger_cleaned:
            cleanup_app_logger()
            self._logger_cleaned = True

        return final_exit_code

    def _unsubscribe_lifecycle_events(self) -> None:
        if not getattr(self, '_events_subscribed', False):
            return
        subscriptions = (
            (EventType.APP_PRE_START, self._on_pre_start),
            (EventType.APP_INIT_READY, self._on_init_ready),
            (EventType.APP_QUIT, self._on_app_quit),
            (EventType.GAME_MODE_STATUS_CHANGE, self._on_game_mode_status_change),
            (EventType.UI_CLICKTHROUGH_TOGGLE, self._on_clickthrough_status_change),
            (EventType.AUTOSTART_STATUS_CHANGE, self._on_autostart_status_change),
        )
        for event_type, callback in subscriptions:
            self._event_center.unsubscribe(event_type, callback)
        self._events_subscribed = False

    def _cleanup_backend(self) -> None:
        if self._backend_cleaned:
            return
        self._backend_cleaned = True
        cleanup = self._backend_cleanup
        if cleanup is None:
            return
        try:
            cleanup()
        except Exception:
            import traceback
            logger.error('桌面后端最终清理失败:\n%s', traceback.format_exc())

    def exit(self, exit_code: int = 0):
        self.request_exit(exit_code)

def main(
    backend_selection: BackendSelection | None = None,
    backend_bundle: DesktopBackendBundle | None = None,
):
    """主函数"""
    if not _new_acquire_single_instance_lock():
        try:
            _new_notify_already_running()
        finally:
            bundle = backend_bundle or get_desktop_backend_bundle()
            cleanup = None if bundle is None else bundle.cleanup
            if cleanup is not None:
                cleanup()
        return

    app_state = None
    exit_code = -1
    try:
        app_state = ApplicationState(
            backend_selection=backend_selection,
            backend_bundle=backend_bundle,
        )
        # START 状态 - 发布预启动事件，开始非阻塞初始化
        app_state.start()

        # 运行桌面后端事件循环（初始化在事件回调中完成）
        exit_code = app_state.run_event_loop()

        # EXIT 状态
        exit_code = app_state.finalize_after_event_loop(exit_code)
    except Exception:
        import traceback
        logger.error('程序运行出错:\n%s', traceback.format_exc())

        if app_state is not None:
            try:
                app_state.request_exit(-1)
                exit_code = app_state.finalize_after_event_loop(-1)
            except Exception:
                logger.error('异常启动后的收尾失败:\n%s', traceback.format_exc())
                exit_code = -1
        else:
            bundle = backend_bundle or get_desktop_backend_bundle()
            cleanup = None if bundle is None else bundle.cleanup
            if cleanup is not None:
                try:
                    cleanup()
                except Exception:
                    logger.error('应用状态创建失败后的后端清理失败:\n%s', traceback.format_exc())
            cleanup_event_center()
    finally:
        if app_state is not None:
            app_state._cleanup_backend()
        _new_release_single_instance_lock()

    sys.exit(exit_code)

if __name__ == '__main__':
    main()
