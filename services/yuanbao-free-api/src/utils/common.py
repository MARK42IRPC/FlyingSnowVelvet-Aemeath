"""通用工具函数模块"""

import asyncio
import logging
from typing import Dict, Optional

from src.services.browser import browser_manager

logger = logging.getLogger(__name__)


class HeadersUnavailableError(Exception):
    """浏览器会话暂时无法提供元宝认证头。"""


async def generate_headers(retries: int = 4, delay: float = 0.75) -> Dict[str, str]:
    """生成请求头，从浏览器管理器获取最新的认证信息

    Returns:
        Dict[str, str]: 包含认证信息的请求头

    Raises:
        HeadersUnavailableError: 无法获取请求头时抛出
    """
    last_error: Optional[Exception] = None
    attempts = max(1, int(retries))

    for attempt in range(1, attempts + 1):
        try:
            headers = await browser_manager.get_headers()
        except Exception as exc:
            last_error = exc
            logger.warning("获取元宝认证头失败，第 %s/%s 次: %s", attempt, attempts, exc)
            headers = None

        if headers:
            return dict(headers)

        if attempt < attempts:
            await asyncio.sleep(max(0.0, float(delay)))

    message = "无法获取请求头，请确保已登录，或稍后重试"
    if last_error:
        message = f"{message}: {last_error}"
    raise HeadersUnavailableError(message)
