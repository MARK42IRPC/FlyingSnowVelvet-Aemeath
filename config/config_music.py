"""??????????"""

from __future__ import annotations

import config.config_runtime as _config_runtime

from config.scale import scale_px


SOUND = {
    # 总音量系数（0.0-1.0），与各音频类申请的响度相乘得到实际播放音量
    'master_volume': 0.68,
    # 主宠物音效音量系数（0.0-1.0）
    'main_pet_volume': 0.4,
    # 游戏物体音效音量系数（0.0-1.0）
    'game_object_volume': 0.9,
}
SPEAKER_AUDIO = {
    'scale_range':   0.1,    # 缩放范围：[1.0 - scale_range, 1.0 + scale_range]
    'scale_exp':     2.0,    # 指数映射：freq_intensity^scale_exp
    'ema_attack':    0.35,   # EMA 攻击系数（峰值上升）
    'ema_decay':     0.08,   # EMA 衰减系数（峰值下降）
    'freq_min':    200.0,    # 关注频率范围下限（Hz）
    'freq_max':   2000.0,    # 关注频率范围上限（Hz）
}
SPEAKER_SEARCH_UI = {
    'input_width':      scale_px(160),  # 输入框宽度（像素）
    'button_width':      scale_px(80),  # 搜索按钮宽度（像素）
    'height':            scale_px(36),  # 总高度（像素）
    'border':            scale_px(4),   # 边框厚度（像素）
    'gap':               scale_px(6),   # 与音响的水平间距（像素）
    # 颜色配置（RGB）
    'border_color':   (0,   0,   0),      # 黑色外框
    'mid_color':      (173, 216, 230),    # 浅青色中框
    'bg_color':       (255, 182, 193),    # 淡粉色背景
    'text_color':     (0,   0,   0),      # 黑色字体
    'entry_bg_color': (255, 255, 255),    # 输入框白色背景
}
CLOUD_MUSIC = {
    # 当前音乐平台（抽象层路由入口，后续可扩展 qq / kugou）
    'provider': 'qq',
    'bitrate_ladder':   (320000, 192000, 128000),  # 音质梯度（bps）
    'default_volume':   0.2,                      # 默认音量（15%）
    'pygame_init_wait': 5,                         # pygame 初始化最大等待时间（秒）
    'particle_interval': 60,                       # 音符粒子生成间隔（帧数）
    'search_result_limit': 128,                    # 音响搜索结果上限（首）
    # 缓存目录（相对于项目根目录）
    'search_fallback_enabled': True,               # 当前平台搜索不足或失败时自动补充其它平台
    'search_fallback_order': ('qq', 'kugou', 'netease'),  # 多源搜索回退顺序
    'search_append_source_label': True,            # 混合搜索结果标注来源平台
    'cache_dir': 'resc/user/temp',
    # 本地音乐目录（支持绝对路径；相对路径按项目根目录解析，默认空）
    'local_music_dir': '',
    # 启动鸣潮路径文件（支持 bat/lnk/exe，留空则走桌面快捷方式自动检测）
    'launch_wuwa_path': '',
}
