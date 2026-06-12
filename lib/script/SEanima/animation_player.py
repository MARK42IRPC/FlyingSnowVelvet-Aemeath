"""独立动画播放器 - 多线程流式解码版本

改进内容（相较于原版）：
1. ThreadPoolExecutor (4线程) 并行解码，速度约 4x
2. queue.Queue 替代信号作为暂存区，QPixmap 转换在主线程（符合 Qt 线程规则）
3. 分离 _paint_frame / _play_frame，消除 paintEvent 帧索引竞态
4. 缓冲区不足时暂停于最后有效帧，消除透明空帧频闪
"""
import sys
import os
import queue
import threading
import time
import logging
from concurrent.futures import ThreadPoolExecutor

# 添加项目根目录到 Python 路径
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from PyQt5.QtWidgets import QWidget, QApplication
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap, QPainter
from PIL import Image, ImageFilter

# 项目核心模块（路径已在上方注入，可安全导入）
from lib.core.topmost_manager import get_topmost_manager
from lib.core.anchor_utils import apply_ui_opacity
from config.config import ANIMATION  # 与主宠物共享尺寸配置来源

_log = logging.getLogger(__name__)

# Pillow LANCZOS 兼容（Pillow 9.1+ 用 Resampling 枚举，旧版用属性）
try:
    _LANCZOS = Image.Resampling.LANCZOS
except AttributeError:
    _LANCZOS = Image.LANCZOS

_ANIMATION_DEFAULT_SIZE_MULTIPLIER = 2.0
_EXIT_SHADOW_COLOR = (0, 0, 0)
_EXIT_SHADOW_DEFAULT_STRENGTH = 112
_EXIT_SHADOW_DEFAULT_BLUR_RADIUS = 14
_EXIT_SHADOW_DEFAULT_DIRECTION = 'down'
_EXIT_SHADOW_MAX_STRENGTH = 255
_EXIT_SHADOW_MAX_BLUR_RADIUS = 128
_EXIT_SHADOW_OFFSET_DISTANCE_RATIO = 0.45
_EXIT_SHADOW_MIN_OFFSET_DISTANCE = 4
_EXIT_SHADOW_PADDING_MULTIPLIER = 2.2
_EXIT_SHADOW_DIRECTION_VECTORS = {
    'center': (0, 0),
    'down': (0, 1),
    'up': (0, -1),
    'left': (-1, 0),
    'right': (1, 0),
    'down_left': (-1, 1),
    'down_right': (1, 1),
    'up_left': (-1, -1),
    'up_right': (1, -1),
}
_EXIT_SHADOW_DIRECTION_ALIASES = {
    '': 'down',
    'center': 'center',
    'none': 'center',
    'off': 'center',
    'middle': 'center',
    'stay': 'center',
    '静止': 'center',
    '无': 'center',
    '不偏移': 'center',
    'down': 'down',
    'bottom': 'down',
    'south': 'down',
    '下': 'down',
    '向下': 'down',
    'up': 'up',
    'top': 'up',
    'north': 'up',
    '上': 'up',
    '向上': 'up',
    'left': 'left',
    'west': 'left',
    '左': 'left',
    '向左': 'left',
    'right': 'right',
    'east': 'right',
    '右': 'right',
    '向右': 'right',
    'down_left': 'down_left',
    'bottom_left': 'down_left',
    'south_west': 'down_left',
    'southwest': 'down_left',
    '左下': 'down_left',
    '向左下': 'down_left',
    'down_right': 'down_right',
    'bottom_right': 'down_right',
    'south_east': 'down_right',
    'southeast': 'down_right',
    '右下': 'down_right',
    '向右下': 'down_right',
    'up_left': 'up_left',
    'top_left': 'up_left',
    'north_west': 'up_left',
    'northwest': 'up_left',
    '左上': 'up_left',
    '向左上': 'up_left',
    'up_right': 'up_right',
    'top_right': 'up_right',
    'north_east': 'up_right',
    'northeast': 'up_right',
    '右上': 'up_right',
    '向右上': 'up_right',
}


def _clamp_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = int(default)
    return max(minimum, min(maximum, number))


def _normalize_exit_shadow_direction(value) -> str:
    key = str(value or '').strip().lower().replace('-', '_').replace(' ', '_')
    return _EXIT_SHADOW_DIRECTION_ALIASES.get(key, _EXIT_SHADOW_DEFAULT_DIRECTION)


def _build_exit_shadow_metrics(width: int, height: int, strength: int, blur_radius: int, direction: str):
    """按配置生成退出动画羽化阴影参数。"""
    strength = _clamp_int(strength, _EXIT_SHADOW_DEFAULT_STRENGTH, 0, _EXIT_SHADOW_MAX_STRENGTH)
    blur_radius = _clamp_int(
        blur_radius,
        _EXIT_SHADOW_DEFAULT_BLUR_RADIUS,
        0,
        min(_EXIT_SHADOW_MAX_BLUR_RADIUS, max(0, min(width, height))),
    )
    if strength <= 0:
        return None

    direction_key = _normalize_exit_shadow_direction(direction)
    vec_x, vec_y = _EXIT_SHADOW_DIRECTION_VECTORS.get(direction_key, _EXIT_SHADOW_DIRECTION_VECTORS['down'])
    offset_distance = 0
    if vec_x or vec_y:
        offset_distance = max(
            _EXIT_SHADOW_MIN_OFFSET_DISTANCE,
            int(round(max(1, blur_radius) * _EXIT_SHADOW_OFFSET_DISTANCE_RATIO)),
        )
    offset_x = vec_x * offset_distance
    offset_y = vec_y * offset_distance
    feather_pad = max(
        2,
        blur_radius + 2,
        int(round(max(1, blur_radius) * _EXIT_SHADOW_PADDING_MULTIPLIER)),
    )
    frame_x = feather_pad + max(0, -offset_x)
    frame_y = feather_pad + max(0, -offset_y)
    canvas_w = width + feather_pad * 2 + abs(offset_x)
    canvas_h = height + feather_pad * 2 + abs(offset_y)
    return {
        'strength': strength,
        'blur_radius': blur_radius,
        'direction': direction_key,
        'offset_x': offset_x,
        'offset_y': offset_y,
        'frame_x': frame_x,
        'frame_y': frame_y,
        'canvas_w': canvas_w,
        'canvas_h': canvas_h,
        'alpha_lut': [(value * strength) // 255 for value in range(256)],
    }


def _resolve_exit_shadow_metrics(width: int, height: int):
    animation_cfg = ANIMATION if isinstance(ANIMATION, dict) else {}
    return _build_exit_shadow_metrics(
        width,
        height,
        animation_cfg.get('exit_shadow_strength', _EXIT_SHADOW_DEFAULT_STRENGTH),
        animation_cfg.get('exit_shadow_blur_radius', _EXIT_SHADOW_DEFAULT_BLUR_RADIUS),
        animation_cfg.get('exit_shadow_offset_direction', _EXIT_SHADOW_DEFAULT_DIRECTION),
    )


def _compose_exit_shadow_frame(frame: Image.Image, metrics) -> Image.Image:
    """为退出动画帧合成羽化阴影，并扩出画布避免裁切。"""
    shadow_alpha = frame.getchannel('A').point(metrics['alpha_lut'])
    shadow = Image.new('RGBA', frame.size, _EXIT_SHADOW_COLOR + (0,))
    shadow.putalpha(shadow_alpha)
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=metrics['blur_radius']))

    canvas = Image.new(
        'RGBA',
        (metrics['canvas_w'], metrics['canvas_h']),
        (0, 0, 0, 0),
    )
    canvas.paste(
        shadow,
        (metrics['frame_x'] + metrics['offset_x'], metrics['frame_y'] + metrics['offset_y']),
        shadow,
    )
    canvas.paste(frame, (metrics['frame_x'], metrics['frame_y']), frame)
    return canvas


class AnimationWindow(QWidget):
    """流式动画窗口

    架构：
    - 后台线程并行解码帧 → raw RGBA bytes → queue.Queue（线程安全暂存区）
    - 主线程定时器每帧 drain 暂存区 → 转换为 QPixmap → 追加到 _frame_buffer
    - 播放帧（_play_frame）与渲染帧（_paint_frame）分离，消除竞态
    - 缓冲区不足时保持最后一帧，不产生透明闪烁
    """

    def __init__(self, animation_type: str):
        super().__init__()
        self._animation_type = animation_type

        # 帧缓冲（主线程独享）
        self._frame_buffer = []     # list[QPixmap]
        self._paint_frame  = 0      # paintEvent 渲染此索引
        self._play_frame   = 0      # 播放推进位置
        self._total_frames = 0      # 加载完成后设置
        self._loading_done = False

        # 线程安全暂存区：后台线程 put bytes，主线程 get 并转 QPixmap
        # sentinel = None 表示加载完成
        self._staging: queue.Queue = queue.Queue()

        # 目标帧尺寸（后台线程写一次，主线程读）
        self._target_w = 0
        self._target_h = 0
        self._visual_anchor_x = 0.0
        self._visual_anchor_y = 0.0

        # 淡出
        self._opacity          = 1.0
        self._fade_start_frame = 0

        # 窗口是否已显示
        self._is_showing = False

        # 播放定时器 ~60fps
        self._timer = QTimer()
        self._timer.timeout.connect(self._on_frame)

        _log.debug("[AnimationWindow] 创建: %s", animation_type)

    # ── 路径 ────────────────────────────────────────────────────────

    def _get_folder(self) -> str:
        sub = 'start-anim-compressed' if self._animation_type == 'start' \
              else 'exit-anim-compressed'
        return os.path.join(project_root, 'resc', 'GIF', sub)

    # ── 加载阶段（后台线程）─────────────────────────────────────────

    def start(self):
        """启动后台解码线程与播放定时器

        与主宠物统一：尺寸使用 config.config 的 ANIMATION['pet_size']，
        不再基于屏幕宽度做二次推导。
        """
        threading.Thread(
            target=self._decode_worker, daemon=True
        ).start()
        self._timer.start(16)   # 60 fps；缓冲区有帧前窗口不显示

    def _decode_worker(self):
        """后台线程：并行解码所有帧，以 RGBA bytes 形式送入暂存区"""
        folder = self._get_folder()
        if not os.path.exists(folder):
            _log.warning("[AnimationWindow] 动画文件夹不存在: %s", folder)
            self._staging.put(None)
            return

        files = sorted(
            f for f in os.listdir(folder) if f.endswith(('.png', '.webp'))
        )
        if not files:
            _log.warning("[AnimationWindow] 未找到帧文件: %s", folder)
            self._staging.put(None)
            return

        total = len(files)
        _log.info("[AnimationWindow] 开始解码 %d 帧 (%s)", total, self._animation_type)
        t0 = time.time()

        # 探测第一帧以确定原始尺寸（只读 header，极快）
        with Image.open(os.path.join(folder, files[0])) as probe:
            orig_w, orig_h = probe.size

        # 与主宠物统一尺寸来源：基于 ANIMATION['pet_size'] 的默认尺寸再放大 3 倍。
        # 若资源比例与目标比例不一致，则按等比缩放以避免拉伸。
        pet_w, pet_h = ANIMATION.get('pet_size', (150, 150))
        target_box_w = max(1, int(round(max(1, int(pet_w)) * _ANIMATION_DEFAULT_SIZE_MULTIPLIER)))
        target_box_h = max(1, int(round(max(1, int(pet_h)) * _ANIMATION_DEFAULT_SIZE_MULTIPLIER)))
        fit_scale = min(target_box_w / max(1, orig_w), target_box_h / max(1, orig_h))
        base_w = max(1, int(orig_w * fit_scale))
        base_h = max(1, int(orig_h * fit_scale))

        shadow_metrics = None
        target_w = base_w
        target_h = base_h
        self._visual_anchor_x = target_w / 2.0
        self._visual_anchor_y = target_h / 2.0

        if self._animation_type == 'exit':
            shadow_metrics = _resolve_exit_shadow_metrics(base_w, base_h)
            if shadow_metrics is not None:
                target_w = int(shadow_metrics['canvas_w'])
                target_h = int(shadow_metrics['canvas_h'])
                self._visual_anchor_x = float(shadow_metrics['frame_x']) + (base_w / 2.0)
                self._visual_anchor_y = float(shadow_metrics['frame_y']) + (base_h / 2.0)

        # 先写尺寸，再往 queue 里放数据；主线程 get 之后读尺寸保证可见
        self._target_w = target_w
        self._target_h = target_h

        _log.info("[AnimationWindow] 帧尺寸(主宠物模式x%.1f): %dx%d → %dx%d",
                  _ANIMATION_DEFAULT_SIZE_MULTIPLIER, orig_w, orig_h, target_w, target_h)
        if shadow_metrics is not None:
            _log.info(
                "[AnimationWindow] 退出动画羽化阴影: strength=%d, blur=%d, direction=%s, offset=(%d,%d)",
                shadow_metrics['strength'],
                shadow_metrics['blur_radius'],
                shadow_metrics['direction'],
                shadow_metrics['offset_x'],
                shadow_metrics['offset_y'],
            )

        def decode_one(idx: int):
            path = os.path.join(folder, files[idx])
            try:
                with Image.open(path) as img:
                    img = img.convert('RGBA').resize((base_w, base_h), _LANCZOS)
                    if shadow_metrics is not None:
                        img = _compose_exit_shadow_frame(img, shadow_metrics)
                    return img.tobytes()
            except Exception as e:
                _log.error("[AnimationWindow] 帧 %d 解码失败: %s", idx, e)
                return None

        # pool.map 保证结果顺序与输入一致，4线程并行加速 I/O + 解码
        workers = min(4, os.cpu_count() or 2)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for raw in pool.map(decode_one, range(total)):
                self._staging.put(raw)   # bytes 或 None（解码失败）

        self._staging.put(None)  # sentinel：加载完成

        elapsed = time.time() - t0
        speed   = total / elapsed if elapsed > 0 else 0
        _log.info("[AnimationWindow] 解码完成: %d 帧, %.2fs, %.0f 帧/s", total, elapsed, speed)

    # ── 主线程 drain：暂存区 → QPixmap ──────────────────────────────

    def _drain_staging(self):
        """将暂存区中已解码的 raw bytes 转换为 QPixmap 并追加到帧缓冲。

        QPixmap 只能在主线程创建，此方法仅在主线程（定时器回调）中调用。
        """
        while True:
            try:
                raw = self._staging.get_nowait()
            except queue.Empty:
                break

            # 读尺寸放在 get 之后，利用 queue 的内存可见性保证
            tw, th = self._target_w, self._target_h

            if raw is None:
                # sentinel：所有帧已入队
                self._loading_done    = True
                self._total_frames    = len(self._frame_buffer)
                self._fade_start_frame = max(0, self._total_frames - 60)
                _log.info("[AnimationWindow] 全部帧就绪: %d 帧, 淡出起始帧: %d",
                          self._total_frames, self._fade_start_frame)
                break

            if tw == 0 or th == 0:
                # 尺寸异常，跳过此帧（理论上不会发生）
                continue

            # bytes → QImage（.copy() 确保独立内存）→ QPixmap
            qimg = QImage(raw, tw, th, QImage.Format_RGBA8888).copy()
            self._frame_buffer.append(QPixmap.fromImage(qimg))

    # ── 播放阶段（主线程）──────────────────────────────────────────

    def _on_frame(self):
        """定时器回调：drain 暂存区 → 推进帧 → 触发重绘"""
        self._drain_staging()

        # 第一帧就绪时才显示窗口
        if not self._is_showing and self._frame_buffer:
            self._is_showing = True
            self._setup_and_show()

        # 缓冲区不足当前播放位置 → 暂停保帧，不闪烁
        if self._play_frame >= len(self._frame_buffer):
            if self._loading_done and self._play_frame >= self._total_frames:
                _log.info("[AnimationWindow] 播放完成 (%d 帧)", self._play_frame)
                self.close()
                QApplication.instance().quit()
            # 不调用 update()：保持上一帧画面，无需重绘
            return

        # 淡出透明度计算
        if self._fade_start_frame > 0 and self._play_frame >= self._fade_start_frame:
            total    = self._total_frames or len(self._frame_buffer)
            progress = (self._play_frame - self._fade_start_frame) / (
                        total - self._fade_start_frame)
            self._opacity = max(0.0, 1.0 - progress)
            self.setWindowOpacity(apply_ui_opacity(self._opacity))

        # 固定渲染帧给 paintEvent，推进播放位置
        self._paint_frame  = self._play_frame
        self._play_frame  += 1
        self.update()

    def _setup_and_show(self):
        """配置窗口属性并居中显示（仅调用一次）"""
        tw, th = self._target_w, self._target_h
        self.setFixedSize(tw, th)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        screen = QApplication.primaryScreen().geometry()
        anchor_x = self._visual_anchor_x or (tw / 2.0)
        anchor_y = self._visual_anchor_y or (th / 2.0)
        self.move(
            int(round(screen.x() + (screen.width() / 2.0) - anchor_x)),
            int(round(screen.y() + (screen.height() / 2.0) - anchor_y)),
        )
        self.setWindowOpacity(apply_ui_opacity(1.0))
        self.show()
        get_topmost_manager().register(self)
        self.raise_()
        self.activateWindow()
        _log.info("[AnimationWindow] 窗口显示: %dx%d", tw, th)

    def paintEvent(self, event):
        if not self._frame_buffer:
            return
        idx = self._paint_frame
        if idx >= len(self._frame_buffer):
            return
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._frame_buffer[idx])

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)


# ── 入口 ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(1)

    animation_type = sys.argv[1]
    logging.basicConfig(level=logging.INFO)
    _log.info("[AnimationPlayer] 启动流式解码播放器: %s", animation_type)

    # 设置 Qt 平台插件路径
    if 'QT_QPA_PLATFORM_PLUGIN_PATH' not in os.environ:
        try:
            import PyQt5.QtCore
            qt_path = os.path.dirname(PyQt5.QtCore.__file__)
            platforms_path = os.path.join(qt_path, 'Qt5', 'plugins', 'platforms')
            if os.path.exists(platforms_path):
                os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = platforms_path
                _log.info("[AnimationPlayer] Qt平台插件路径: %s", platforms_path)
        except Exception:
            pass

    t0 = time.time()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    _log.info("[AnimationPlayer] QApplication 创建耗时: %.2fs", time.time() - t0)

    window = AnimationWindow(animation_type)
    window.start()

    _log.info("[AnimationPlayer] 总启动耗时: %.2fs，进入事件循环", time.time() - t0)
    sys.exit(app.exec_())
