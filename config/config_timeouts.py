"""??????????"""

from __future__ import annotations

TOOL_DISPATCHER = {
    # 触发工具调用的正则模式（捕获组1=指令，捕获组2=参数可选），兼容 ###指令### / ### 指令 参数 ### / ###指令：参数###
    'tool_pattern': r'###\s*(\S+?)(?:[\s：:，,;；]+(.+?))?\s*###',
    # 搜索结果取第几首（0=第一首）
    'play_index': 0,
    # 场上无音响时自动生成的数量
    'auto_spawn_speaker_count': 1,
}
TIMEOUTS = {
    'api_list':          2,       # 获取 API 模型列表超时
    'api_request':      10,       # API 请求超时
    'ollama_pull_read': 30,       # Ollama 模型下载单次读取超时
    'login_wait':       30,       # 登录等待超时
    'login_call':       20,       # 单次登录接口调用软超时
    'cmd_exec':         30,       # 命令执行超时
    'idle_close_ms': 10000,       # UI 空闲自动关闭时间（毫秒）
}
