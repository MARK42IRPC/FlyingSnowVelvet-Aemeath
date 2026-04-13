"""CMD窗口类 - 仿Windows cmd风格，项目主题绘制，独立控制面板风格窗口

功能特性：
  - 流式输出：Popen + readline 逐行实时追加，命令不阻塞 UI
  - Braille Spinner：命令运行中滚动动画，完成后显示 ✓ / ✗
  - 关闭 / 清空按钮：标题栏右侧，符合项目风格（黑色边框）
  - 命令历史：↑↓ 键最多 50 条
  - ANSI 剥离：去除 \x1b[...m 等转义序列
  - 编码修复：chcp 65001 强制 UTF-8，errors='replace' 兜底
  - 全部使用项目字体（UI 鸿蒙 / CLR 拉海洛 / 标题拉海洛粗体）
  - 窗口边缘拖拽自由缩放
  - 滚动条仅绘制滑块，不绘制轨道背景
  - 悬浮提示使用项目 _description 属性系统
"""

import re
import os
import subprocess
import threading
import traceback
from collections import deque

from PyQt5.QtCore import Qt, QPoint, QRect, QPropertyAnimation, QEasingCurve, QEvent, QTimer
from PyQt5.QtGui import QColor, QPainter, QPen, QCursor, QTextCursor
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QTextEdit, QLineEdit, QApplication,
    QGraphicsOpacityEffect, QLabel, QHBoxLayout, QPushButton,
)

from config.config import COLORS, UI, UI_THEME
from config.font_config import get_cmd_font, get_ui_font, get_digit_font
from config.scale import scale_px, scale_style_px
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.topmost_manager import get_topmost_manager


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _hex(color: QColor) -> str:
    return color.name()


_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b.')


def _strip_ansi(text: str) -> str:
    """剥离 ANSI 转义序列（颜色、光标移动等）。"""
    return _ANSI_RE.sub('', text)


# 边缘拖拽缩放参数
_EDGE = scale_px(6)

# 边缘 → 光标映射
_EDGE_CURSORS = {
    'l':  Qt.SizeHorCursor,
    'r':  Qt.SizeHorCursor,
    'b':  Qt.SizeVerCursor,
    'bl': Qt.SizeBDiagCursor,
    'br': Qt.SizeFDiagCursor,
}


def _hit_edge(pos: QPoint, w: int, h: int) -> str | None:
    """返回鼠标命中的边缘方向（不含顶部，由标题栏拖拽处理）。"""
    x, y = pos.x(), pos.y()
    e = _EDGE
    left   = x < e
    right  = x > w - e
    bottom = y > h - e
    if bottom and left:  return 'bl'
    if bottom and right: return 'br'
    if left:             return 'l'
    if right:            return 'r'
    if bottom:           return 'b'
    return None


# ---------------------------------------------------------------------------
# 自定义 QEvent 子类（线程→主线程安全传递）
# ---------------------------------------------------------------------------

class _StreamLineEvent(QEvent):
    """后台线程每读取一行输出就投递此事件。"""
    _TYPE = QEvent.Type(QEvent.registerEventType())

    def __init__(self, line: str):
        super().__init__(_StreamLineEvent._TYPE)
        self.line = line


class _StreamDoneEvent(QEvent):
    """命令执行完毕（或失败）时投递此事件。"""
    _TYPE = QEvent.Type(QEvent.registerEventType())

    def __init__(self, success: bool, msg: str = ''):
        super().__init__(_StreamDoneEvent._TYPE)
        self.success = success
        self.msg = msg


# ---------------------------------------------------------------------------
# 标题栏无边框按钮
# ---------------------------------------------------------------------------

class _TitleButton(QPushButton):
    """标题栏小按钮：无边框，hover 变色，黑色边框，支持自定义字体。"""

    def __init__(self, text: str, hover_bg: QColor, parent=None, custom_font=None):
        super().__init__(text, parent)
        self._hover_bg   = hover_bg
        self._normal_bg  = COLORS['pink']
        self._hovered    = False
        font = custom_font if custom_font is not None else get_ui_font(size=scale_px(9))
        self.setFont(font)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(scale_px(28), scale_px(18))
        self.setFocusPolicy(Qt.NoFocus)
        self._refresh_style(False)

    def _refresh_style(self, hovered: bool):
        bg     = _hex(self._hover_bg) if hovered else _hex(self._normal_bg)
        border = _hex(COLORS['black'])          # ← 黑色边框
        self.setStyleSheet(
            f"QPushButton {{"
            f"  background: {bg};"
            f"  color: {_hex(COLORS['black'])};"
            f"  border: {scale_px(1, min_abs=1)}px solid {border};"
            f"  padding: 0px;"
            f"}}"
        )

    def enterEvent(self, event):
        self._hovered = True
        self._refresh_style(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._refresh_style(False)
        super().leaveEvent(event)


# ---------------------------------------------------------------------------
# 绘制型关闭按钮（QPainter 对角线 × 符号，比字符更粗醒目）
# ---------------------------------------------------------------------------

class _CloseButton(_TitleButton):
    """关闭按钮：覆盖 paintEvent，用 QPainter 粗线绘制 × 号。"""

    def __init__(self, hover_bg: QColor, parent=None):
        super().__init__('', hover_bg, parent)     # 文本为空，完全靠绘制
        self.setFixedSize(scale_px(22), scale_px(18))

    def paintEvent(self, event):
        super().paintEvent(event)                  # 先画背景 + 黑色边框
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pen_w = max(2, scale_px(2, min_abs=2))
        pen = QPen(COLORS['black'], pen_w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        m = max(5, scale_px(5, min_abs=5))
        r = self.rect().adjusted(m, m, -m, -m)
        painter.drawLine(r.topLeft(), r.bottomRight())
        painter.drawLine(r.topRight(), r.bottomLeft())
        painter.end()


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

class CmdWindow(QWidget):
    """
    CMD窗口：仿Windows cmd终端，项目主题绘制。
    独立控制面板风格，支持拖拽移动 + 边缘缩放。

    包含：
      - 实时流式命令输出（逐行追加）
      - Braille Spinner 进度指示
      - 标题栏「CLR」清空按钮 + 「✕」关闭按钮（黑色边框）
      - ↑↓ 键命令历史（最多 50 条）
      - ANSI 转义序列自动剥离
      - 强制 UTF-8 编码，兜底 replace 策略
      - 窗口边缘拖拽自由缩放
    """

    # Braille spinner 帧序列
    _SPINNER_FRAMES = ('⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏')
    _HISTORY_MAX    = 50
    _CMD_TIMEOUT    = 60  # 秒，0 表示不限制

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('CMD终端')
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        # 自由缩放：最小尺寸代替固定尺寸
        self.setMinimumSize(scale_px(360), scale_px(260))
        self.resize(scale_px(620), scale_px(420))
        get_topmost_manager().register(self)

        self._event_center = get_event_center()

        # 拖拽移动
        self._dragging     = False
        self._drag_offset  = QPoint()

        # 边缘缩放
        self._resize_edge       = None          # str | None
        self._resize_start_pos  = QPoint()
        self._resize_start_geom = QRect()

        # 边缘光标覆盖（app 级，可跨子 widget 生效）
        self._edge_cursor_active = False

        # 可见性
        self._visible = False

        # 命令历史
        self._history:     deque[str] = deque(maxlen=self._HISTORY_MAX)
        self._history_idx: int        = -1      # -1 = 未在历史中导航

        # 运行中命令控制
        self._running    = False
        self._stop_event = threading.Event()

        # Spinner 状态
        self._spinner_idx   = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(80)
        self._spinner_timer.timeout.connect(self._on_spinner_tick)

        # 透明度效果 + 淡入淡出
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._anim = QPropertyAnimation(self._opacity, b'opacity', self)
        self._anim.setDuration(UI['ui_fade_duration'])
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._anim.finished.connect(self._on_anim_finished)

        self._setup_ui()

        self._event_center.subscribe(EventType.UI_OPEN_CMD_WINDOW, self._on_open_cmd_window)
        self._event_center.subscribe(EventType.UI_OPEN_CMD_WINDOW_WITH_COMMAND, self._on_open_cmd_window_with_command)

        # 安装 app 级事件过滤器，使边缘光标在子 widget 上也能生效
        QApplication.instance().installEventFilter(self)

        self.hide()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------

    def _setup_ui(self):
        """构建 UI 布局：标题栏 + 输出区 + 输入行。"""
        inset = scale_px(4)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(inset, inset, inset, inset)
        main_layout.setSpacing(scale_px(2))

        # ── 标题栏 ──────────────────────────────────────────────────────
        title_widget = QWidget(self)
        title_widget.setFixedHeight(scale_px(24))
        title_layout = QHBoxLayout(title_widget)
        title_layout.setContentsMargins(scale_px(6), 0, scale_px(4), 0)
        title_layout.setSpacing(scale_px(4))

        # Spinner / 状态标签
        self._spinner_label = QLabel(self._SPINNER_FRAMES[0], title_widget)
        self._spinner_label.setFont(get_ui_font(size=scale_px(11)))
        self._spinner_label.setStyleSheet(f'color: {_hex(COLORS["cyan"])};')
        self._spinner_label.setFixedWidth(scale_px(14))
        self._spinner_label.setVisible(False)
        title_layout.addWidget(self._spinner_label)

        # 标题文字：拉海洛粗体，深粉色水印风格
        wm_font = get_digit_font(size=scale_px(16))
        wm_font.setBold(True)
        wm_color = QColor(UI_THEME['deep_pink'])
        wm_color.setAlpha(210)
        title_label = QLabel('Command Line', title_widget)
        title_label.setFont(wm_font)
        title_label.setStyleSheet(
            f'color: rgba({wm_color.red()}, {wm_color.green()}, {wm_color.blue()}, {wm_color.alpha()});'
        )
        title_layout.addWidget(title_label)

        title_layout.addStretch()

        # 「CLR」清空按钮：拉海洛字体
        clr_font = get_digit_font(size=scale_px(9))
        btn_clr = _TitleButton('CLR', UI_THEME['deep_cyan'], title_widget, custom_font=clr_font)
        btn_clr._description = '清空输出'          # 项目 tooltip 系统
        btn_clr.clicked.connect(self._on_clear)
        title_layout.addWidget(btn_clr)

        # 关闭按钮：绘制型 × 符号，更粗更醒目
        btn_close = _CloseButton(UI_THEME['deep_pink'], title_widget)
        btn_close._description = '关闭窗口'        # 项目 tooltip 系统
        btn_close.clicked.connect(self._hide_window)
        title_layout.addWidget(btn_close)

        main_layout.addWidget(title_widget)

        # ── 输出区域（只读）─────────────────────────────────────────────
        self._output = QTextEdit(self)
        self._output.setReadOnly(True)
        self._output.setFont(get_cmd_font())
        self._output.setStyleSheet(scale_style_px(f"""
            QTextEdit {{
                background: {_hex(COLORS['black'])};
                color: {_hex(COLORS['cyan'])};
                border: 2px solid {_hex(COLORS['cyan'])};
                padding: 4px;
                selection-background-color: {_hex(UI_THEME['deep_cyan'])};
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 6px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {_hex(UI_THEME['deep_cyan'])};
                min-height: 20px;
                border-radius: 3px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
                background: transparent;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
        """))
        main_layout.addWidget(self._output)

        # ── 输入行 ────────────────────────────────────────────────────────
        self._input = QLineEdit(self)
        self._input.setPlaceholderText('输入命令，Enter 执行，↑↓ 历史...')
        self._input.setFont(get_cmd_font())
        self._input.setStyleSheet(scale_style_px(f"""
            QLineEdit {{
                background: {_hex(COLORS['black'])};
                color: {_hex(COLORS['cyan'])};
                border: 2px solid {_hex(COLORS['pink'])};
                padding: 2px 6px;
            }}
        """))
        self._input.returnPressed.connect(self._on_input_return)
        self._input.installEventFilter(self)
        main_layout.addWidget(self._input)

        # 初始提示
        self._append_output('CMD 终端已就绪。输入命令并按 Enter 执行。\n')

    # ------------------------------------------------------------------
    # 输出操作
    # ------------------------------------------------------------------

    def _append_output(self, text: str):
        """向输出区追加文本（需在主线程调用）。"""
        self._output.moveCursor(QTextCursor.End)
        self._output.insertPlainText(text)
        self._output.ensureCursorVisible()

    def _on_clear(self):
        """清空输出区。"""
        self._output.clear()

    # ------------------------------------------------------------------
    # 命令执行
    # ------------------------------------------------------------------

    def _on_input_return(self):
        """输入行回车：记录历史并启动流式执行。"""
        cmd = self._input.text().strip()
        if not cmd:
            return

        self._input.clear()
        self._history_idx = -1

        # 记录历史（去重最近一条）
        if not self._history or self._history[-1] != cmd:
            self._history.append(cmd)

        # 显示提示符
        self._append_output(f'\n> {cmd}\n')

        # 启动流式执行
        self._start_stream(cmd)

    def _start_stream(self, cmd: str):
        """启动后台流式执行线程并开始 Spinner。"""
        if self._running:
            self._append_output('[上一条命令仍在运行，请等待...]\n')
            return

        self._running = True
        self._stop_event.clear()
        self._input.setEnabled(False)

        # 启动 Spinner
        self._spinner_idx = 0
        self._spinner_label.setText(self._SPINNER_FRAMES[0])
        self._spinner_label.setVisible(True)
        self._spinner_timer.start()

        t = threading.Thread(target=self._stream_command, args=(cmd,), daemon=True)
        t.start()

    def _stream_command(self, cmd: str):
        """
        后台线程：以 Popen + readline 方式逐行读取输出，
        每行通过 _StreamLineEvent 投递到主线程。
        使用 chcp 65001 强制 UTF-8；fallback errors='replace'。
        """
        # Windows：在 cmd 子 shell 里先切换代码页到 UTF-8
        full_cmd = f'chcp 65001 > nul 2>&1 & {cmd}'
        env = os.environ.copy()
        env['PYTHONUTF8'] = '1'

        try:
            proc = subprocess.Popen(
                full_cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
            )

            for raw_line in iter(proc.stdout.readline, b''):
                if self._stop_event.is_set():
                    proc.kill()
                    break

                # 优先 UTF-8，失败则 GBK，再兜底 replace
                line = None
                for enc in ('utf-8', 'gbk'):
                    try:
                        line = raw_line.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
                if line is None:
                    line = raw_line.decode('utf-8', errors='replace')

                line = _strip_ansi(line)
                QApplication.instance().postEvent(self, _StreamLineEvent(line))

            proc.wait()
            success = (proc.returncode == 0)
            QApplication.instance().postEvent(self, _StreamDoneEvent(success))

        except Exception as e:
            err_msg = f'[执行错误] {e}'
            QApplication.instance().postEvent(self, _StreamLineEvent(err_msg + '\n'))
            QApplication.instance().postEvent(self, _StreamDoneEvent(False, str(e)))

    def _finish_stream(self, success: bool, msg: str = ''):
        """命令完成后的 UI 清理（主线程）。"""
        self._spinner_timer.stop()
        self._spinner_label.setVisible(False)
        self._running = False
        self._input.setEnabled(True)
        self._input.setFocus()

        status = '✓' if success else '✗'
        suffix = f'  [{msg}]' if msg else ''
        self._append_output(f'{status}{suffix}\n')

    # ------------------------------------------------------------------
    # Spinner
    # ------------------------------------------------------------------

    def _on_spinner_tick(self):
        """定时器：推进 Spinner 动画帧。"""
        self._spinner_idx = (self._spinner_idx + 1) % len(self._SPINNER_FRAMES)
        self._spinner_label.setText(self._SPINNER_FRAMES[self._spinner_idx])

    # ------------------------------------------------------------------
    # 命令历史（↑↓）
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        """拦截：① app 级 MouseMove 更新边缘光标；② 输入行 ↑↓ 历史导航。"""
        # ── app 级鼠标移动：边缘光标覆盖（可穿透子 widget）──────────────
        if event.type() == QEvent.MouseMove and isinstance(obj, QWidget):
            try:
                if obj.window() is self:
                    gp   = obj.mapToGlobal(event.pos())
                    lp   = self.mapFromGlobal(gp)
                    edge = _hit_edge(lp, self.width(), self.height())
                    if edge:
                        cur = QCursor(_EDGE_CURSORS.get(edge, Qt.ArrowCursor))
                        if self._edge_cursor_active:
                            QApplication.changeOverrideCursor(cur)
                        else:
                            QApplication.setOverrideCursor(cur)
                            self._edge_cursor_active = True
                    elif self._edge_cursor_active:
                        QApplication.restoreOverrideCursor()
                        self._edge_cursor_active = False
            except Exception:
                pass

        # ── 输入行 ↑↓ 历史导航 ────────────────────────────────────────────
        if obj is self._input and event.type() == QEvent.KeyPress:
            key  = event.key()
            hist = list(self._history)

            if key == Qt.Key_Up and hist:
                if self._history_idx == -1:
                    self._history_idx = len(hist) - 1
                elif self._history_idx > 0:
                    self._history_idx -= 1
                self._input.setText(hist[self._history_idx])
                return True

            if key == Qt.Key_Down and hist:
                if self._history_idx == -1:
                    return True
                if self._history_idx < len(hist) - 1:
                    self._history_idx += 1
                    self._input.setText(hist[self._history_idx])
                else:
                    self._history_idx = -1
                    self._input.clear()
                return True

        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # 事件派发
    # ------------------------------------------------------------------

    def event(self, ev):
        """分发自定义流式事件。"""
        t = ev.type()

        if t == _StreamLineEvent._TYPE:
            try:
                self._append_output(ev.line)
            except Exception:
                pass
            return True

        if t == _StreamDoneEvent._TYPE:
            try:
                self._finish_stream(ev.success, ev.msg)
            except Exception:
                pass
            return True

        return super().event(ev)

    # ------------------------------------------------------------------
    # 窗口事件订阅
    # ------------------------------------------------------------------

    def _on_open_cmd_window(self, event: Event):
        if self._visible:
            self._hide_window()
        else:
            self._show_window()

    def _on_open_cmd_window_with_command(self, event: Event):
        command = event.data.get('command', '')
        if not self._visible:
            self._show_window()
        if command:
            self._input.setText(command)
            self._on_input_return()

    def leaveEvent(self, event):
        """鼠标离开窗口时恢复 app 级光标覆盖。"""
        if self._edge_cursor_active:
            QApplication.restoreOverrideCursor()
            self._edge_cursor_active = False
        super().leaveEvent(event)

    # ------------------------------------------------------------------
    # 显示 / 隐藏
    # ------------------------------------------------------------------

    def _show_window(self):
        if not self._visible and (self.x() == 0 and self.y() == 0):
            desktop = QApplication.desktop()
            sr = desktop.screenGeometry()
            self.move(
                sr.x() + (sr.width()  - self.width())  // 2,
                sr.y() + (sr.height() - self.height()) // 2,
            )
        self.show()
        self._input.setFocus()
        self._visible = True
        self._animate(1.0)

    def _hide_window(self):
        if self._edge_cursor_active:
            QApplication.restoreOverrideCursor()
            self._edge_cursor_active = False
        self._visible = False
        self._animate(0.0)

    def _animate(self, target: float):
        if self._anim.state() == QPropertyAnimation.Running:
            self._anim.stop()
        self._anim.setStartValue(self._opacity.opacity())
        self._anim.setEndValue(target)
        self._anim.start()

    def _on_anim_finished(self):
        if not self._visible:
            self.hide()

    # ------------------------------------------------------------------
    # 绘制（与项目其余窗口一致：黑边→青色边→粉色背景）
    # ------------------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        layer  = scale_px(2, min_abs=1)
        inset2 = layer * 2

        painter.fillRect(self.rect(), COLORS['black'])

        cyan_rect = self.rect().adjusted(layer, layer, -layer, -layer)
        painter.fillRect(cyan_rect, COLORS['cyan'])

        content_rect = self.rect().adjusted(inset2, inset2, -inset2, -inset2)
        painter.fillRect(content_rect, COLORS['pink'])

    # ------------------------------------------------------------------
    # 鼠标事件：标题栏拖拽移动 + 边缘拖拽缩放
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return

        pos  = event.pos()
        edge = _hit_edge(pos, self.width(), self.height())

        # 优先处理边缘缩放
        if edge:
            self._resize_edge       = edge
            self._resize_start_pos  = event.globalPos()
            self._resize_start_geom = self.geometry()
            event.accept()
            return

        # 标题栏拖拽移动
        if pos.y() <= scale_px(24):
            self._dragging    = True
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.pos()

        # 正在缩放
        if self._resize_edge and (event.buttons() & Qt.LeftButton):
            self._do_resize(event.globalPos())
            event.accept()
            return

        # 正在拖拽移动
        if self._dragging and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPos() - self._drag_offset)
            event.accept()
            return

        # 悬停光标更新
        edge = _hit_edge(pos, self.width(), self.height())
        if edge:
            self.setCursor(_EDGE_CURSORS.get(edge, Qt.ArrowCursor))
        elif pos.y() <= scale_px(24):
            self.setCursor(Qt.OpenHandCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self._resize_edge:
                self._resize_edge = None
                self.setCursor(Qt.ArrowCursor)
                event.accept()
                return
            if self._dragging:
                self._dragging = False
                self.setCursor(Qt.OpenHandCursor)
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def _do_resize(self, global_pos: QPoint):
        """根据当前 resize 边缘和鼠标位置动态调整窗口尺寸。"""
        dx = global_pos.x() - self._resize_start_pos.x()
        dy = global_pos.y() - self._resize_start_pos.y()
        g  = self._resize_start_geom
        min_w, min_h = self.minimumWidth(), self.minimumHeight()

        x, y, w, h = g.x(), g.y(), g.width(), g.height()
        edge = self._resize_edge

        if 'r' in edge:
            w = max(min_w, g.width() + dx)
        if 'l' in edge:
            new_w = max(min_w, g.width() - dx)
            x = g.x() + (g.width() - new_w)
            w = new_w
        if 'b' in edge:
            h = max(min_h, g.height() + dy)

        self.setGeometry(x, y, w, h)

    # ------------------------------------------------------------------
    # 关闭保护
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        self._hide_window()
        event.ignore()


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_cmd_window_instance: CmdWindow | None = None


def get_cmd_window() -> CmdWindow:
    """获取全局 CmdWindow 实例（单例）。"""
    global _cmd_window_instance
    if _cmd_window_instance is None:
        _cmd_window_instance = CmdWindow(None)
    return _cmd_window_instance


def cleanup_cmd_window():
    """清理全局 CmdWindow 实例。"""
    global _cmd_window_instance
    if _cmd_window_instance is not None:
        _cmd_window_instance.hide()
        _cmd_window_instance.deleteLater()
        _cmd_window_instance = None
