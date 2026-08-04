"""桌面截图捕获工具。"""


def capture_screen() -> list[bytes] | None:
    """
    捕获主屏幕截图并返回字节数组列表（Ollama 多模态格式）。

    Returns:
        包含单个图片字节数据的列表，失败时返回 None
    """
    from lib.core.desktop_backend import get_screen_capture_provider

    provider = get_screen_capture_provider()
    image_data = provider() if provider is not None else None
    return [image_data] if image_data else None
