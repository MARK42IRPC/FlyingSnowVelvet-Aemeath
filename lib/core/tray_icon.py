"""系统托盘图标模块"""
import os
import sys
import uuid
import webbrowser
from pathlib import Path
from PyQt5.QtWidgets import (
    QSystemTrayIcon,
    QAction,
    QApplication,
    QStyle,
)
from PyQt5.QtGui import QIcon, QCursor, QGuiApplication
from PyQt5.QtCore import QObject, pyqtSignal, QTimer, QPoint

from lib.core.logger import get_logger
from lib.core.event.center import get_event_center, EventType, Event
from lib.script.ui.tray_menu import TrayContextMenu
from config.config import CLOUD_MUSIC
from config.tooltip_config import TOOLTIPS

_logger = get_logger(__name__)

# 开机启动注册表路径
AUTOSTART_REG_PATH = r'Software\Microsoft\Windows\CurrentVersion\Run'
AUTOSTART_KEY_NAME = 'FlyingSnowflake'

# 托盘图标唯一标识符（用于 Windows 持久化设置）
TRAY_ICON_GUID = uuid.UUID('{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}')


class TrayIcon(QObject):
    """系统托盘图标管理器"""

    RETRY_INTERVAL_MS = 1500
    MAX_RETRY_COUNT = 40
    _ICON_TEST_SIZES = ((16, 16), (20, 20), (24, 24), (32, 32))

    # 退出信号
    quit_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._event_center = get_event_center()
        self._tray_icon = None
        self._menu = None
        self._autostart_action = None
        self._clickthrough_action = None
        self._clickthrough_enabled = False
        self._clickthrough_status_subscribed = False
        self._ai_settings_panel = None
        self._icon = None
        self._icon_path = None
        self._initialized = False
        self._retry_count = 0
        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(self.RETRY_INTERVAL_MS)
        self._retry_timer.timeout.connect(self._on_retry_timeout)

    def initialize(self, icon_path: str = None) -> bool:
        """
        初始化系统托盘图标

        Args:
            icon_path: 图标文件路径，如果为 None 则使用默认路径

        Returns:
            是否初始化成功
        """
        self._subscribe_clickthrough_events()

        if self._initialized:
            return True

        app = QApplication.instance()
        if app is None:
            _logger.error('QApplication 实例不存在，无法创建托盘图标')
            return False

        if icon_path is None:
            icon_path = self._resolve_default_icon_path()
        self._icon_path = icon_path

        if self._try_create_tray_icon():
            return True

        self._start_retry()
        return False

    def _resolve_default_icon_path(self) -> str:
        """获取默认托盘图标路径"""
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(project_root, 'resc', 'icon.ico')

    def _start_retry(self):
        """开始后台重试创建托盘图标"""
        if self._retry_timer.isActive():
            return
        self._retry_count = 0
        self._retry_timer.start()
        _logger.warning('系统托盘暂不可用，已启动后台重试')

    def _stop_retry(self):
        """停止后台重试"""
        if self._retry_timer.isActive():
            self._retry_timer.stop()
        self._retry_count = 0

    def _on_retry_timeout(self):
        """重试定时器回调"""
        if self._initialized:
            self._stop_retry()
            return

        self._retry_count += 1
        if self._retry_count > self.MAX_RETRY_COUNT:
            _logger.error('系统托盘创建重试超时，已放弃（%s 次）', self.MAX_RETRY_COUNT)
            self._stop_retry()
            return

        if self._try_create_tray_icon():
            _logger.info('系统托盘在第 %s 次重试后创建成功', self._retry_count)
            self._stop_retry()

    def _try_create_tray_icon(self) -> bool:
        """执行一次托盘图标创建尝试"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return False

        self._teardown_tray_icon()

        # 创建托盘对象
        self._tray_icon = QSystemTrayIcon(self)
        self._create_menu()
        self._tray_icon.setContextMenu(None)

        # 加载图标
        icon = self._load_icon(self._icon_path)
        if not self._is_icon_renderable(icon):
            if icon is not None and not icon.isNull():
                _logger.warning('自定义托盘图标不可渲染（常见于 ICO 解码异常），将回退默认图标')
            icon = self._get_default_icon()
        if not self._is_icon_renderable(icon):
            _logger.warning('托盘图标加载失败，无法创建系统托盘')
            self._teardown_tray_icon()
            return False

        self._icon = icon
        self._tray_icon.setIcon(self._icon)
        self._tray_icon.setToolTip('飞行雪绒')

        try:
            self._tray_icon.messageClicked.connect(self._on_message_clicked)
        except TypeError:
            # 已连接时忽略，避免重复连接报错
            pass
        try:
            self._tray_icon.activated.connect(self._on_tray_activated)
        except TypeError:
            # 已连接时忽略，避免重复连接报错
            pass

        # 不强依赖 isVisible() 立即返回值，某些系统上 show 后可见状态存在延迟
        self._tray_icon.show()

        self._initialized = True
        _logger.info('系统托盘图标已创建并显示')
        return True

    def _teardown_tray_icon(self):
        """销毁旧托盘对象（用于重建或 cleanup）"""
        if self._tray_icon is not None:
            try:
                self._tray_icon.messageClicked.disconnect(self._on_message_clicked)
            except (TypeError, RuntimeError):
                pass
            try:
                self._tray_icon.activated.disconnect(self._on_tray_activated)
            except (TypeError, RuntimeError):
                pass
            self._tray_icon.hide()
            self._tray_icon.setContextMenu(None)
            self._tray_icon.deleteLater()
            self._tray_icon = None

    def _load_icon(self, icon_path: str) -> QIcon:
        """加载图标文件"""
        if not icon_path:
            return None

        if not os.path.exists(icon_path):
            _logger.warning('图标文件不存在: %s', icon_path)
            return None

        icon = QIcon(icon_path)
        return icon

    def _is_icon_renderable(self, icon: QIcon) -> bool:
        """
        检查图标是否可渲染为托盘常用小尺寸。

        仅检查 `icon.isNull()` 不足以覆盖所有环境：部分机器会出现
        ICO 对象不为空，但 16x16/20x20 像素图取不到的情况。
        """
        if icon is None or icon.isNull():
            return False
        for w, h in self._ICON_TEST_SIZES:
            pixmap = icon.pixmap(w, h)
            if pixmap is not None and not pixmap.isNull():
                return True
        return False

    def _get_default_icon(self) -> QIcon:
        """获取系统默认图标"""
        # 尝试使用应用程序图标
        app = QApplication.instance()
        if app and hasattr(app, 'windowIcon'):
            icon = app.windowIcon()
            if not icon.isNull():
                return icon

        # 使用 Qt 内置图标
        app = QApplication.instance()
        if app:
            return app.style().standardIcon(QStyle.SP_ComputerIcon)

        return None

    def _create_menu(self):
        """创建托盘菜单"""
        if self._menu is not None:
            self._menu.clear()
            self._menu.deleteLater()

        self._menu = TrayContextMenu()

        # ── 常用入口 ───────────────────────────────────────────────
        # 控制面板动作
        ai_settings_action = QAction('控制面板', self._menu)
        ai_settings_action.setToolTip(TOOLTIPS['tray_ai_settings'])
        ai_settings_action.setStatusTip(TOOLTIPS['tray_ai_settings'])
        ai_settings_action.triggered.connect(self._on_ai_settings)
        self._menu.addAction(ai_settings_action)

        # CMD窗口动作
        cmd_window_action = QAction('CMD终端', self._menu)
        cmd_window_action.setToolTip(TOOLTIPS['tray_cmd_window'])
        cmd_window_action.setStatusTip(TOOLTIPS['tray_cmd_window'])
        cmd_window_action.triggered.connect(self._on_cmd_window)
        self._menu.addAction(cmd_window_action)

        # 鼠标穿透动作（可勾选）
        self._clickthrough_action = QAction('鼠标穿透', self._menu)
        self._clickthrough_action.setCheckable(True)
        self._set_clickthrough_action_checked(self._clickthrough_enabled)
        self._clickthrough_action.setToolTip(TOOLTIPS['tray_clickthrough'])
        self._clickthrough_action.setStatusTip(TOOLTIPS['tray_clickthrough'])
        self._clickthrough_action.triggered.connect(self._on_toggle_clickthrough)
        self._menu.addAction(self._clickthrough_action)

        self._menu.addSeparator()

        # ── 启动配置 ───────────────────────────────────────────────
        # 开机启动动作
        self._autostart_action = QAction('开机启动', self._menu)
        self._autostart_action.setCheckable(True)
        autostart_enabled = self._is_autostart_enabled()
        self._set_autostart_action_checked(autostart_enabled)
        self._autostart_action.setToolTip(TOOLTIPS['tray_autostart'])
        self._autostart_action.setStatusTip(TOOLTIPS['tray_autostart'])
        self._autostart_action.triggered.connect(self._on_toggle_autostart)
        self._menu.addAction(self._autostart_action)
        self._publish_autostart_status(autostart_enabled, source='tray_init')

        self._menu.addSeparator()

        # ── 清理维护 ───────────────────────────────────────────────
        # 清理桌面动作
        cleanup_action = QAction('清理桌面', self._menu)
        cleanup_action.setToolTip(TOOLTIPS['tray_cleanup_desktop'])
        cleanup_action.setStatusTip(TOOLTIPS['tray_cleanup_desktop'])
        cleanup_action.triggered.connect(self._on_cleanup_desktop)
        self._menu.addAction(cleanup_action)

        # 清理缓存动作
        cleanup_cache_action = QAction('清理缓存', self._menu)
        cleanup_cache_action.setToolTip(TOOLTIPS['tray_cleanup_cache'])
        cleanup_cache_action.setStatusTip(TOOLTIPS['tray_cleanup_cache'])
        cleanup_cache_action.triggered.connect(self._on_cleanup_cache)
        self._menu.addAction(cleanup_cache_action)

        # 清理历史动作
        cleanup_history_action = QAction('清理历史', self._menu)
        cleanup_history_action.setToolTip(TOOLTIPS['tray_cleanup_history'])
        cleanup_history_action.setStatusTip(TOOLTIPS['tray_cleanup_history'])
        cleanup_history_action.triggered.connect(self._on_cleanup_history)
        self._menu.addAction(cleanup_history_action)

        self._menu.addSeparator()

        # ── 其它 ───────────────────────────────────────────────────
        # 关注作者动作
        follow_author_action = QAction('关注作者', self._menu)
        follow_author_action.setToolTip(TOOLTIPS['tray_follow_author'])
        follow_author_action.setStatusTip(TOOLTIPS['tray_follow_author'])
        follow_author_action.triggered.connect(self._on_follow_author)
        self._menu.addAction(follow_author_action)

        # 分隔线
        self._menu.addSeparator()

        # 退出动作
        quit_action = QAction('退出程序', self._menu)
        quit_action.setToolTip(TOOLTIPS['tray_quit'])
        quit_action.setStatusTip(TOOLTIPS['tray_quit'])
        quit_action.triggered.connect(self._on_quit)
        self._menu.addAction(quit_action)

    def _on_message_clicked(self):
        """消息点击回调"""
        _logger.debug('托盘消息被点击')

    def _on_tray_activated(self, reason):
        """处理托盘图标激活事件。"""
        if reason == QSystemTrayIcon.Context:
            self._show_menu_above_cursor()
            return
        if reason == QSystemTrayIcon.Trigger:
            self._on_ai_settings()

    def _show_menu_above_cursor(self):
        """在鼠标位置上方弹出托盘菜单，避免向下被屏幕遮挡。"""
        if self._menu is None:
            return

        self._menu.ensurePolished()
        hint = self._menu.sizeHint()
        menu_w = max(1, hint.width())
        menu_h = max(1, hint.height())

        cursor_pos = QCursor.pos()
        x = cursor_pos.x()
        y = cursor_pos.y() - menu_h

        screen = QGuiApplication.screenAt(cursor_pos)
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            x = max(geo.left(), min(x, geo.right() - menu_w + 1))
            y = max(geo.top(), min(y, geo.bottom() - menu_h + 1))

        self._menu.popup(QPoint(x, y))

    def _on_quit(self):
        """处理退出动作"""
        _logger.info('用户通过托盘菜单请求退出')
        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': '正在退出程序',
            'min': 0,
            'max': 60,
        }))
        QTimer.singleShot(120, self.quit_requested.emit)

    def begin_shutdown(self):
        """立即隐藏托盘相关 UI，完整释放仍由 cleanup() 负责。"""
        self._stop_retry()
        if self._menu is not None:
            try:
                self._menu.hide()
            except Exception:
                pass
        if self._ai_settings_panel is not None:
            try:
                self._ai_settings_panel.hide()
            except Exception:
                pass
        if self._tray_icon is not None:
            try:
                self._tray_icon.hide()
            except Exception:
                pass

    def _on_cleanup_desktop(self):
        """处理清理桌面动作"""
        self._event_center.publish(Event(EventType.INPUT_HASH, {
            'text': '清理',
        }))

    def _on_cleanup_history(self):
        """处理清理历史动作：清空所有平台历史与登录数据，不清理缓存。"""
        try:
            from lib.script.cloudmusic import clear_all_history_and_login_data

            result = clear_all_history_and_login_data()
            history_items = int(result.get('history_items') or 0)
            deleted_login_files = int(result.get('deleted_login_files') or 0)
            logged_in_providers = int(result.get('logged_in_providers') or 0)
            total_failed = (
                int(result.get('history_failures') or 0)
                + int(result.get('failed_login_files') or 0)
                + int(result.get('login_provider_failures') or 0)
            )
            cleared_login = deleted_login_files > 0 or logged_in_providers > 0
            if history_items == 0 and not cleared_login and total_failed == 0:
                message = '暂无音乐历史或登录数据需要清理'
            else:
                parts: list[str] = []
                if history_items > 0:
                    parts.append(f'已清空 {history_items} 条音乐历史')
                if cleared_login:
                    parts.append('已清除登录数据')
                message = '，'.join(parts) if parts else '音乐历史与登录数据已清理'
                if total_failed > 0:
                    message += f'（{total_failed} 项清理失败）'
        except Exception as e:
            _logger.error('清理音乐历史与登录数据失败: %s', e)
            message = '清理历史失败，请查看日志'

        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': message,
            'min': 0,
            'max': 60,
        }))

    def _on_cleanup_cache(self):
        """处理清理缓存动作：仅清理音乐缓存目录，不影响历史与登录数据。"""
        project_root = Path(__file__).resolve().parents[2]
        cache_root = project_root / str(CLOUD_MUSIC.get("cache_dir", "resc/user/temp") or "resc/user/temp")
        platform_names = ("netease", "qq", "kugou", "local", "other")
        platform_dirs = [cache_root / name for name in platform_names if (cache_root / name).is_dir()]

        deleted_files = 0
        failed_files = 0
        deleted_bytes = 0

        for platform_dir in platform_dirs:
            for file_path in platform_dir.rglob('*'):
                if not file_path.is_file():
                    continue
                try:
                    file_size = file_path.stat().st_size
                except OSError:
                    file_size = 0

                try:
                    file_path.unlink()
                    deleted_files += 1
                    deleted_bytes += max(0, file_size)
                except OSError as e:
                    failed_files += 1
                    _logger.warning('清理缓存失败: %s (%s)', file_path, e)

        if deleted_files == 0 and failed_files == 0:
            message = '现在很干净，无需清理缓存'
        elif deleted_files > 0:
            cleaned_mb = deleted_bytes / (1024 * 1024)
            message = f'已清理 {cleaned_mb:.2f} MB 缓存'
            if failed_files > 0:
                message += f'（{failed_files} 项清理失败）'
        else:
            message = f'缓存清理失败，{failed_files} 项被占用'

        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': message,
            'min': 0,
            'max': 60,
        }))

    def _on_ai_settings(self):
        """处理控制面板动作：打开面板并居中显示。"""
        try:
            from lib.script.ui.ai_settings_panel import AISettingsPanel

            if self._ai_settings_panel is None:
                self._ai_settings_panel = AISettingsPanel()
            self._ai_settings_panel.show_centered()
        except Exception as e:
            _logger.error('打开控制面板失败: %s', e)
            self._event_center.publish(Event(EventType.INFORMATION, {
                'text': f'打开控制面板失败: {e}',
                'min': 12,
                'max': 120,
            }))

    def _on_cmd_window(self):
        """处理CMD窗口动作：打开CMD终端窗口。"""
        try:
            from lib.script.ui.cmd_window import get_cmd_window
            cmd_window = get_cmd_window()
            # 发布打开CMD窗口事件，由事件处理器处理显示逻辑
            self._event_center.publish(Event(EventType.UI_OPEN_CMD_WINDOW, {
                'entity': None  # 从托盘打开，没有实体引用
            }))
        except Exception as e:
            _logger.error('打开CMD窗口失败: %s', e)
            self._event_center.publish(Event(EventType.INFORMATION, {
                'text': f'打开CMD窗口失败: {e}',
                'min': 12,
                'max': 120,
            }))

    def _on_toggle_clickthrough(self, checked: bool):
        """处理鼠标穿透开关动作。"""
        target = bool(checked)
        self._clickthrough_enabled = target
        self._set_clickthrough_action_checked(target)

        self._event_center.publish(Event(EventType.UI_CLICKTHROUGH_TOGGLE, {
            'enabled': target,
        }))
        self._event_center.publish(Event(EventType.INFORMATION, {
            'text': '鼠标穿透已开启' if target else '鼠标穿透已关闭',
            'min': 0,
            'max': 60,
        }))

    def _subscribe_clickthrough_events(self):
        """订阅鼠标穿透状态事件，用于同步托盘勾选状态。"""
        if self._clickthrough_status_subscribed:
            return
        self._event_center.subscribe(
            EventType.UI_CLICKTHROUGH_TOGGLE,
            self._on_clickthrough_status_change,
        )
        self._clickthrough_status_subscribed = True

    def _unsubscribe_clickthrough_events(self):
        """取消订阅鼠标穿透状态事件。"""
        if not self._clickthrough_status_subscribed:
            return
        self._event_center.unsubscribe(
            EventType.UI_CLICKTHROUGH_TOGGLE,
            self._on_clickthrough_status_change,
        )
        self._clickthrough_status_subscribed = False

    def _on_clickthrough_status_change(self, event: Event):
        """接收鼠标穿透状态变化并同步托盘动作。"""
        data = event.data if isinstance(event.data, dict) else {}
        enabled = bool(data.get('enabled', False))
        self._clickthrough_enabled = enabled
        self._set_clickthrough_action_checked(enabled)

    def _set_clickthrough_action_checked(self, enabled: bool):
        """同步托盘鼠标穿透动作的勾选状态，避免重复触发信号。"""
        if self._clickthrough_action is None:
            return
        target = bool(enabled)
        if self._clickthrough_action.isChecked() == target:
            return
        blocked = self._clickthrough_action.blockSignals(True)
        try:
            self._clickthrough_action.setChecked(target)
        finally:
            self._clickthrough_action.blockSignals(blocked)

    def _on_follow_author(self):
        """处理关注作者动作"""
        try:
            webbrowser.open('https://space.bilibili.com/486401719')
            self._event_center.publish(Event(EventType.INFORMATION, {
                'text': '已打开作者主页',
                'min': 0,
                'max': 60,
            }))
        except Exception as e:
            _logger.warning('打开作者主页失败: %s', e)
            self._event_center.publish(Event(EventType.INFORMATION, {
                'text': '打开作者主页失败',
                'min': 0,
                'max': 60,
            }))

    def _is_autostart_enabled(self) -> bool:
        """检查开机启动是否已启用"""
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_PATH, 0,
                               winreg.KEY_READ) as key:
                try:
                    value, _ = winreg.QueryValueEx(key, AUTOSTART_KEY_NAME)
                    return True
                except FileNotFoundError:
                    return False
        except Exception as e:
            _logger.warning('检查开机启动状态失败: %s', e)
            return False

    def _set_autostart_action_checked(self, enabled: bool):
        """同步托盘开机启动动作的勾选状态，避免重复触发信号。"""
        if self._autostart_action is None:
            return
        target = bool(enabled)
        if self._autostart_action.isChecked() == target:
            return
        blocked = self._autostart_action.blockSignals(True)
        try:
            self._autostart_action.setChecked(target)
        finally:
            self._autostart_action.blockSignals(blocked)

    def _publish_autostart_status(self, enabled: bool, source: str = 'unknown'):
        """广播开机启动状态，供控制面板与其他 UI 同步。"""
        self._event_center.publish(Event(EventType.AUTOSTART_STATUS_CHANGE, {
            'enabled': bool(enabled),
            'source': str(source or 'unknown'),
        }))

    def _on_toggle_autostart(self, checked: bool, source: str = 'tray_menu'):
        """切换开机启动状态"""
        target = bool(checked)
        try:
            import winreg
            if target:
                # 启用开机启动
                if getattr(sys, 'frozen', False):
                    script_dir = os.path.dirname(sys.executable)
                else:
                    script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

                bat_path = os.path.join(script_dir, '启动程序.bat')

                if not os.path.exists(bat_path):
                    _logger.warning('启动脚本不存在: %s', bat_path)
                    self._set_autostart_action_checked(False)
                    self._publish_autostart_status(False, source=source)
                    return

                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_PATH, 0,
                                   winreg.KEY_SET_VALUE) as key:
                    winreg.SetValueEx(key, AUTOSTART_KEY_NAME, 0, winreg.REG_SZ, f'"{bat_path}"')

                _logger.info('开机启动已启用')
                self._event_center.publish(Event(EventType.INFORMATION, {
                    'text': '开机启动已启用',
                    'min': 0,
                    'max': 60,
                }))
            else:
                # 禁用开机启动
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_PATH, 0,
                                   winreg.KEY_SET_VALUE) as key:
                    try:
                        winreg.DeleteValue(key, AUTOSTART_KEY_NAME)
                    except FileNotFoundError:
                        pass  # 键不存在，无需删除

                _logger.info('开机启动已禁用')
                self._event_center.publish(Event(EventType.INFORMATION, {
                    'text': '开机启动已禁用',
                    'min': 0,
                    'max': 60,
                }))
        except Exception as e:
            _logger.error('切换开机启动失败: %s', e)
        actual = bool(self._is_autostart_enabled())
        self._set_autostart_action_checked(actual)
        self._publish_autostart_status(actual, source=source)

    def show_message(self, title: str, message: str,
                     icon: QSystemTrayIcon.MessageIcon = QSystemTrayIcon.Information,
                     msecs: int = 3000):
        """
        显示托盘消息

        Args:
            title: 消息标题
            message: 消息内容
            icon: 消息图标类型
            msecs: 消息显示时间(毫秒)
        """
        if self._tray_icon and self._tray_icon.isVisible():
            self._tray_icon.showMessage(title, message, icon, msecs)

    def is_visible(self) -> bool:
        """检查托盘图标是否可见"""
        return self._tray_icon is not None and self._tray_icon.isVisible()

    def set_icon(self, icon_path: str) -> bool:
        """
        动态更换托盘图标

        Args:
            icon_path: 新图标路径

        Returns:
            是否更换成功
        """
        if not self._tray_icon:
            return False

        icon = self._load_icon(icon_path)
        if icon and not icon.isNull():
            self._tray_icon.setIcon(icon)
            return True
        return False

    def cleanup(self):
        """清理托盘图标资源"""
        self._unsubscribe_clickthrough_events()
        self._stop_retry()
        self._teardown_tray_icon()
        if self._menu:
            self._menu.clear()
            self._menu.deleteLater()
            self._menu = None
        if self._ai_settings_panel is not None:
            self._ai_settings_panel.hide()
            self._ai_settings_panel.deleteLater()
            self._ai_settings_panel = None
        self._icon = None
        self._icon_path = None
        self._autostart_action = None
        self._clickthrough_action = None
        self._clickthrough_enabled = False
        self._initialized = False
        _logger.info('系统托盘图标已清理')


# 全局单例实例
_tray_icon_instance: TrayIcon = None


def get_tray_icon() -> TrayIcon:
    """获取托盘图标单例"""
    global _tray_icon_instance
    if _tray_icon_instance is None:
        app = QApplication.instance()
        _tray_icon_instance = TrayIcon(app)
    return _tray_icon_instance


def cleanup_tray_icon():
    """清理托盘图标单例"""
    global _tray_icon_instance
    if _tray_icon_instance is not None:
        _tray_icon_instance.cleanup()
        _tray_icon_instance = None
