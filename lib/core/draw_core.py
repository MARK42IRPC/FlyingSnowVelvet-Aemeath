"""绘制核心模块 - 统一管理所有绘制逻辑"""
from typing import Dict, List, Tuple, Optional, Union
from PyQt5.QtGui import QPainter, QPixmap, QImage, QTransform
from PyQt5.QtCore import QRect, QPoint, Qt
from dataclasses import dataclass

from lib.core.layer import Layer, draw_order_key, normalize_layer


@dataclass
class DrawRequest:
    """绘制请求数据结构"""
    resource_id: str      # 资源ID（如状态名称）
    frame_index: int = -1 # 帧索引，-1表示使用当前帧
    position: Optional[Union[QPoint, Tuple[int, int]]] = None  # 绘制位置
    alpha: float = 1.0    # 透明度
    flipped: bool = False # 是否水平翻转
    scale: float = 1.0    # 缩放比例
    layer: int = int(Layer.MAIN_PET)  # 绘制层级
    z: int = 0           # 同层内排序，越大越靠前
    order: int = 0       # 稳定插入顺序，由 DrawCore 写入


class DrawCore:
    """
    绘制核心类 - 统一管理所有绘制逻辑
    
    通过事件系统调用，格式: [资源id, 资源帧[如有], 绘制位置]
    """

    def __init__(self):
        # 资源缓存: resource_id -> [QImage, ...]
        self._resources: Dict[str, List[QImage]] = {}
        
        # 当前帧缓存: resource_id -> current_frame_index
        self._current_frames: Dict[str, int] = {}
        
        # 活跃绘制请求
        self._active_requests: Dict[str, DrawRequest] = {}
        self._request_seq: int = 0

        # 基础像素图缓存: (resource_id, frame_index) -> QPixmap
        self._frame_pixmap_cache: Dict[Tuple[str, int], QPixmap] = {}
        # 变换后像素图缓存: (resource_id, frame_index, w, h, flipped) -> QPixmap
        self._render_pixmap_cache: Dict[Tuple[str, int, int, int, bool], QPixmap] = {}

    def register_resource(self, resource_id: str, frames: List[QImage]):
        """
        注册资源
        
        Args:
            resource_id: 资源ID
            frames: 帧列表
        """
        self._invalidate_resource_cache(resource_id)
        self._resources[resource_id] = frames
        self._current_frames[resource_id] = 0

    def unregister_resource(self, resource_id: str):
        """注销资源"""
        self._invalidate_resource_cache(resource_id)
        self._resources.pop(resource_id, None)
        self._current_frames.pop(resource_id, None)
        self._active_requests.pop(resource_id, None)

    def has_resource(self, resource_id: str) -> bool:
        """检查资源是否存在"""
        return resource_id in self._resources

    def get_frame_count(self, resource_id: str) -> int:
        """获取资源的帧数"""
        return len(self._resources.get(resource_id, []))

    def get_current_frame_index(self, resource_id: str) -> int:
        """获取当前帧索引"""
        return self._current_frames.get(resource_id, 0)

    def next_frame(self, resource_id: str) -> Optional[Tuple[QImage, bool]]:
        """
        切换到下一帧并返回
        
        Args:
            resource_id: 资源ID
            
        Returns:
            元组 (QImage, loop_completed)，如果资源不存在返回 None
            - QImage: 下一帧的图像
            - loop_completed: 是否完成了一次完整循环
        """
        if resource_id not in self._resources:
            return None
        
        frames = self._resources[resource_id]
        if not frames:
            return None
        
        # 检测循环完成（在切换前检测，因为当前帧是最后一帧时意味着本次切换后循环完成）
        loop_completed = self._current_frames[resource_id] == len(frames) - 1
        
        # 切换到下一帧
        self._current_frames[resource_id] = (self._current_frames[resource_id] + 1) % len(frames)
        
        frame = frames[self._current_frames[resource_id]]
        return frame, loop_completed

    def get_frame(self, resource_id: str, frame_index: int = -1) -> Optional[QImage]:
        """
        获取指定帧
        
        Args:
            resource_id: 资源ID
            frame_index: 帧索引，-1表示当前帧
            
        Returns:
            指定帧的QImage
        """
        if resource_id not in self._resources:
            return None
        
        frames = self._resources[resource_id]
        if not frames:
            return None
        
        if frame_index == -1:
            frame_index = self._current_frames[resource_id]
        
        if 0 <= frame_index < len(frames):
            return frames[frame_index]
        
        return None

    def reset_frame(self, resource_id: str):
        """重置资源到第一帧"""
        if resource_id in self._current_frames:
            self._current_frames[resource_id] = 0

    def add_draw_request(self, request: DrawRequest, clear_others: bool = False):
        """
        添加绘制请求

        Args:
            request: 绘制请求对象
            clear_others: 是否清除其他所有请求（只保留当前请求）
        """
        if clear_others:
            # 清除所有其他请求，只保留当前请求
            self._active_requests.clear()

        request.layer = normalize_layer(request.layer, Layer.MAIN_PET)
        try:
            request.z = int(request.z)
        except (TypeError, ValueError):
            request.z = 0

        existing = self._active_requests.get(request.resource_id)
        if existing is not None:
            # 更新现有请求
            request.order = existing.order
            self._active_requests[request.resource_id] = request
        else:
            # 添加新请求
            self._request_seq += 1
            request.order = self._request_seq
            self._active_requests[request.resource_id] = request

    def remove_draw_request(self, resource_id: str):
        """移除绘制请求"""
        self._active_requests.pop(resource_id, None)

    def clear_all_requests(self):
        """清除所有绘制请求"""
        self._active_requests.clear()

    def render(self, painter: QPainter, target_rect: Optional[QRect] = None):
        """
        执行所有活跃的绘制请求

        Args:
            painter: QPainter对象
            target_rect: 目标绘制区域，如果为None则使用每个请求自己的位置
        """
        if not self._active_requests:
            return

        painter.save()
        # DrawCore 当前主要绘制 QPixmap，保留平滑缩放即可；
        # 关闭高质量抗锯齿可明显降低主线程绘制开销。
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        requests = sorted(
            self._active_requests.values(),
            key=lambda item: draw_order_key(item.layer, item.z, item.order, Layer.MAIN_PET),
        )

        for request in requests:
            # 获取帧
            frame_index = self._resolve_frame_index(request.resource_id, request.frame_index)
            frame = self.get_frame(request.resource_id, frame_index)
            if frame is None:
                continue

            # 使用缓存获取基础 pixmap
            base_pixmap = self._get_base_pixmap(request.resource_id, frame_index, frame)
            if base_pixmap.isNull():
                continue

            # 确定最终绘制尺寸
            if target_rect:
                # 使用目标区域的大小
                draw_w = max(1, target_rect.width())
                draw_h = max(1, target_rect.height())
            else:
                # 使用原始大小 * 缩放比例
                draw_w = max(1, int(round(base_pixmap.width() * request.scale)))
                draw_h = max(1, int(round(base_pixmap.height() * request.scale)))

            pixmap = self._get_render_pixmap(
                request.resource_id,
                frame_index,
                draw_w,
                draw_h,
                request.flipped,
                base_pixmap,
            )

            draw_rect = self._resolve_draw_rect(request, pixmap, target_rect)

            # 仅在需要时切换透明度，减少 painter 状态切换。
            if abs(request.alpha - 1.0) > 1e-4:
                painter.save()
                painter.setOpacity(request.alpha)
                painter.drawPixmap(draw_rect, pixmap)
                painter.restore()
            else:
                painter.drawPixmap(draw_rect, pixmap)

        painter.restore()

    def get_active_resource_ids(self) -> List[str]:
        """获取所有活跃的资源ID"""
        return list(self._active_requests.keys())

    def set_request_alpha(self, resource_id: str, alpha: float):
        """设置指定资源的透明度"""
        if resource_id in self._active_requests:
            self._active_requests[resource_id].alpha = max(0.0, min(1.0, alpha))

    def set_request_flipped(self, resource_id: str, flipped: bool):
        """设置指定资源的翻转状态"""
        if resource_id in self._active_requests:
            self._active_requests[resource_id].flipped = flipped

    def set_request_position(self, resource_id: str, position: Union[QPoint, Tuple[int, int]]):
        """设置指定资源的绘制位置"""
        if resource_id in self._active_requests:
            self._active_requests[resource_id].position = position

    def set_request_scale(self, resource_id: str, scale: float):
        """设置指定资源的缩放比例"""
        if resource_id in self._active_requests:
            self._active_requests[resource_id].scale = scale

    def cleanup(self):
        """清理绘制核心，释放所有资源"""
        self._resources.clear()
        self._current_frames.clear()
        self._active_requests.clear()
        self._frame_pixmap_cache.clear()
        self._render_pixmap_cache.clear()

    def _invalidate_resource_cache(self, resource_id: str) -> None:
        """清理指定资源的 pixmap 缓存。"""
        self._frame_pixmap_cache = {
            key: value for key, value in self._frame_pixmap_cache.items()
            if key[0] != resource_id
        }
        self._render_pixmap_cache = {
            key: value for key, value in self._render_pixmap_cache.items()
            if key[0] != resource_id
        }

    def _resolve_frame_index(self, resource_id: str, frame_index: int) -> int:
        """将 -1 帧索引解析为当前帧索引。"""
        if frame_index == -1:
            return self._current_frames.get(resource_id, 0)
        return frame_index

    def _get_base_pixmap(self, resource_id: str, frame_index: int, frame: QImage) -> QPixmap:
        """获取基础 pixmap（只做 QImage -> QPixmap 转换）。"""
        key = (resource_id, frame_index)
        cached = self._frame_pixmap_cache.get(key)
        if cached is not None:
            return cached
        pixmap = QPixmap.fromImage(frame)
        self._frame_pixmap_cache[key] = pixmap
        return pixmap

    def _get_render_pixmap(
        self,
        resource_id: str,
        frame_index: int,
        draw_w: int,
        draw_h: int,
        flipped: bool,
        base_pixmap: QPixmap,
    ) -> QPixmap:
        """获取缩放/翻转后的 pixmap。"""
        key = (resource_id, frame_index, draw_w, draw_h, flipped)
        cached = self._render_pixmap_cache.get(key)
        if cached is not None:
            return cached

        pixmap = base_pixmap
        if draw_w != pixmap.width() or draw_h != pixmap.height():
            pixmap = pixmap.scaled(
                draw_w,
                draw_h,
                Qt.IgnoreAspectRatio,
                Qt.SmoothTransformation
            )

        if flipped:
            transform = QTransform().scale(-1, 1)
            pixmap = pixmap.transformed(transform, Qt.SmoothTransformation)

        self._render_pixmap_cache[key] = pixmap
        return pixmap

    def _resolve_draw_rect(
        self,
        request: DrawRequest,
        pixmap: QPixmap,
        target_rect: Optional[QRect],
    ) -> QRect:
        """解析最终绘制矩形。"""
        if target_rect:
            draw_rect = QRect(QPoint(0, 0), pixmap.size())
            draw_rect.moveCenter(target_rect.center())
            return draw_rect
        if request.position:
            if isinstance(request.position, QPoint):
                return QRect(request.position, pixmap.size())
            return QRect(QPoint(*request.position), pixmap.size())
        return QRect(QPoint(0, 0), pixmap.size())


# 全局绘制核心实例
_draw_core = None


def get_draw_core() -> DrawCore:
    """获取全局绘制核心实例（单例模式）"""
    global _draw_core
    if _draw_core is None:
        _draw_core = DrawCore()
    return _draw_core


def cleanup_draw_core():
    """清理全局绘制核心实例"""
    global _draw_core
    if _draw_core is not None:
        _draw_core.cleanup()
        _draw_core = None
