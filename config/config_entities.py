"""???????"""

from __future__ import annotations

import config.config_runtime as _config_runtime

from config.scale import scale_px, scale_size


SNOW_LEOPARD = {
    # GIF 资源路径
    'gif_file': 'resc/GIF/snow_leopard.gif',

    # 雪豹渲染尺寸（像素）
    'size': scale_size((80, 80)),

    # 生成区域：屏幕高度占比（0.0=顶部, 1.0=底部），默认接近屏幕底部
    # spawn_y_min 为生成区域的上边界，spawn_y_max 为下边界
    'spawn_y_min': 0.85,
    'spawn_y_max': 0.95,

    # 主宠物中心锚点触发淡出的交互半径（像素）
    'interact_radius': 50,

    # 自然生成数量上限（雪堆批次/右键触发受此约束，命令生成无视此上限）
    'natural_spawn_limit': 12,

    # 弹跳力度随机倍率范围（对 vx / vy 同时生效）
    'jump_power_min': 2,
    'jump_power_max': 2.5,

    # 锚点偏移（get_center 返回值相对于几何中心的偏移）
    'anchor_offset_y': scale_px(-30),  # 垂直中心向上偏移 30 像素
}
SNOW_PILE = {
    # PNG 资源路径
    'png_file': 'resc/GIF/snow.png',

    # 基础渲染尺寸（像素），KeepAspectRatio 模式缩放，实际宽高以图片比例为准
    'size': scale_size((80, 80)),

    # 生成区域：屏幕高度占比（0.0=顶部, 1.0=底部）
    'spawn_y_min': 0.82,
    'spawn_y_max': 0.93,

    # 随机缩放比例范围（相对于基础尺寸，120%~150%）
    'scale_min': 1.2,
    'scale_max': 1.5,

    # 批次触发间隔（毫秒，min/max）：每个雪堆每隔 10~20 秒触发一批生成
    'batch_interval': (10000, 20000),

    # 每批生成数量范围（min/max）
    'batch_size': (1, 2),

    # 批次内生成间隔（毫秒，min/max）：同一批多只时每只之间的间隔
    'batch_item_interval': (3000, 5000),

    # 生成雪豹时的弹跳力度随机倍率范围
    'spawn_power_min': 3,
    'spawn_power_max': 5,
}
SOFA = {
    # PNG 资源路径
    'png_file': 'resc/GIF/sofa.png',

    # 沙发渲染尺寸（像素）
    'size': scale_size((120, 120)),

    # 生成区域：屏幕高度占比（0.0=顶部, 1.0=底部）
    'spawn_y_min': 0.8,
    'spawn_y_max': 0.9,

    # 保护半径（像素）：宠物中心进入此范围时暂停漫游计时器
    'protect_radius': 10,
}
MORTOR = {
    # PNG 资源路径
    'png_file': 'resc/GIF/mortor.png',

    # 等比缩放目标宽度（像素）
    'target_width': scale_px(400),

    # 方向键控制下的逐帧水平移动速度（像素/帧）
    'move_speed_px_per_frame': 2.0,
    # 按住方向键时每 tick 的加速度（像素/帧）
    'move_accel_per_tick': 1.0,
    # 松开方向键时每 tick 的减速度（像素/帧）
    'move_decel_per_tick': 2.0,
    # 方向键移动速度上限（像素/帧）
    'move_speed_max': 10.0,
    # 跳跃初速度（负值=向上，绝对值越大跳得越高）
    'jump_vy': -16.0,
    # 摩托出现时是否自动播放专属 BGM（“于无羁之昼点亮真彩”）
    'bgm_enabled': True,

    # 生成区域：屏幕高度占比（0.0=顶部, 1.0=底部）
    'spawn_y_min': 0.8,
    'spawn_y_max': 0.9,
}
CLOCK = {
    # PNG 资源路径
    'png_file': 'resc/GIF/clock.png',

    # 等比缩放目标宽度（像素）
    'target_width': scale_px(150),

    # 生成区域：屏幕高度占比（0.0=顶部, 1.0=底部）
    'spawn_y_min': 0.8,
    'spawn_y_max': 0.9,

    # 默认倒计时秒数
    'countdown_ss': 30,
}
SPEAKER = {
    # PNG 资源路径
    'png_file': 'resc/GIF/music.png',

    # 渲染尺寸（像素）
    'size': scale_size((150, 150)),

    # 生成区域：屏幕高度占比（0.0=顶部, 1.0=底部）
    'spawn_y_min': 0.8,
    'spawn_y_max': 0.9,
}

SNOWBALL = {
    # PNG 资源路径
    'png_file': 'resc/GIF/snowball.png',

    # 生成区域：屏幕高度占比（0.0=顶部, 1.0=底部）
    'spawn_y_min': 0.85,
    'spawn_y_max': 0.95,

    # 同时存在的雪球数量上限（FIFO，超出则淘汰最早的）
    'max_count': 16,

    # 雪球直径范围（像素）
    'size_min': 24,
    'size_max': 48,

    # 雪球自然消亡寿命范围（秒，随机取区间内均匀分布值）
    'lifetime_min': 10,
    'lifetime_max': 15,

    # 地面水平摩擦系数（触地时 vx 保留比例）
    # 默认世界常数 0.80；雪球更滑，设为 0.96 使其在地面上继续滑行
    'ground_friction': 0.96,

    # 球间碰撞弹性系数（碰撞后法线方向速度保留比例，0=完全非弹性，1=完全弹性）
    'collision_elasticity': 0.60,

    # 球间切向摩擦系数（Coulomb 模型，切向冲量上限 = ball_friction × |法向冲量|）
    # 0=无摩擦（纯弹性），越大切向能量损失越多，0.45 约等于"较涩的雪球"
    'ball_friction': 0.45,

    # 冻结解冻阈值（法向冲量，单位像素/帧）
    # 静止冻结的雪球被撞时，法向冲量 >= 此值才解冻并重新参与物理
    'freeze_impulse_threshold': 2.5,
}
OBJECTS = {
    # obj 物体统一透明度（0.0-1.0）
    'object_opacity': 1.0,
}
