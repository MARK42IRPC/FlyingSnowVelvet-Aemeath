"""Small bounded cleanup tasks scheduled during application startup."""

from __future__ import annotations

from lib.core.compute_hub import get_compute_hub
from lib.core.cuda_runtime_cleanup import cleanup_obsolete_cuda_runtime_artifacts
from lib.core.logger import get_logger


logger = get_logger(__name__)


def schedule_startup_cleanup() -> None:
    """Clean stale CUDA runtime artifacts without delaying the first window."""

    def worker() -> None:
        report = cleanup_obsolete_cuda_runtime_artifacts()
        if report.removed:
            logger.info(
                "已清理 %s 个过时 CUDA 语音运行时或残留包体",
                len(report.removed),
            )
        for message in report.errors:
            logger.warning("CUDA 语音运行时启动清理未完成: %s", message)
        if report.skipped:
            logger.warning(
                "CUDA 语音运行时启动清理跳过 %s 个重解析路径",
                len(report.skipped),
            )

    try:
        get_compute_hub().submit_io(worker)
    except Exception as exc:
        logger.debug("CUDA 语音运行时启动清理任务提交失败: %s", exc)


__all__ = ["schedule_startup_cleanup"]
