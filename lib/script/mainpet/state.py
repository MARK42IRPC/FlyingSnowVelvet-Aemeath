"""主宠状态机：只负责「状态迁移判定」与「迁移副作用」。

行为决策（漫游目标选择、保护半径、游戏区避让）在
`lib/script/mainpet/director.py`；动作清单与迁移规则的事实源在
`config/actions.py`。

对外接口：
- `request(action_id, by_event=...)`  请求切换（内部走 STATE_CHANGE_REQUEST 事件）
- `current()`                         当前动作 id
- `on_animation_complete()`           由 GIF 循环完成事件驱动
"""
from __future__ import annotations

from config.actions import (
    ACTION,
    BASE,
    INTERRUPT_ANY,
    INTERRUPT_EVENT,
    TRANSIENT,
    ActionSpec,
    action_spec,
    is_special_idle,
    pick_idle_action,
)
from lib.core.event.center import get_event_center, EventType, Event
from lib.core.logger import get_logger


_logger = get_logger(__name__)


def log(msg):
    """状态机正常运行日志默认静默，需要排查时可临时恢复。"""
    # _logger.debug("[StateMachine] %s", msg)


# ── 迁移矩阵 ──────────────────────────────────────────────────────────────
# 键是 (当前层, 目标层)，值是允许条件：
#   "always"              直接允许
#   "on_event"            只允许事件级打断（by_event=True）
#   "if_interruptible"    当前动作的 interrupt 设置决定
#   "never"               禁止
#
# 两条无条件放行规则（与旧实现的语义一致）：
#   1. 进入 base 层永远放行：动作播完、被拖拽、移动队列清空都要能回等待态。
#      移动运行时会用 `by_event=False` 请求 moving -> idle，若此处要求事件级，
#      状态机会停在 moving 而移动控制器已停止，表现为“原地播放移动动画”。
#   2. idle 池内的动作之间互相放行：idle 池是同一层级的待机选择，
#      动作播完后要能直接切到池内下一个，不能被上一个的 `interrupt=none` 卡住。
# 其余情况才按当前动作的 interrupt 判定。
TRANSITIONS: dict[tuple[str, str], str] = {
    (BASE, BASE): "always",
    (BASE, ACTION): "if_interruptible",
    (BASE, TRANSIENT): "if_interruptible",
    (ACTION, BASE): "always",
    (ACTION, ACTION): "if_idle_pool_peer",
    (ACTION, TRANSIENT): "if_interruptible",
    (TRANSIENT, BASE): "always",
    (TRANSIENT, ACTION): "never",
    (TRANSIENT, TRANSIENT): "never",
}


class StateMachine:
    """宠物状态机（事件驱动，动作表驱动）。"""

    def __init__(self, entity, timing_manager=None, *, defer=None):
        """
        初始化状态机。

        Args:
            entity: BaseEntity 实例（PetWindow）
            timing_manager: 计时器管理器
        """
        self._entity = entity
        self._current_state = "idle"
        self._timing_manager = timing_manager
        self._defer = defer
        self._event_center = get_event_center()

        # 当前动作已播放循环次数
        self._current_loop_count = 0

        # 后台音乐是否在播放；决定 idle 池是否包含唱歌动作
        self._music_playing = False
        # 上一次进入 idle 时抽到的是否为特殊待机（用于阻止连续特殊）
        self._last_idle_was_special = False

        # 事件订阅
        self._event_center.subscribe(EventType.STATE_CHANGE_REQUEST, self._on_state_change_request)
        self._event_center.subscribe(EventType.GIF_LOOP_COMPLETED, self._on_gif_loop_completed)
        self._event_center.subscribe(EventType.PET_MOVE_DONE, self._on_pet_move_done)
        self._event_center.subscribe(EventType.MUSIC_STATUS_CHANGE, self._on_music_status_change)

        # 行为导演：漫游、保护半径、游戏区避让都归它
        from lib.script.mainpet.director import BehaviorDirector

        self._director = BehaviorDirector(
            entity=entity,
            request_action=self._request_from_director,
            on_wander_pause=self._pause_wander,
            on_wander_resume=self._resume_wander,
            defer=defer,
        )

        log("StateMachine initialized")

        if self._timing_manager:
            self._director.start(self._timing_manager)

        # 动作表自检：有问题时打警告但不阻断启动
        problems = self._validation_problems()
        if problems:
            for problem in problems:
                _logger.warning("[StateMachine] 动作表问题: %s", problem)

    # ── 查询 ──────────────────────────────────────────────────────────

    def current(self) -> str:
        """当前动作 id。"""
        return self._current_state

    # 历史接口别名
    get_current_state = current

    def current_spec(self) -> ActionSpec | None:
        return action_spec(self._current_state)

    def _request_from_director(self, action_id: str) -> None:
        """导演请求——按「非事件打断」处理，保证动作完整播放。"""
        self.request(action_id, by_event=False)

    # ── 请求入口 ──────────────────────────────────────────────────────

    def request(self, action_id: str, *, by_event: bool = True) -> None:
        """发布状态切换请求事件（经事件中心回到 `_apply`）。"""
        self._event_center.publish(Event(EventType.STATE_CHANGE_REQUEST, {
            "new_state": action_id,
            "by_event": by_event,
        }))

    def on_animation_complete(self) -> None:
        """动画播完一轮。达到 loops 后由状态机自己请求回到 base。"""
        self._current_loop_count += 1
        spec = self.current_spec()
        if spec is None:
            return
        if spec.loops > 0 and self._current_loop_count >= spec.loops:
            self._return_to_base(by_event=False)

    # ── 事件处理 ──────────────────────────────────────────────────────

    def _on_state_change_request(self, event):
        new_state = event.data.get("new_state")
        by_event = event.data.get("by_event", True)
        if new_state:
            self._apply(str(new_state), by_event)

    def _on_gif_loop_completed(self, event):
        state = event.data.get("state")
        if state != self._current_state:
            return
        self.on_animation_complete()

    def _on_pet_move_done(self, event: Event) -> None:
        if event.data.get("source") != "mainpet_state":
            return
        result = str(event.data.get("result") or "").strip().lower()
        if result != "timeout":
            return
        log("Move timeout, forcing random action")
        self._director.force_random_action()

    def _on_music_status_change(self, event: Event) -> None:
        """跟踪后台音乐播放状态，供 idle 池决定是否纳入唱歌动作。"""
        self._music_playing = bool(event.data.get("playing", False))

    # ── 迁移核心 ──────────────────────────────────────────────────────

    def _apply(self, new_state: str, by_event: bool = True) -> None:
        """判定并执行一次状态迁移。

        请求 `idle` 时不是直接播 idle.gif，而是从 idle 池按权重抽一个动作。
        池内候选若不可切入（例如刚播完不可打断的动作），依次剔除后重抽。
        """
        from_idle_pool = new_state == "idle"
        if from_idle_pool:
            chosen = self._pick_idle_target(by_event)
            if chosen is None:
                log("No idle action available")
                return
            new_state = chosen

        new_spec = action_spec(new_state)
        if new_spec is None:
            log(f"Action not found: {new_state}")
            return

        current_spec = self.current_spec()

        if not self._can_transition(
            current_spec, new_spec, by_event, from_idle_pool=from_idle_pool
        ):
            log(f"Transition rejected: {self._current_state} -> {new_state} (by_event={by_event})")
            return

        previous = self._current_state
        self._current_state = new_state
        self._current_loop_count = 0

        self._on_enter(previous, new_state, new_spec)
        self._entity.change_state(new_state, new_spec.resource_id)
        log(f"State changed: {previous} -> {new_state}")

    def _pick_idle_target(self, by_event: bool) -> str | None:
        """从 idle 池抽一个当前可切入的动作。

        抽取完全由权重决定（允许抽到当前正在播的动作，基础 idle 可以连续循环）。
        抽到的候选若因迁移规则不可切入，才剔除它重抽；全部候选都不可切入时
        退回 `idle` 本身，保证状态机能落地。
        """
        current = self.current_spec()
        attempted: set[str] = set()
        for _ in range(8):
            candidate = pick_idle_action(
                music_playing=self._music_playing,
                last_was_special=self._last_idle_was_special,
            )
            if candidate is None or candidate in attempted:
                break
            attempted.add(candidate)
            spec = action_spec(candidate)
            if spec is not None and self._can_transition(
                current, spec, by_event, from_idle_pool=True
            ):
                self._last_idle_was_special = is_special_idle(candidate)
                return candidate
        # 兜底：回到纯 idle（idle 本身总是可以进入）
        self._last_idle_was_special = False
        return "idle"

    def _can_transition(
        self,
        current: ActionSpec | None,
        target: ActionSpec,
        by_event: bool,
        *,
        from_idle_pool: bool = False,
    ) -> bool:
        """按迁移矩阵判定是否允许这次切换。

        `from_idle_pool` 标记目标动作是由 idle 池抽出的：池内轮转是同级
        选择，不受上一个待机动作 `interrupt=none` 的限制。
        """
        if current is None:
            return True

        # enter_from 约束（如 moving 只能从 idle 进入）。
        # 这里比较的是「是否处于等待态」而不是字面动作 id：idle 池会抽出
        # boring / tape 等动作，它们的层同样是 base 语义上的等待态，
        # 若按字面 id 比较，抽中待机变体后 move 就永远进不来了。
        if target.enter_from and not self._in_waiting_state(target.enter_from):
            return False

        # idle 池轮转：池内动作互为同级选择，可以从任何当前动作切过去，
        # 不受上一个动作 `interrupt=none` 的限制。互动动作播完回池正是走这条。
        if from_idle_pool and current.layer == ACTION:
            return True

        # 离开「可自由切出」的常驻动作不受其 interrupt 限制：
        # idle 是等待态，移动/自动行为都必须能把它切走，否则 moving 永远进不来。
        if self._leaves_freely(current):
            return True

        # 目标就是当前动作：允许重播（重置帧），但同样受打断规则约束
        if target is current:
            return self._interrupt_allows(current, by_event)

        rule = TRANSITIONS.get((current.layer, target.layer), "never")
        if rule == "never":
            return False
        if rule == "always":
            return True
        if rule == "on_event":
            return by_event
        if rule == "if_idle_pool_peer" and from_idle_pool:
            # idle 池内的动作互为同级候选：池内轮转不受上一个动作的
            # `interrupt=none` 限制，否则动作播完就抽不到下一个。
            return True
        return self._interrupt_allows(current, by_event)

    def _in_waiting_state(self, allowed: tuple[str, ...]) -> bool:
        """当前状态是否属于 `allowed` 描述的等待态。

        `enter_from=("idle",)` 的语义是「处于待机状态时」，而待机状态在
        idle 池机制下可能表现为 boring / tape / spinning 等动作 id。
        因此只要当前动作的层是 base（常驻层），且它属于 idle 池或就是 idle，
        就视为满足。
        """
        if self._current_state in allowed:
            return True
        current = self.current_spec()
        if current is None:
            return False
        if "idle" not in allowed:
            return False
        # 常驻层 = 等待态；idle 池动作播完也会回到等待态
        return current.layer == BASE or "idle" in current.groups

    def _leaves_freely(self, spec: ActionSpec) -> bool:
        """当前动作是否允许被任意请求切走（不要求 by_event）。

        `idle` 是等待态而不是「正在播放的动作」，任何请求都应能离开它；
        这与旧实现里 `if self._current_state != 'idle' and ...` 的语义一致。
        """
        return spec.layer == BASE and self._current_state == "idle"

    @staticmethod
    def _interrupt_allows(spec: ActionSpec, by_event: bool) -> bool:
        if spec.interrupt == INTERRUPT_ANY:
            return True
        if spec.interrupt == INTERRUPT_EVENT:
            return by_event
        return False

    def _on_enter(self, previous: str, new_state: str, spec: ActionSpec) -> None:
        """迁移副作用：漫游计时器、动作事件、粒子。"""
        # 漫游计时器：只有 base 层恢复，离开 base 层一律暂停
        if spec.layer == BASE:
            self._director.on_enter_base(new_state)
        else:
            self._director.on_leave_base()

        # 动作开始事件与粒子（仅非 base 层）
        if spec.layer != BASE:
            self._publish_action_start(new_state)
            if spec.has_particle_effect() and (spec.particle or {}).get("trigger") == "start":
                self._spawn_particle_effect(spec.particle or {})

    def _return_to_base(self, by_event: bool = False) -> None:
        """动作播完，回到 base 层（优先 idle）。"""
        self.request("idle", by_event=by_event)

    # ── 导演回调 ──────────────────────────────────────────────────────

    def _request_from_director(self, action_id: str) -> None:
        self.request(action_id, by_event=False)

    def _pause_wander(self) -> None:
        if self._timing_manager:
            self._timing_manager.pause_task(self._director.wander_task_id)

    def _resume_wander(self) -> None:
        if self._timing_manager:
            self._timing_manager.resume_task(self._director.wander_task_id)

    # ── 事件发布 ──────────────────────────────────────────────────────

    def _publish_action_start(self, action_name: str) -> None:
        self._event_center.publish(Event(EventType.ACTION_START, {
            "action_name": action_name,
            "loop_count": self._current_loop_count,
        }))

    def _spawn_particle_effect(self, particle_config: dict) -> None:
        """按配置在主宠位置触发粒子效果。"""
        particle_id = particle_config.get("particle_id", "scatter_fall")
        area_type = particle_config.get("area_type", "point")

        pos = self._entity.get_core_position()
        rect = self._entity.get_core_geometry()

        if area_type == "point":
            area_data = (pos.x + rect.width // 2, pos.y + rect.height // 2)
        elif area_type == "rect":
            area_data = (pos.x, pos.y, pos.x + rect.width, pos.y + rect.height)
        elif area_type == "circle":
            area_data = (
                pos.x + rect.width // 2,
                pos.y + rect.height // 2,
                min(rect.width, rect.height) // 2,
            )
        else:
            return

        self._event_center.publish(Event(EventType.PARTICLE_REQUEST, {
            "particle_id": particle_id,
            "area_type": area_type,
            "area_data": area_data,
        }))

    # ── 自检 ──────────────────────────────────────────────────────────

    @staticmethod
    def _validation_problems() -> list[str]:
        from config.actions import validate

        return validate()
