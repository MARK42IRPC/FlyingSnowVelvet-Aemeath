"""主宠动作声明表（纯数据，无 Qt / 无业务逻辑）。

动作按「层」组织，层决定它在状态机里的生命周期：

- ``base``      常驻层。只有 ``idle`` 与 ``moving``，播完不回退。
- ``action``    动作层。播放固定次数后自动回到 base；可被事件打断。
- ``transient`` 瞬态层。一次性反馈（受伤、被拖拽等），播完必回 base。

``groups`` 是动作的语义标签，供各触发源按用途取随机动作，
取代过去同时维护的 ``BEHAVIOR['random_states']`` 与 ``ACTION_GROUPS`` 两份名单：

- ``click``  左键点击 / 空格键触发的互动动作
- ``idle``   自动行为定时器挑选的待机小动作
- ``wake``   启动时播放的欢迎动作

新增动作只需在 ``ACTIONS`` 里加一条并把 GIF 放进 ``ANIMATION['gif_dir']``，
不需要改状态机代码。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from config.config_animation import ANIMATION


BASE = "base"
ACTION = "action"
TRANSIENT = "transient"

INTERRUPT_NONE = "none"     # 播完前不可打断
INTERRUPT_EVENT = "event"   # 只允许事件级（非播放完成）打断
INTERRUPT_ANY = "any"       # 任意请求可打断


@dataclass(frozen=True)
class ActionSpec:
    """单个动作的不可变声明。"""

    gif: str                              # GIF 文件名，相对 ANIMATION['gif_dir']
    layer: str = ACTION
    loops: int = 1                        # 播放次数，-1 表示无限循环
    interrupt: str = INTERRUPT_NONE
    groups: tuple[str, ...] = field(default_factory=tuple)
    enter_from: tuple[str, ...] | None = None   # 仅这些动作之后可进入；None 表示不限
    particle: dict | None = None          # 粒子效果配置
    weight: int = 10                      # 待机池抽取权重（组内相对值）

    @property
    def gif_path(self) -> str:
        """GIF 的相对路径（相对工作目录）。"""
        return f"{ANIMATION['gif_dir'].rstrip('/')}/{self.gif}"

    @property
    def resource_id(self) -> str:
        """DrawCore 资源 id，取 GIF 文件名去扩展名。"""
        return self.gif.rsplit(".", 1)[0]

    def has_particle_effect(self) -> bool:
        return self.particle is not None


# ── 动作表 ────────────────────────────────────────────────────────────────
# 键是动作 id（英文、稳定，会进入日志与事件 payload），值是动作声明。

ACTIONS: dict[str, ActionSpec] = {
    # 常驻层
    # idle 既是等待态也是 idle 池的一员：`loops` 给一个有限轮数，
    # 播完会重新抽池，避免抽中后永久停留导致其他待机动画再也不出现。
    "idle": ActionSpec(
        gif="idle.gif",
        layer=BASE,
        loops=4,
        interrupt=INTERRUPT_EVENT,
        groups=("idle", "ordinary_idle"),
        weight=60,          # 基础待机是 idle 池的主体
    ),
    "moving": ActionSpec(
        gif="moving.gif",
        layer=BASE,
        loops=-1,
        interrupt=INTERRUPT_EVENT,
        enter_from=("idle",),
    ),

    # 动作层 —— 互动
    "wave": ActionSpec(
        gif="wave.gif",
        layer=ACTION,
        loops=2,
        groups=("click", "idle", "special_idle"),
        weight=5,
    ),
    "happy": ActionSpec(
        gif="happy.gif",
        layer=ACTION,
        loops=2,
        groups=("click", "wake", "idle", "special_idle"),
        weight=5,
        particle={"particle_id": "pink_scatter_fall", "area_type": "point", "trigger": "start"},
    ),
    "jumping": ActionSpec(
        gif="jumping.gif",
        layer=ACTION,
        loops=2,
        groups=("click", "idle", "special_idle"),
        weight=5,
        particle={"particle_id": "scatter_fall", "area_type": "point", "trigger": "start"},
    ),

    # 动作层 —— 待机池
    # 进入 idle 时从 `idle` 组按权重抽取，不再固定播某一个。
    "boring": ActionSpec(
        gif="boring.gif",
        layer=ACTION,
        loops=2,
        groups=("idle", "ordinary_idle"),
        weight=5,           # 与特殊待机同权重
    ),
    "play": ActionSpec(
        gif="play.gif",
        layer=ACTION,
        loops=1,
        interrupt=INTERRUPT_ANY,   # 原实现属于 action1 组且默认可打断
        groups=("idle", "click", "ordinary_idle"),
        weight=5,           # 与特殊待机同权重
    ),
    "tape": ActionSpec(
        gif="tape.gif",
        layer=ACTION,
        loops=1,
        groups=("idle", "special_idle"),
        weight=5,
    ),
    "spinning": ActionSpec(
        gif="spinning.gif",
        layer=ACTION,
        loops=1,
        groups=("idle", "special_idle"),
        weight=5,
    ),
    # 唱歌动作只在后台音乐正在播放时进入待机池（见 pick_idle_action）。
    "singing1": ActionSpec(
        gif="singing1.gif",
        layer=ACTION,
        loops=1,
        groups=("idle", "singing"),
        weight=5,
    ),
    "singing2": ActionSpec(
        gif="singing2.gif",
        layer=ACTION,
        loops=1,
        groups=("idle", "singing"),
        weight=5,
    ),
}

# 动作 id 的前置校验：常驻层动作必须存在
BASE_ACTION_IDS = tuple(aid for aid, spec in ACTIONS.items() if spec.layer == BASE)


def action_spec(action_id: str) -> ActionSpec | None:
    """按 id 取动作声明；不存在时返回 None。"""
    return ACTIONS.get(str(action_id or "").strip())


def actions_in_group(group: str) -> list[str]:
    """取某语义分组下的全部动作 id（保持声明顺序）。"""
    return [aid for aid, spec in ACTIONS.items() if group in spec.groups]


def pick_idle_action(
    *,
    music_playing: bool = False,
    last_was_special: bool = False,
    rng=None,
) -> str | None:
    """按权重抽取本次待机要播的动作。

    池子分两类（都带 `weight`，组内相对值）：
    - `ordinary_idle`：普通待机（idle / boring / play），构成绝大多数播放；
    - `special_idle` ：特殊待机（wave / happy / jumping / tape / spinning），小概率穿插。
    `singing` 组的唱歌动作只在 `music_playing=True` 时参与抽取。

    抽取完全由权重决定：允许连续抽到同一个动作（基础 idle 可以一直循环），
    只有「上一次是特殊待机」时强制本次抽普通待机，避免特殊动画连着出现。
    """
    import random as _random

    chooser = rng or _random
    pool = [
        aid for aid in actions_in_group("idle")
        if music_playing or "singing" not in ACTIONS[aid].groups
    ]
    if last_was_special:
        ordinary = [aid for aid in pool if "ordinary_idle" in ACTIONS[aid].groups]
        if ordinary:
            pool = ordinary

    if not pool:
        return None
    weights = [max(0, ACTIONS[aid].weight) for aid in pool]
    if sum(weights) <= 0:
        return chooser.choice(pool)
    return chooser.choices(pool, weights=weights, k=1)[0]


def is_special_idle(action_id: str) -> bool:
    """该动作是否属于特殊待机（用于阻止连续触发）。"""
    spec = ACTIONS.get(str(action_id or "").strip())
    return spec is not None and "special_idle" in spec.groups


def gif_files() -> list[str]:
    """全部动作的 GIF 相对路径，供 GifLoader 预加载。"""
    return [spec.gif_path for spec in ACTIONS.values()]


def validate(check_files: bool = True) -> list[str]:
    """返回动作表的问题清单；空列表表示通过。"""
    problems: list[str] = []
    seen_resources: dict[str, str] = {}
    for action_id, spec in ACTIONS.items():
        if spec.layer not in (BASE, ACTION, TRANSIENT):
            problems.append(f"{action_id}: 未知 layer {spec.layer!r}")
        if spec.interrupt not in (INTERRUPT_NONE, INTERRUPT_EVENT, INTERRUPT_ANY):
            problems.append(f"{action_id}: 未知 interrupt {spec.interrupt!r}")
        # base 层中只有 idle 允许有限轮数（它是 idle 池的一员，播完要重抽）；
        # moving 是持续播放的移动动画，必须无限循环。
        if spec.layer == BASE and spec.loops != -1 and action_id != "idle":
            problems.append(f"{action_id}: 常驻层动作必须 loops=-1")
        if spec.layer != BASE and spec.loops == 0:
            problems.append(f"{action_id}: loops 不能为 0")
        if spec.layer == BASE and spec.groups and action_id != "idle":
            # idle 是常驻层里唯一可进待机池的动作（它是等待态本身）；
            # moving 是正在播放的移动动画，不属于待机池。
            problems.append(f"{action_id}: 只有 idle 可以加入语义分组")

        other = seen_resources.get(spec.resource_id)
        if other is not None:
            problems.append(f"{action_id}: GIF {spec.gif!r} 与 {other} 重复")
        seen_resources[spec.resource_id] = action_id

        if spec.enter_from:
            unknown = [src for src in spec.enter_from if src not in ACTIONS]
            if unknown:
                problems.append(f"{action_id}: enter_from 引用未知动作 {unknown}")

        if check_files and not os.path.isfile(spec.gif_path):
            problems.append(f"{action_id}: GIF 文件不存在 {spec.gif_path}")

    if "idle" not in ACTIONS or ACTIONS["idle"].layer != BASE:
        problems.append("缺少 layer=base 的 idle 动作")
    return problems
