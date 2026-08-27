"""Page metadata and routing registry for the unified workbench."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Iterable


PageFactory = Callable[[], object]


@dataclass(frozen=True)
class WorkbenchPageSpec:
    page_id: str
    title: str
    group: str
    description: str = ""
    keywords: tuple[str, ...] = ()
    factory: PageFactory | None = None
    show_in_navigation: bool = True

    def __post_init__(self) -> None:
        page_id = str(self.page_id or "").strip()
        title = str(self.title or "").strip()
        group = str(self.group or "").strip()
        if not page_id:
            raise ValueError("workbench page_id cannot be empty")
        if not title:
            raise ValueError(f"workbench page {page_id!r} must have a title")
        if not group:
            raise ValueError(f"workbench page {page_id!r} must have a group")

        normalized_keywords = tuple(
            keyword
            for keyword in (str(value or "").strip() for value in self.keywords)
            if keyword
        )
        object.__setattr__(self, "page_id", page_id)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "group", group)
        object.__setattr__(self, "description", str(self.description or "").strip())
        object.__setattr__(self, "keywords", normalized_keywords)

    @property
    def searchable_text(self) -> str:
        return " ".join((self.page_id, self.title, self.group, self.description, *self.keywords)).casefold()

    def with_factory(self, factory: PageFactory | None) -> "WorkbenchPageSpec":
        return replace(self, factory=factory)


class WorkbenchPageRegistry:
    def __init__(self) -> None:
        self._pages: dict[str, WorkbenchPageSpec] = {}

    def register(self, spec: WorkbenchPageSpec) -> WorkbenchPageSpec:
        if spec.page_id in self._pages:
            raise ValueError(f"duplicate workbench page_id: {spec.page_id}")
        self._pages[spec.page_id] = spec
        return spec

    def extend(self, specs: Iterable[WorkbenchPageSpec]) -> None:
        for spec in specs:
            self.register(spec)

    def get(self, page_id: str) -> WorkbenchPageSpec | None:
        return self._pages.get(str(page_id or ""))

    def require(self, page_id: str) -> WorkbenchPageSpec:
        spec = self.get(page_id)
        if spec is None:
            raise KeyError(page_id)
        return spec

    def all(self) -> tuple[WorkbenchPageSpec, ...]:
        return tuple(self._pages.values())

    def navigation_pages(self) -> tuple[WorkbenchPageSpec, ...]:
        return tuple(spec for spec in self._pages.values() if spec.show_in_navigation)

    def search(self, query: str, *, navigation_only: bool = False) -> tuple[WorkbenchPageSpec, ...]:
        candidates = self.navigation_pages() if navigation_only else self.all()
        terms = tuple(part.casefold() for part in str(query or "").split() if part)
        if not terms:
            return candidates
        return tuple(spec for spec in candidates if all(term in spec.searchable_text for term in terms))


_PAGE_PRESENTATION: dict[str, dict] = {
    "overview": {
        "title": "总览",
        "group": "工作台",
        "description": "常用设置与维护入口",
        "keywords": ("主页", "快捷", "状态"),
    },
    "ai": {
        "title": "AI 与对话",
        "group": "智能交互",
        "description": "模型、接口、记忆、自动陪伴与语音生成",
        "keywords": ("模型", "API", "元宝", "记忆", "语音"),
    },
    "office": {
        "title": "办公模式",
        "group": "智能交互",
        "description": "任务历史、实时执行状态、推理和权限管理",
        "keywords": ("vibe coding", "编码", "任务", "DSH", "办公"),
    },
    "ui_anim": {
        "title": "界面与动画",
        "group": "桌宠与场景",
        "description": "显示、动画与命令框",
        "keywords": ("界面", "动画", "透明度", "帧率", "命令框"),
    },
    "behavior_physics": {
        "title": "行为与物理",
        "group": "桌宠与场景",
        "description": "桌宠行为、移动、粒子与物理参数",
        "keywords": ("行为", "移动", "物理", "粒子"),
    },
    "scene_objects": {
        "title": "场景对象",
        "group": "桌宠与场景",
        "description": "雪豹、雪堆、沙发、音响与其他对象",
        "keywords": ("雪豹", "雪堆", "沙发", "摩托", "闹钟", "音响", "雪球"),
    },
    "audio_music": {
        "title": "音频与音乐",
        "group": "声音与媒体",
        "description": "音量、麦克风、语音、云音乐与音频可视化",
        "keywords": ("音量", "麦克风", "语音", "音乐", "音响"),
    },
    "game_manager": {
        "title": "游戏包",
        "group": "扩展与游戏",
        "description": "安装、查看和运行游戏扩展包",
        "keywords": ("游戏", "扩展", "安装", "package"),
    },
    "system_dispatch": {
        "title": "系统与调度",
        "group": "系统与维护",
        "description": "启动、工具调度、超时与绘制参数",
        "keywords": ("系统", "启动", "调度", "超时", "绘制"),
    },
    "desktop_pet_update": {
        "title": "桌宠更新",
        "group": "系统与维护",
        "description": "版本检查与更新管理",
        "keywords": ("更新", "版本", "同步", "开发版"),
    },
    "bug_tracker": {
        "title": "故障跟踪",
        "group": "系统与维护",
        "description": "日志筛选、问题定位与诊断导出",
        "keywords": ("故障", "日志", "错误", "诊断", "bug"),
    },
    "contribution_list": {
        "title": "贡献者",
        "group": "关于",
        "description": "项目贡献者列表",
        "keywords": ("贡献", "作者", "关于"),
        "show_in_navigation": False,
    },
    "sponsor_author": {
        "title": "赞助作者",
        "group": "关于",
        "description": "项目赞助入口",
        "keywords": ("赞助", "作者", "关于"),
        "show_in_navigation": False,
    },
}


def default_page_spec(
    page_id: str,
    fallback_title: str | None = None,
    *,
    factory: PageFactory | None = None,
) -> WorkbenchPageSpec:
    normalized_id = str(page_id or "").strip()
    presentation = _PAGE_PRESENTATION.get(normalized_id, {})
    title = presentation.get("title") or str(fallback_title or normalized_id).strip()
    return WorkbenchPageSpec(
        page_id=normalized_id,
        title=title,
        group=presentation.get("group") or "其他",
        description=presentation.get("description") or "",
        keywords=tuple(presentation.get("keywords") or (fallback_title or "",)),
        factory=factory,
        show_in_navigation=bool(presentation.get("show_in_navigation", True)),
    )
