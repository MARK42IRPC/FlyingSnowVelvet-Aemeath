"""Small bounded cleanup tasks scheduled during application startup."""

from __future__ import annotations

from lib.core.compute_hub import get_compute_hub
from lib.core.cuda_runtime_cleanup import cleanup_obsolete_cuda_runtime_artifacts
from lib.core.forum_cache import clear_cache as clear_forum_cache
from lib.core.logger import get_logger


logger = get_logger(__name__)


def schedule_startup_cleanup() -> None:
    """Remove the obsolete onnx-cuda runtime without delaying the first window."""

    def worker() -> None:
        cleanup_forum_cache_files()
        report = cleanup_obsolete_cuda_runtime_artifacts()
        if report.removed:
            logger.info(
                "已清理 %s 个过时 onnx-cuda 运行时条目",
                len(report.removed),
            )
        for message in report.errors:
            logger.warning("onnx-cuda 运行时启动清理未完成: %s", message)
        if report.skipped:
            logger.warning(
                "onnx-cuda 运行时启动清理跳过 %s 个重解析路径",
                len(report.skipped),
            )

    try:
        get_compute_hub().submit_io(worker)
    except Exception as exc:
        logger.debug("CUDA 语音运行时启动清理任务提交失败: %s", exc)


def cleanup_forum_cache_files() -> None:
    """清掉雪绒社区的浏览缓存（列表快照 / 标签云）。

    缓存本身可再生，启动时清一次让「看到的不是最新内容」这件事最多只发生在一次
    运行之内；账号页另有手动清理按钮，两处走同一个实现。
    """
    try:
        report = clear_forum_cache()
    except Exception as exc:
        logger.warning("社区浏览缓存启动清理失败: %s", exc)
        return
    if report.files:
        logger.info("已清理社区浏览缓存 %s 个文件、%s 字节", report.files, report.bytes)
    for message in report.errors:
        logger.warning("社区浏览缓存启动清理未完成: %s", message)


__all__ = ["cleanup_forum_cache_files", "schedule_startup_cleanup"]
