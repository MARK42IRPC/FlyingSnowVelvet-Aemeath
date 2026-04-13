# 悬停提示文字配置
#
# 集中管理所有 UI 组件的 _description 属性文本。
# tooltip_panel.py 在鼠标静止约 1 秒后读取该属性并显示悬浮说明。
#
# 新增组件时，在对应分组下添加一个键值对，
# 然后在组件的 __init__ 中写：
#   from config.tooltip_config import TOOLTIPS
#   self._description = TOOLTIPS['your_key']

TOOLTIPS: dict[str, str] = {

    # ── 基础控制按钮 ──────────────────────────────────────────────
    'close_button':        '关闭并退出桌宠程序',
    'clickthrough_button': '启用后,点击将达下方窗口',
    'restore_button':      '关闭后,恢复正常交互模式',
    'scale_up_button':     '放大桌宠（重启生效）',
    'scale_down_button':   '缩小桌宠（重启生效）',
    'launch_wuwa_button':  '检测并启动鸣潮',
    'auto_chat_button':    '开启或关闭自动语聊：检测到说话时自动转文字，停顿3秒后自动发送',

    # ── 气泡 ─────────────────────────────────────────────────────
    'bubble':              '左键关闭,右键复制并关闭',

    # ── 命令系统 ─────────────────────────────────────────────────
    'command_dialog':      '输入命令',
    'command_hint_box':    '左键点击快捷执行',

    # ── 音响 / 播放器 ────────────────────────────────────────────
    'speaker_play_pause':       '暂停 / 继续',
    'speaker_next':             '下一首',
    'speaker_volume_up':        '音量 +5%',
    'speaker_volume_down':      '音量 -5%',
    'speaker_play_mode':        '切换播放模式',
    'speaker_music_login':      '登录当前音乐平台账号',
    'speaker_platform_mode':    '切换并保存当前音乐平台模式',
    'speaker_playlist_toggle':  '播放队列面板',
    'speaker_search_priority':  '切换搜索优先级（单曲/歌手/专辑/歌单）',
    'speaker_history_queue':    '将历史记录追加到播放队列',
    'speaker_local_queue':      '加载本地音乐文件夹到播放队列',
    'speaker_like_queue':       '随机载入喜欢的音乐（最多32首）',
    'playlist_panel':           '左键上移,右键下移',
    'playlist_remove_song':     '从播放队列中移除该歌曲',
    'playlist_play_now':        '立即播放该歌曲',
    'speaker_search_dialog':    '搜索当前音乐平台歌曲',
    'speaker_search_result_box':'左键播放,右键加入队列',

    # ── 系统托盘 ────────────────────────────────────────────────────
    'tray_autostart':           '切换开机自动启动',
    'tray_cleanup_desktop':     '清理其余游戏物体（含音响）',
    'tray_cleanup_cache':       '清理 temp 中音乐缓存，不清理历史与登录数据',
    'tray_cleanup_history':     '清空所有平台音乐历史与登录数据，不清理缓存',
    'tray_ai_settings':         '打开控制面板（含 AI 设置）',
    'tray_cmd_window':          '打开CMD终端窗口',
    'tray_follow_author':       '打开作者 B 站主页',
    'tray_quit':                '退出程序',
}
