"""AI 设置面板的文案与名称表（无 Qt）。

从 `ai_settings_panel.py` 拆出的纯数据 + 纯函数：配置分组的帮助文案、配置字典/键的
中文名、以及把它们查出来的几个小函数。这一族不碰 Qt、不碰 UI 装配、不读 config，
只把"键 -> 给人看的文字"的映射集中在一处，供面板构建控件树时查用。

拆出它的理由同 `ai_settings_contributions.py`：它是纯逻辑，和 5000 行的面板控件挤在
一个文件里没有道理，拆出后主文件只剩控件代码。
"""

from __future__ import annotations

#: 各配置分组「问号」按钮弹出的说明。键是 `(分类 id, 配置字典名)`，
#: 值是给用户看的整段文字。没登记的组合不出问号——宁可没有入口，
#: 也不要弹一个空窗口出来。换行用 `\n`，帮助窗口按普通文本原样呈现。
SECTION_HELP_TEXTS: dict[tuple[str, str], str] = {
    ("ui_anim", "ANIMATION"): (
        "这里管桌宠自己的动画节奏。\n\n"
        "「帧率」是主循环推进动画的速度，调低会更省电，但动作看起来会一顿一顿的；"
        "「GIF 帧率」只影响循环播放的动图这一类资源。\n\n"
        "「启动/退出动画」可以换成自己喜欢的动画文件夹，时长按帧数和帧率换算，"
        "帧数越多、时长越短，动作就越快。退出时的阴影强度、模糊半径和方向只作用于"
        "退出动画那一帧的效果。\n\n"
        "改完建议重启程序，动画资源是启动时加载的。"
    ),
    ("ui_anim", "UI"): (
        "这里管所有界面窗口的外观与手感。\n\n"
        "「桌宠透明度」只改桌宠本身；「界面控件透明度」改的是面板、气泡这一类控件；"
        "「提示框透明度」单独管鼠标悬停提示。三个都是各自独立生效的。\n\n"
        "「淡入淡出时长」决定窗口出现和消失时那段渐变动画有多长，调小会更干脆，"
        "调到很小基本就是瞬间切换。\n\n"
        "「鼠标远离距离」是命令框自动收起前允许鼠标离开多远。\n\n"
        "「渲染后端」切换 Qt 与 DX 两套绘制实现，需要重启程序才会真正换过去。"
    ),
    ("ui_anim", "COMMAND_DIALOG"): (
        "命令框的输入保持多久算「还在用」。\n\n"
        "超过这个空闲时间没有输入，命令框会自己收起并中断当前这次输入，"
        "免得它一直停在桌面上挡住别的东西。"
    ),
    ("behavior_physics", "PARTICLES"): (
        "桌宠活动时飘出来的那层粒子。\n\n"
        "「描边」打开后每颗粒子会多一圈轮廓，粒子小而密的时候更容易看清；"
        "「淡出阈值」是粒子透明到多少就彻底不再绘制，调高会让粒子更早消失、更省一点性能，"
        "调低则粒子会在很淡的状态下多留一会儿。"
    ),
    ("behavior_physics", "BEHAVIOR"): (
        "桌宠平时怎么走、怎么跟周围的东西打交道。\n\n"
        "「音响徘徊半径」是桌宠路过音响时会在多近的距离内被吸引过去；"
        "「双击判定」是按几次 tick 来区分单击和双击，调小会让双击更容易触发，"
        "但也更容易把两次慢点击误判成双击。\n\n"
        "「移动速度」三项分别是最高速度、加速度和最低速度，决定了它从静止走到全速"
        "要多久、以及慢速挪动时有多慢。"
    ),
    ("behavior_physics", "PHYSICS"): (
        "桌宠被扔出去之后怎么落地和弹跳。\n\n"
        "「最大弹跳次数」用完就停下不再反弹；「地面高度」是屏幕高度的百分比，"
        "调大等于把地面往下压；「空气阻力」越大，飞出去之后减速越快。"
    ),
    ("audio_music", "AUDIO_VOLUMES"): (
        "各路人声与音效的独立音量。\n\n"
        "这些音量是乘法叠加的：某一个调成 0 就是这一类彻底静音，"
        "而不影响别的类别。想整体变小，可以先只动「总音量」这一项。"
    ),
    ("audio_music", "VOICE"): (
        "语音合成与麦克风输入。\n\n"
        "上面一块决定桌宠说话用什么音色和语速，以及一次能合成多长的文本；"
        "下面一块决定麦克风怎么听、听多久判定说完、以及识别结果交给谁。\n\n"
        "麦克风相关项改错会让桌宠听不清或一直抢话，不确定时先保持默认。"
    ),
    ("audio_music", "SPEAKER_AUDIO"): (
        "音响上的动感效果跟着声音怎么动。\n\n"
        "「频段」决定响应哪一段频率的声音：范围收窄到低频，音响只跟鼓点跳；"
        "拉到高频则只跟人声和镲片这类声音动作。\n\n"
        "每个音响还能在右键菜单右侧的竖滑条上单独调自己的响应频段：滑条上只有一个块，"
        "块的位置就是中心频率，响应范围固定为中心 ±10Hz，按 10Hz 步进吸附；改完只影响"
        "那一个音响，重启后回到这里的默认值。\n\n"
        "「灵敏度」和「平滑」控制反应的幅度与跟手的快慢，平滑调大动作更柔和、"
        "但会慢半拍。"
    ),
    ("audio_music", "CLOUD_MUSIC"): (
        "云音乐账号与播放行为。\n\n"
        "登录信息保存在本机，清理登录数据会一并清掉。\n\n"
        "音质这一项只在账号有对应权限时才会真正生效，没有权限时会自动回退到可用的档位。"
    ),
    ("scene_objects", "SNOW_LEOPARD"): (
        "雪豹的生成与交互参数。\n\n"
        "「生成高度」用屏幕高度的百分比表示，两条一起决定它可能出现的高度范围。\n\n"
        "「交互半径」是鼠标要多近才算碰到它；「自然生成上限」限制同一时间最多同时存在几只，"
        "调太大会明显吃性能。「跳跃力度」决定被点起来之后跳多高。"
    ),
    ("scene_objects", "SNOW_PILE"): (
        "雪堆的生成、大小与批量出雪豹的节奏。\n\n"
        "「生成高度」是屏幕高度百分比；「大小」是缩放范围，两端可以不一样大。\n\n"
        "批量参数决定右键雪堆之后每隔多久出一只、「一批出几只」以及一只接一只之间的间隔，"
        "三项都调小会瞬间刷出一大群雪豹，慎用。"
    ),
    ("scene_objects", "SOFA"): (
        "沙发出现的位置，以及它的「保护半径」。\n\n"
        "保护半径内的其他对象不会被生成或移动进来，避免有东西压在沙发上。"
    ),
    ("scene_objects", "MORTOR"): (
        "摩托的生成位置、移动速度，以及是否播放它自带的背景音乐。\n\n"
        "速度的单位是每帧像素数，帧率越高实际移动越快。"
    ),
    ("scene_objects", "CLOCK"): (
        "闹钟出现在屏幕的哪个高度，以及倒计时持续多久。"
    ),
    ("scene_objects", "SPEAKER"): (
        "音响出现的高度范围。\n\n"
        "音响的音量与动感响应频段请在音响自己的右键菜单里单独调整，"
        "这里只管它出在屏幕的哪个高度。"
    ),
    ("scene_objects", "SNOWBALL"): (
        "雪球的生成范围与交互参数。"
    ),
    ("scene_objects", "OBJECTS"): (
        "通用物体参数，作用于没有单独列出的那些桌面对象。"
    ),
    ("system_dispatch", "TIMEOUTS"): (
        "各条链路的等待上限。\n\n"
        "调小会让卡住的操作更快失败并给出提示，代价是网络慢的时候容易误判超时；"
        "调大则相反——不容易误判，但真出问题时用户要等更久。"
    ),
    ("system_dispatch", "TOOL_DISPATCHER"): (
        "AI 请求工具（打开网页、搜索一类）时怎么调度。\n\n"
        "并发数决定同时最多跑几个工具，调大更快但更吃资源；"
        "重试次数与间隔决定失败后要不要再试一次。"
    ),
    ("system_dispatch", "CLOUD_MUSIC"): (
        "与《鸣潮》相关的设置。\n\n"
        "「启动路径」用于让桌宠找到游戏本体，路径填错只会让相关功能失效，"
        "不会影响其他功能。"
    ),
    ("system_dispatch", "DRAW"): (
        "绘制相关的运行参数，主要影响画面刷新与渲染开销。\n\n"
        "不确定的时候保持默认即可。"
    ),
    ("system_dispatch", "STARTUP"): (
        "启动时的行为。\n\n"
        "「确保桌面快捷方式」会在每次启动时检查并补回缺失的快捷方式，"
        "同时负责开机启动项的迁移；关掉之后不会再自动维护这些入口。"
    ),
    ("system_dispatch", "LAYER_VALUES"): (
        "全局绘制层级的顺序。\n\n"
        "数值越大越靠前：两个窗口/元素同时出现时，层的数值大的那一方显示在上面。"
        "同一层且 z 相同时，后生成的排在上面；改完保存后都需要重启程序才会生效。\n\n"
        "不确定时不要动，调到相同数值会让先后顺序变得不稳定。"
    ),
}


def section_help_text(category_id: str, dict_name: str) -> str:
    """取某个配置分组的帮助文案；没登记过就返回空串（此时不显示问号）。"""
    return SECTION_HELP_TEXTS.get((str(category_id), str(dict_name)), "")

DICT_FRIENDLY_NAME = {
    "UI": "界面",
    "ANIMATION": "动画",
    "BUBBLE_CONFIG": "气泡",
    "COMMAND_DIALOG": "命令框",
    "BEHAVIOR": "行为",
    "PHYSICS": "物理",
    "PARTICLES": "粒子",
    "AUDIO_VOLUMES": "音量控制",
    "SOUND": "音量",
    "VOICE": "语音",
    "SPEAKER_AUDIO": "音频可视化",
    "CLOUD_MUSIC": "云音乐",
    "SNOW_LEOPARD": "雪豹",
    "SNOW_PILE": "雪堆",
    "SOFA": "沙发",
    "MORTOR": "摩托",
    "CLOCK": "闹钟",
    "SPEAKER": "音响",
    "OBJECTS": "物体",
    "SNOWBALL": "雪球",
    "TIMEOUTS": "超时",
    "TOOL_DISPATCHER": "工具调度",
    "DRAW": "绘制",
    "LAYER_VALUES": "图层顺序",
    "STARTUP": "启动",
}

KEY_FRIENDLY_NAME = {
    "UI": {
        "cmd_window_width": "命令框宽度",
        "cmd_window_height": "命令框高度",
        "bubble_max_width": "气泡最大宽度",
        "pet_opacity": "桌宠透明度",
        "ui_widget_opacity": "UI控件透明度",
        "tooltip_opacity": "悬浮说明透明度",
        "ui_fade_duration": "淡入淡出时长(ms)",
        "auto_hide_mouse_distance": "自动关闭阈值",
        "render_backend": "渲染后端",
    },
    "ANIMATION": {
        "pet_size": "宠物尺寸",
        "gif_fps": "GIF帧率",
        "frame_fps": "帧率",
        "start_exit_enabled": "启动/退出动画",
        "start_animation_duration": "启动动画倍速",
        "exit_animation_duration": "退出动画倍速",
        "start_animation_folder": "启动序列帧目录",
        "exit_animation_folder": "退出序列帧目录",
        "exit_shadow_strength": "退出阴影强度",
        "exit_shadow_blur_radius": "退出阴影模糊半径(px)",
        "exit_shadow_offset_direction": "退出阴影偏移方向",
    },
    "STARTUP": {
        "ensure_desktop_shortcut": "启动时创建快捷方式",
        "log_retention_count": "日志保留数量",
        "ui_cache_preload": "启动期预绘制缓存",
    },
    "BUBBLE_CONFIG": {
        "default_min_ticks": "默认最小显示tick",
        "default_max_ticks": "默认最大显示tick",
        "padding": "气泡内边距",
        "border_width": "气泡边框宽度",
        "default_persona_file": "默认人格文件",
    },
    "COMMAND_DIALOG": {
        "idle_timeout_ms": "自动关闭时间(ms)",
        "offset_x": "水平偏移",
        "offset_y": "垂直偏移",
    },
    "BEHAVIOR": {
        "auto_behavior_interval": "自动行为间隔(ms)",
        "auto_wander_interval": "自动漫游间隔(ms)",
        "wander_near_speaker_radius": "音响漫游半径",
        "random_states": "随机状态列表",
        "double_click_ticks": "双击判定",
        "move_min_speed": "最小移动速度",
        "move_acceleration": "移动加速度",
        "move_max_speed": "最大移动速度",
        "move_decel_distance": "减速距离",
    },
    "PHYSICS": {
        "snow_leopard_jump_vx": "雪豹跳跃水平速度",
        "snow_leopard_jump_vy": "雪豹跳跃垂直速度",
        "max_throw_vx": "最大抛掷水平速度",
        "max_throw_vy": "最大抛掷垂直速度",
        "drag_threshold": "拖拽阈值",
        "max_bounces": "最大弹跳次数",
        "ground_y_pct": "地面高度比例",
        "air_resistance": "空气阻力",
        "min_velocity": "静止速度阈值",
        "fade_step": "淡出步长",
        "fade_interval_ms": "淡出间隔(ms)",
        "flip_interval_min": "自动翻转最小间隔(ms)",
        "flip_interval_max": "自动翻转最大间隔(ms)",
    },
    "PARTICLES": {
        "enable_stroke": "启用粒子描边",
        "fade_threshold": "淡出阈值",
    },
    "SOUND": {
        "master_volume": "总音量",
        "main_pet_volume": "主宠物语音音量",
        "game_object_volume": "特效音量",
    },
    "VOICE": {
        "voice_volume": "AI语音音量",
        "lahai_skill_release_volume": "拉海洛技能语音音量",
        "microphone_push_to_talk_key": "语聊快捷键(留空禁用)",
        "microphone_silence_timeout_secs": "静音停止时长(s)",
        "microphone_speech_rms_threshold": "说话判定阈值",
        "microphone_denoise_enabled": "启用语音降噪",
        "microphone_denoise_strength": "降噪强度",
        "microphone_noise_gate_threshold": "噪声门阈值",
    },
    "SPEAKER_AUDIO": {
        "scale_range": "缩放范围",
        "scale_exp": "缩放指数",
        "response_gain": "响应增益",
        "ema_attack": "EMA攻击系数",
        "ema_decay": "EMA衰减系数",
        "freq_min": "最低频率(Hz)",
        "freq_max": "最高频率(Hz)",
        "band_slider_min_hz": "频段滑条下限(Hz)",
        "band_slider_max_hz": "频段滑条上限(Hz)",
        "level_floor_db": "强度下限(dB)",
        "level_ceil_db": "强度上限(dB)",
    },
    "CLOUD_MUSIC": {
        "provider": "音乐平台",
        "bitrate_ladder": "音质梯度(bps)",
        "default_volume": "音乐音量",
        "particle_interval": "音符粒子间隔(帧)",
        "search_result_limit": "搜索结果上限(首)",
        "cache_dir": "缓存目录",
        "local_music_dir": "本地音乐文件夹",
        "launch_wuwa_path": "启动鸣潮路径文件",
    },
    "SNOW_LEOPARD": {
        "gif_file": "GIF资源路径",
        "size": "渲染尺寸",
        "spawn_y_min": "生成高度最小值",
        "spawn_y_max": "生成高度最大值",
        "interact_radius": "交互半径",
        "natural_spawn_limit": "自然生成上限",
        "jump_power_min": "跳跃力度最小倍率",
        "jump_power_max": "跳跃力度最大倍率",
        "anchor_offset_y": "锚点Y偏移",
    },
    "SNOW_PILE": {
        "png_file": "PNG资源路径",
        "size": "渲染尺寸",
        "spawn_y_min": "生成高度最小值",
        "spawn_y_max": "生成高度最大值",
        "scale_min": "随机缩放最小倍率",
        "scale_max": "随机缩放最大倍率",
        "batch_interval": "批次间隔(ms)",
        "batch_size": "批次数量范围",
        "batch_item_interval": "批次内间隔(ms)",
        "spawn_power_min": "生成力度最小倍率",
        "spawn_power_max": "生成力度最大倍率",
    },
    "SOFA": {
        "png_file": "PNG资源路径",
        "size": "渲染尺寸",
        "spawn_y_min": "生成高度最小值",
        "spawn_y_max": "生成高度最大值",
        "protect_radius": "保护半径",
    },
    "MORTOR": {
        "png_file": "PNG资源路径",
        "target_width": "目标宽度",
        "move_speed_px_per_frame": "移动速度(像素/帧)",
        "move_accel_per_tick": "按键加速度",
        "move_decel_per_tick": "松键减速度",
        "move_speed_max": "最大移动速度",
        "jump_vy": "跳跃垂直速度",
        "bgm_enabled": "摩托BGM",
        "spawn_y_min": "生成高度最小值",
        "spawn_y_max": "生成高度最大值",
    },
    "CLOCK": {
        "png_file": "PNG资源路径",
        "target_width": "目标宽度",
        "spawn_y_min": "生成高度最小值",
        "spawn_y_max": "生成高度最大值",
        "countdown_ss": "默认倒计时秒",
    },
    "SPEAKER": {
        "png_file": "PNG资源路径",
        "size": "渲染尺寸",
        "spawn_y_min": "生成高度最小值",
        "spawn_y_max": "生成高度最大值",
    },
    "OBJECTS": {
        "object_opacity": "物体透明度",
    },
    "SNOWBALL": {
        "png_file": "PNG资源路径",
        "max_count": "最大存在数量",
        "spawn_y_min": "生成高度最小值",
        "spawn_y_max": "生成高度最大值",
        "size_min": "最小直径(px)",
        "size_max": "最大直径(px)",
        "lifetime_min": "最短寿命(秒)",
        "lifetime_max": "最长寿命(秒)",
    },
    "TIMEOUTS": {
        "api_list": "API模型列表超时(s)",
        "api_request": "API请求超时(s)",
        "login_wait": "登录等待超时(s)",
        "login_call": "登录调用超时(s)",
        "cmd_exec": "命令执行超时(s)",
        "idle_close_ms": "空闲关闭(ms)",
    },
    "TOOL_DISPATCHER": {
        "tool_pattern": "工具触发正则",
        "play_index": "搜索结果播放索引",
        "auto_spawn_speaker_count": "自动生成音响数量",
    },
    "DRAW": {
        "scale": "绘制缩放",
        "screen_width": "屏幕宽度",
        "screen_height": "屏幕高度",
        "scale_rule": "缩放规则",
    },
    "LAYER_VALUES": {
        "BACKGROUND": "背景",
        "WORLD_OBJECT": "世界物体",
        "MAIN_PET": "桌宠本体",
        "PET_EFFECT_BELOW": "桌宠底层特效",
        "PARTICLE": "粒子",
        "EFFECT": "特效",
        "PET_UI": "桌宠界面",
        "PANEL": "面板",
        "DIALOG": "对话框",
        "TOOLTIP": "提示框",
        "SYSTEM_MODAL": "系统模态窗",
    },
}


def friendly_section_name(dict_name: str, fallback: str = "") -> str:
    if fallback and fallback != dict_name:
        return fallback
    return DICT_FRIENDLY_NAME.get(dict_name, fallback or dict_name)


def friendly_field_section_name(dict_name: str, key: str) -> str:
    if str(dict_name) == "CLOUD_MUSIC" and str(key) == "launch_wuwa_path":
        return "鸣潮设置"
    return friendly_section_name(dict_name, dict_name)


def friendly_key_name(dict_name: str, key: str) -> str:
    return KEY_FRIENDLY_NAME.get(dict_name, {}).get(key, key)


def animation_folder_display_name(folder_name: str) -> str:
    text = str(folder_name or "").strip()
    if text.endswith("_anima"):
        text = text[:-6]
    return text or str(folder_name or "")
