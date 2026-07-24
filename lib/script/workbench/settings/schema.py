"""Page-level schema for the settings areas exposed by the workbench."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SettingsSectionSpec:
    config_key: str
    title: str


@dataclass(frozen=True)
class SettingsPageSpec:
    page_id: str
    tab_title: str
    title: str
    description: str
    sections: tuple[SettingsSectionSpec, ...] = ()

SETTINGS_PAGE_SPECS = (
    SettingsPageSpec(
        "ui_anim",
        "界面动画",
        "界面与动画配置",
        "统一调整界面呈现、窗口交互与动画行为。",
        (
            SettingsSectionSpec("ANIMATION", "动画"),
            SettingsSectionSpec("UI", "界面"),
            SettingsSectionSpec("COMMAND_DIALOG", "命令框"),
        ),
    ),
    SettingsPageSpec(
        "behavior_physics",
        "行为物理",
        "行为与物理配置",
        "管理桌宠移动、交互判定、粒子与物理反馈。",
        (
            SettingsSectionSpec("PARTICLES", "粒子"),
            SettingsSectionSpec("BEHAVIOR", "行为"),
            SettingsSectionSpec("PHYSICS", "物理"),
        ),
    ),
    SettingsPageSpec(
        "audio_music",
        "音频音乐",
        "音频与音乐配置",
        "集中管理音量、麦克风、语音可视化和云音乐。",
        (
            SettingsSectionSpec("AUDIO_VOLUMES", "音量控制"),
            SettingsSectionSpec("VOICE", "语音与麦克风"),
            SettingsSectionSpec("SPEAKER_AUDIO", "音频可视化"),
            SettingsSectionSpec("CLOUD_MUSIC", "云音乐"),
        ),
    ),
    SettingsPageSpec(
        "scene_objects",
        "场景对象",
        "场景对象配置",
        "调整桌面场景中各类对象的生成范围与交互参数。",
        (
            SettingsSectionSpec("SNOW_LEOPARD", "雪豹"),
            SettingsSectionSpec("SNOW_PILE", "雪堆"),
            SettingsSectionSpec("SOFA", "沙发"),
            SettingsSectionSpec("MORTOR", "摩托"),
            SettingsSectionSpec("CLOCK", "闹钟"),
            SettingsSectionSpec("SPEAKER", "音响"),
            SettingsSectionSpec("SNOWBALL", "雪球"),
            SettingsSectionSpec("OBJECTS", "物体"),
        ),
    ),
    SettingsPageSpec(
        "system_dispatch",
        "系统调度",
        "系统与调度配置",
        "维护超时、工具调度、启动行为和运行参数。",
        (
            SettingsSectionSpec("TIMEOUTS", "超时"),
            SettingsSectionSpec("TOOL_DISPATCHER", "工具调度"),
            SettingsSectionSpec("CLOUD_MUSIC", "鸣潮设置"),
            SettingsSectionSpec("DRAW", "绘制"),
            SettingsSectionSpec("STARTUP", "启动"),
        ),
    ),
    SettingsPageSpec(
        "desktop_pet_update",
        "桌宠更新",
        "桌宠更新管理",
        "检查稳定版本、同步开发版本，或手动获取完整安装包。",
    ),
    SettingsPageSpec(
        "contribution_list",
        "贡献列表",
        "贡献列表",
        "查看项目贡献者及其负责领域。",
    ),
    SettingsPageSpec(
        "sponsor_author",
        "赞助作者",
        "赞助作者",
        "通过本地赞助码或爱发电支持项目维护。",
    ),
)


GENERAL_CONFIG_CATEGORIES = SETTINGS_PAGE_SPECS
