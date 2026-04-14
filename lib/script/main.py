"""主程序入口模块 - 使用动态发现机制初始化模块"""
import sys
import os

from PyQt5.QtCore import QTimer

from config.config import GIF_FILES, DRAW, ANIMATION
from lib.core.qt_gif_loader import GifLoader
from lib.core.qt_particle_system import ParticleOverlay
from lib.core.pet_window import PetWindow
from lib.core.event.center import get_event_center, EventType, Event, cleanup_event_center
from lib.script.SEanima.animation import get_start_exit_animation, cleanup_start_exit_animation
from lib.core.logger import initialize as initialize_app_logger, cleanup as cleanup_app_logger, get_logger
from lib.core.cmd_center import get_cmd_center, cleanup_cmd_center
from lib.script.ui.cmd_window import get_cmd_window, cleanup_cmd_window
from lib.script.chat.ollama import get_ollama_manager, cleanup_ollama_manager
from lib.script.chat.handler import get_chat_handler, cleanup_chat_handler
from lib.script.chat.memory import get_stream_memory, cleanup_stream_memory
from lib.script.tool_dispatcher import get_tool_dispatcher, cleanup_tool_dispatcher
from lib.script.gsvmove import get_gsvmove_service, cleanup_gsvmove_service
from lib.script.yuanbao_free_api import get_yuanbao_free_api_service, cleanup_yuanbao_free_api_service
from lib.script.microphone_stt import (
    cleanup_microphone_push_to_talk_manager,
    cleanup_microphone_stt_service,
    get_microphone_push_to_talk_manager,
    get_microphone_stt_service,
)
from lib.script.voice.handler import get_voice_request_handler, cleanup_voice_request_handler
from lib.core.plugin_registry import (
    discover_all, init_all_managers, cleanup_all_managers, get_manager
)
from lib.core.tray_icon import get_tray_icon, cleanup_tray_icon
from lib.script.ui.shutdown import hide_all_runtime_ui, cleanup_all_runtime_ui
from lib.script.app.single_instance import (
    acquire_single_instance_lock as _new_acquire_single_instance_lock,
    notify_already_running as _new_notify_already_running,
    release_single_instance_lock as _new_release_single_instance_lock,
)
from lib.script.app.startup_probe import log_startup_hardware_info as _new_log_startup_hardware_info
from lib.script.app.desktop_shortcut import ensure_desktop_shortcut as _new_ensure_desktop_shortcut
from lib.script.app.qt_runtime import create_qt_application as _new_create_qt_application

logger = get_logger(__name__)

class ApplicationState:
    """应用程序状态管理"""

    def __init__(self):
        self._event_center = get_event_center()
        self._app = None
        self._pet = None
        self._gifs = None
        self._particles = None
        self._animation = get_start_exit_animation()
        # 管理器实例字典（由动态发现机制填充）
        self._managers = {}
        # 清理命令处理器
        self._cleanup_handler = None
        # 工具调度器
        self._tool_dispatcher = None
        # 工作目录
        self._script_dir = None
        # 初始化完成标志
        self._init_ready = False
        # 系统托盘图标
        self._tray_icon = None
        self._exit_requested = False
        self._exit_in_progress = False
        self._exit_completed = False
        self._components_cleaned = False
        self._logger_cleaned = False
        self._exit_code = 0
        self._shutdown_steps = []
        self._shutdown_step_index = 0

        # 音频核心在事件中心初始化后立即创建，以便订阅 APP_PRE_START 完成 MCI 预热
        from lib.core.voice.core import get_voice_core
        self._voice = get_voice_core()
        # GSVmove 文本转语音桥接：按配置决定是否在预启动阶段后台拉起本地 TTS 服务
        self._gsvmove = get_gsvmove_service()
        self._yuanbao_free_api = get_yuanbao_free_api_service()
        self._microphone_stt = get_microphone_stt_service()
        self._microphone_push_to_talk = get_microphone_push_to_talk_manager()
        # 语音抽象层：接收 VOICE_REQUEST 并路由到底层声音系统
        self._voice_script = get_voice_request_handler()
        # CmdCenter 在事件中心初始化后立即注册，确保捕获所有输入事件
        self._cmd_center = get_cmd_center()

        # OllamaManager 需在 APP_PRE_START 前注册（订阅该事件以尝试启动服务）
        # ChatHandler 在其内部初始化 OllamaManager，顺序在 CmdCenter 之后即可
        self._chat_handler = get_chat_handler()
        self._stream_memory = get_stream_memory()

        # 订阅事件
        self._event_center.subscribe(EventType.APP_PRE_START, self._on_pre_start)
        self._event_center.subscribe(EventType.APP_INIT_READY, self._on_init_ready)
        self._event_center.subscribe(EventType.APP_QUIT, self._on_app_quit)

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
        QTimer.singleShot(startup_delay_ms, self._on_init_timer)

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
        self._pet = PetWindow(self._gifs, self._particles)

        # ── 使用动态发现机制初始化所有管理器 ────────────────────────────
        # 管理器会在模块加载时自动注册，这里统一初始化
        self._managers = init_all_managers(self._pet)

        # ── 初始化清理命令处理器 ────────────────────────────────────────
        from lib.script.practical.cleanup_handler import get_cleanup_handler
        self._cleanup_handler = get_cleanup_handler()

        # ── 初始化工具调度器 ────────────────────────────────────────────
        self._tool_dispatcher = get_tool_dispatcher()

        # ── 初始化说明书（鼠标悬停提示面板）────────────────────────────
        from lib.script.ui.tooltip_panel import init_tooltip_panel
        init_tooltip_panel()

        # ── 初始化CMD窗口 ──────────────────────────────────────────────
        get_cmd_window()

        # 发布main事件，进入main状态
        self._publish_event(EventType.APP_MAIN, {
            'gifs_loaded': len(self._gifs)
        })

        # 初始化系统托盘图标
        self._tray_icon = get_tray_icon()
        try:
            self._tray_icon.quit_requested.disconnect(self._on_tray_quit)
        except (TypeError, RuntimeError):
            pass
        self._tray_icon.quit_requested.connect(self._on_tray_quit)

        if self._tray_icon.initialize():
            logger.info('系统托盘图标初始化成功')
        else:
            logger.warning('系统托盘图标初始化未立即成功，已转入后台重试')

        logger.info('桌面宠物启动成功！')
        logger.info('  左键点击 → 随机动作 + 粒子特效')
        logger.info('  右键点击 → 打开/关闭 CMD 输入框')
        logger.info('  鼠标悬停 → 显示关闭按钮（右上角）')
        logger.info('  系统托盘 → 右键菜单退出')

    def _on_tray_quit(self):
        """托盘菜单退出回调"""
        # 调用 exit 方法进行正常退出流程
        self.request_exit(0)

    def _on_app_quit(self, event: Event):
        """缁熶竴鎺ョ APP_QUIT锛岄伩鍏嶇洿鎺ュ己閫€ Qt 浜嬩欢寰幆銆?"""
        event.mark_handled()
        self.request_exit(int((event.data or {}).get('exit_code', 0)))

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
        _new_log_startup_hardware_info(logger, DRAW)

        # ── 检查并创建桌面快捷方式（日志初始化后执行，便于记录错误）────
        _new_ensure_desktop_shortcut(script_dir)

        logger.info('工作目录: %s', script_dir)

        # 创建Qt应用（需要在发布事件前创建，以便 QTimer 工作）
        self._app = _new_create_qt_application(logger, sys.argv)

        # GSVmove 按配置尽早拉起，以便与后续预启动延时并行完成服务启动/预热。
        if self._gsvmove is not None and self._gsvmove.auto_start_enabled():
            self._gsvmove.kickoff_prestart()

        # 初始化字体配置（DPI 缩放，需在 QApplication 创建后调用）
        from config.font_config import init_font_config
        init_font_config()

        # 加载 GIF
        loader = GifLoader(GIF_FILES)
        self._gifs = loader.load_all()

        # 粒子覆盖层（全局单例）
        self._particles = ParticleOverlay()

        # 发布预启动事件，触发初始化流程
        self._publish_event(EventType.APP_PRE_START, {
            'working_dir': script_dir
        })

    def run_event_loop(self):
        """运行 Qt 事件循环"""
        return self._app.exec_()

    def request_exit(self, exit_code: int = 0):
        self._exit_requested = True

        if self._exit_in_progress or self._exit_completed:
            if exit_code and self._exit_code == 0:
                self._exit_code = exit_code
            return

        self._exit_code = exit_code
        self._exit_in_progress = True
        logger.info('收到退出请求，开始分阶段关闭组件')
        hide_all_runtime_ui()
        if self._tray_icon is not None:
            try:
                self._tray_icon.begin_shutdown()
            except Exception:
                pass

        if self._app is None:
            self._perform_component_cleanup()
            self._exit_completed = True
            return

        self._shutdown_steps = [
            ('cleanup_runtime_services', self._shutdown_cleanup_runtime_services, 30),
            ('play_exit_animation', self._shutdown_play_exit_animation, 80),
            ('force_quit_application', self._shutdown_force_quit_application, 0),
        ]
        self._shutdown_step_index = 0
        QTimer.singleShot(0, self._run_next_shutdown_step)

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
            QTimer.singleShot(delay_ms, self._run_next_shutdown_step)

    def _process_pending_events(self):
        if self._app is None:
            return
        try:
            self._app.processEvents()
        except Exception:
            pass

    def _shutdown_stop_primary_windows(self):
        if self._tray_icon:
            try:
                self._tray_icon.quit_requested.disconnect(self._on_tray_quit)
            except (TypeError, RuntimeError):
                pass

        if self._pet:
            timing_manager = getattr(self._pet, '_timing_manager', None)
            if timing_manager:
                timing_manager.stop()
                timing_manager.clear_all()

            try:
                self._pet.close()
            except Exception:
                pass

            try:
                self._pet.deleteLater()
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
        if self._animation is not None:
            try:
                self._animation.play_exit()
            except Exception:
                import traceback
                logger.error('启动退出动画失败:\n%s', traceback.format_exc())

    def _shutdown_quit_application(self):
        if self._app:
            self._app.quit()
        self._exit_completed = True

    def _shutdown_force_quit_application(self):
        self._exit_completed = True
        os._exit(int(self._exit_code))

    def _perform_component_cleanup(self, skip_visual_cleanup: bool = False):
        if self._components_cleaned:
            if not skip_visual_cleanup:
                self._cleanup_visual_components()
            return

        cleanup_all_managers()
        self._managers.clear()

        if self._cleanup_handler:
            from lib.script.practical.cleanup_handler import cleanup_cleanup_handler
            cleanup_cleanup_handler()
            self._cleanup_handler = None

        cleanup_chat_handler()
        cleanup_stream_memory()
        cleanup_tool_dispatcher()
        cleanup_ollama_manager()
        cleanup_cmd_center()
        cleanup_cmd_window()
        cleanup_voice_request_handler()
        cleanup_gsvmove_service()
        cleanup_yuanbao_free_api_service()
        cleanup_microphone_push_to_talk_manager()
        cleanup_microphone_stt_service()

        self._components_cleaned = True

        if not skip_visual_cleanup:
            self._cleanup_visual_components()

    def _cleanup_visual_components(self):
        from lib.core.draw_core import cleanup_draw_core
        from lib.core.voice.core import cleanup_voice_core

        cleanup_all_runtime_ui()

        cleanup_tray_icon()
        self._tray_icon = None

        cleanup_voice_core()

        self._gifs = None

        if self._particles:
            try:
                self._particles.cleanup()
                self._particles.close()
                self._particles.deleteLater()
            except Exception:
                pass
            self._particles = None

        cleanup_draw_core()

    def finalize_after_event_loop(self, exit_code: int) -> int:
        final_exit_code = self._exit_code if self._exit_requested else exit_code

        if self._exit_requested and not self._exit_completed and self._animation is not None:
            try:
                self._animation.play_exit()
            except Exception:
                pass

        if not self._components_cleaned:
            logger.warning('Qt 事件循环已经结束，但组件仍未完全清理，开始兜底收尾')
            self._perform_component_cleanup()

        cleanup_start_exit_animation()
        cleanup_event_center()

        self._app = None
        self._exit_completed = True

        if not self._logger_cleaned:
            cleanup_app_logger()
            self._logger_cleaned = True

        return final_exit_code

    def exit(self, exit_code: int = 0):
        self.request_exit(exit_code)

def main():
    """主函数"""
    if not _new_acquire_single_instance_lock():
        _new_notify_already_running()
        return

    app_state = ApplicationState()

    try:
        # START 状态 - 发布预启动事件，开始非阻塞初始化
        app_state.start()

        # 运行 Qt 事件循环（初始化在事件回调中完成）
        exit_code = app_state.run_event_loop()

        # EXIT 状态
        exit_code = app_state.finalize_after_event_loop(exit_code)

        sys.exit(exit_code)
    except Exception as e:
        import traceback
        logger.error('程序运行出错:\n%s', traceback.format_exc())

        # 即使出错也要发布退出事件
        app_state.request_exit(-1)
        app_state.finalize_after_event_loop(-1)
        input('按回车键退出...')
    finally:
        _new_release_single_instance_lock()

if __name__ == '__main__':
    main()
