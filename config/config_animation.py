"""????????/?????"""

from __future__ import annotations

import config.config_runtime as _config_runtime

from config.scale import scale_px, scale_size


ANIMATION = {
    'pet_size':  scale_size((150, 150)),  # 宠物尺寸（像素）
    'gif_fps': 16,           # GIF帧率（每秒帧数）
    'frame_fps': 120,          # 全局帧率（每秒帧数）
    'start_exit_enabled': True,  # 启用启动/退出动画（同时控制启动延时）
    'start_animation_folder': '耶比_anima',  # 启动动画序列帧目录（兼容旧目录 start-anim-compressed）
    'exit_animation_folder': '星炬学院_anima',  # 退出动画序列帧目录（兼容旧目录 exit-anim-compressed）
    'exit_shadow_strength': 255,  # 退出动画阴影强度（0~255，0=关闭）
    'exit_shadow_blur_radius': 30,  # 退出动画阴影模糊半径（像素）
    'exit_shadow_offset_direction': 'center',  # 退出动画阴影偏移方向（如 down / up / left / right / center）
}
GIF_FILES = [
    'resc/GIF/boring.gif',
    'resc/GIF/happy.gif',
    'resc/GIF/idle.gif',
    'resc/GIF/jumping.gif',
    'resc/GIF/moving.gif',
    'resc/GIF/play.gif',
    'resc/GIF/wave.gif',
]
BEHAVIOR = {
    'auto_behavior_interval': (10000, 20000),  # 自动行为间隔（毫秒）
    'auto_wander_interval':   (5000, 5000),    # 自动漫游间隔（毫秒）
    'wander_near_speaker_radius': 150,
    'random_states': ['boring', 'happy', 'wave', 'play', 'jumping'],
    'double_click_ticks': 4,   # 双击判定间隔（tick，1 tick = 50ms，默认 4 tick = 200ms）
    # 移动加速/减速参数
    'move_min_speed': 3.0,          # 最低速度（起步和结束速度，基础移速 *3）
    'move_acceleration': 0.3,       # 起步加速度（每帧增加的速度，保持原有手感比例）
    'move_max_speed': 6.0,          # 最大速度（基础移速 *3）
    'move_decel_distance': scale_px(100),  # 开始减速的距离（像素）
}
PARTICLES = {
    'enable_stroke':   False,    # 启用粒子描边（1px黑色描边）
    'fade_threshold':  0.75,     # 粒子开始淡出的生命比例（剩余生命低于此比例时才淡出）
}
PHYSICS = {
    # 雪豹跳跃参数
    'snow_leopard_jump_vx':      5.0,    # 水平跳跃速度（像素/帧 @60fps）
    'snow_leopard_jump_vy':     -13.0,    # 垂直跳跃初速度（负值=向上）
    # 沙发/音响拖拽参数
    'max_throw_vx':            25.0,      # 拖拽释放最大水平速度
    'max_throw_vy':            25.0,      # 拖拽释放最大垂直速度
    'drag_threshold':           scale_px(5),  # 拖拽判定阈值（像素）
    'max_bounces':              5,        # 最大弹跳次数
    # 地面高度（屏幕高度占比）
    'ground_y_pct':           0.9,       # 地面位置（0.0=顶部, 1.0=底部）
    # 空气阻力（每帧速度衰减比例，0=无阻力，1=瞬间停止）
    'air_resistance':          0.95,      # 每帧保留速度比例（0.95 = 每帧损失5%）
    'min_velocity':            0.5,       # 速度低于此值视为静止
    # 淡出参数
    'fade_step':             0.05,        # 每次淡出步长
    'fade_interval_ms':        50,        # 淡出帧间隔（毫秒）
    # 自动翻转间隔（毫秒）
    'flip_interval_min':     5000,
    'flip_interval_max':     8000,
}
