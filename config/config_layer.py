"""统一绘制层级配置。

数值越大越靠前。修改后需要重启程序生效。
同一层级且 z 相同时，组件按生成顺序后来居上。
"""


LAYER_VALUES = {
    'BACKGROUND': 0,
    'WORLD_OBJECT': 100,
    'MAIN_PET': 850,
    'PET_EFFECT_BELOW': 250,
    'PARTICLE': 650,
    'EFFECT': 660,
    'PET_UI': 640,
    'PANEL': 600,
    'DIALOG': 700,
    'TOOLTIP': 800,
    'SYSTEM_MODAL': 900,
}


__all__ = ['LAYER_VALUES']
