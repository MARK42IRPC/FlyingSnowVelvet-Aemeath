"""动画帧预取缓冲区 - 流式加载和帧预取"""
import os
import threading
import time
from collections import deque
from concurrent.futures import Future
from typing import Optional, List
from PIL import Image
from PyQt5.QtGui import QPixmap, QImage

from lib.core.compute_hub import get_compute_hub
from lib.core.logger import get_logger
from lib.script.SEanima.clip import resolve_animation_clip
logger = get_logger(__name__)


class FramePrefetchBuffer:
    """帧预取缓冲区 - 流式加载并预取即将播放的帧"""

    def __init__(self, buffer_size: int = 60):
        """
        初始化预取缓冲区

        Args:
            buffer_size: 缓冲区大小（帧数），默认60帧（增加以减少卡顿）
        """
        self._buffer = deque(maxlen=buffer_size)  # 固定大小的双端队列
        self._buffer_size = buffer_size
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._frame_files = []
        self._current_index = 0
        self._total_frames = 0
        self._is_loading = False
        self._is_complete = False
        self._prefetch_future: Optional[Future] = None
        self._anim_folder = ""
        self._project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

    def _get_animation_path(self, animation_type: str) -> str:
        """获取动画文件夹路径"""
        return resolve_animation_clip(animation_type).folder_path

    def _scan_frames(self, animation_type: str) -> bool:
        """扫描动画帧文件"""
        self._anim_folder = self._get_animation_path(animation_type)
        if not os.path.exists(self._anim_folder):
            logger.warning("[FramePrefetchBuffer] 动画文件夹不存在: %s", self._anim_folder)
            return False

        self._frame_files = sorted([
            f for f in os.listdir(self._anim_folder)
            if f.endswith('.png') or f.endswith('.webp')
        ])
        self._total_frames = len(self._frame_files)
        logger.info("[FramePrefetchBuffer] 扫描到 %d 帧动画", self._total_frames)
        return self._total_frames > 0

    def _load_frame(self, frame_index: int) -> Optional[QPixmap]:
        """加载指定帧"""
        if frame_index >= len(self._frame_files):
            return None

        frame_file = self._frame_files[frame_index]
        frame_path = os.path.join(self._anim_folder, frame_file)

        try:
            pil_img = Image.open(frame_path)
            if pil_img.mode != 'RGBA':
                pil_img = pil_img.convert('RGBA')
            w, h = pil_img.size
            data = pil_img.tobytes('raw', 'RGBA')
            qimg = QImage(data, w, h, QImage.Format_RGBA8888).copy()

            if not qimg.isNull():
                return QPixmap.fromImage(qimg)
        except Exception as e:
            logger.error("[FramePrefetchBuffer] 加载帧失败: %s, 错误: %s", frame_file, e)

        return None

    def _prefetch_worker(self):
        """预取工作线程 - 批量加载以提升速度"""
        logger.debug("[FramePrefetchBuffer] 开始预取线程，缓冲区大小: %d", self._buffer_size)

        batch_size = 10  # 批量加载帧数
        start_time = time.time()

        while self._is_loading and self._current_index < self._total_frames:
            with self._condition:
                # 如果缓冲区已满，等待空间（减少等待时间）
                while len(self._buffer) >= self._buffer_size and self._is_loading:
                    self._condition.wait(timeout=0.001)
                    if not self._is_loading:
                        return

            # 批量加载帧
            batch = []
            for i in range(batch_size):
                if self._current_index >= self._total_frames:
                    break

                pixmap = self._load_frame(self._current_index)
                if pixmap:
                    batch.append(pixmap)

                self._current_index += 1

            # 批量添加到缓冲区
            if batch:
                with self._lock:
                    for pixmap in batch:
                        self._buffer.append(pixmap)
                    self._condition.notify_all()

            # 打印进度
            if self._current_index % 30 == 0:
                elapsed = time.time() - start_time
                speed = self._current_index / elapsed if elapsed > 0 else 0
                logger.debug("[FramePrefetchBuffer] 已预取 %d/%d 帧 (耗时: %.2fs, 速度: %.1f 帧/s, 缓冲区: %d 帧)",
                             self._current_index, self._total_frames, elapsed, speed, len(self._buffer))

        self._is_complete = True
        elapsed = time.time() - start_time
        speed = self._total_frames / elapsed if elapsed > 0 else 0
        logger.info("[FramePrefetchBuffer] 预取完成: %d/%d 帧 (总耗时: %.2fs, 平均速度: %.1f 帧/s)",
                    self._current_index, self._total_frames, elapsed, speed)

    def start_prefetch(self, animation_type: str) -> bool:
        """
        开始预取帧

        Args:
            animation_type: 动画类型

        Returns:
            是否成功开始预取
        """
        if not self._scan_frames(animation_type):
            return False

        self._is_loading = True
        self._is_complete = False
        self._current_index = 0
        self._buffer.clear()

        # 启动预取任务
        self._prefetch_future = get_compute_hub().submit_io(self._prefetch_worker)

        return True

    def get_frame(self, timeout: float = 0.1) -> Optional[QPixmap]:
        """
        获取下一帧（阻塞等待直到有帧可用）

        Args:
            timeout: 超时时间（秒）

        Returns:
            下一帧的 QPixmap，如果无帧可用则返回 None
        """
        with self._condition:
            # 等待缓冲区有帧
            while len(self._buffer) == 0 and not self._is_complete and self._is_loading:
                if not self._condition.wait(timeout=timeout):
                    # 超时
                    return None

            if len(self._buffer) == 0:
                return None

            frame = self._buffer.popleft()
            self._condition.notify_all()
            return frame

    def peek_frame(self) -> Optional[QPixmap]:
        """
        查看下一帧（不移除）

        Returns:
            下一帧的 QPixmap，如果无帧可用则返回 None
        """
        with self._lock:
            if len(self._buffer) == 0:
                return None
            return self._buffer[0]

    def stop_prefetch(self):
        """停止预取"""
        with self._lock:
            self._is_loading = False
            self._condition.notify_all()
        self._prefetch_future = None

    @property
    def buffer_size(self) -> int:
        """当前缓冲区大小"""
        return len(self._buffer)

    @property
    def total_frames(self) -> int:
        """总帧数"""
        return self._total_frames

    @property
    def is_complete(self) -> bool:
        """是否加载完成"""
        return self._is_complete

    @property
    def is_loading(self) -> bool:
        """是否正在加载"""
        return self._is_loading


# 全局缓冲区实例
_frame_prefetch_buffer = None


def get_frame_prefetch_buffer() -> FramePrefetchBuffer:
    """获取全局帧预取缓冲区实例（单例模式）"""
    global _frame_prefetch_buffer
    if _frame_prefetch_buffer is None:
        _frame_prefetch_buffer = FramePrefetchBuffer(buffer_size=60)  # 增加到60帧
    return _frame_prefetch_buffer
