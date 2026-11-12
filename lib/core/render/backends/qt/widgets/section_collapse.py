"""设置页卡片的滑入 / 滑出（档位 D 宿主）。

设置页里大量「开关一改，某张卡片跟着出现或消失」的联动：回复模式切换 AI 页的四张
模式卡片、语音包不可用时收起整段语音设置、`LIVE_STREAM.enabled` 控住「弹幕点歌」、
`GIFT_ALERT.enabled` 控住「礼物提醒」、办公页「独立 api」控住那一整块。

这些联动此前都是裸 `setVisible()`：卡片会在一帧内凭空出现或消失，下方卡片瞬间跳位。
本模块把它们统一成一次短动画：

- **高度**：对一层裁切容器的 `maximumHeight` 做动画，卡片因此是「被推出来 / 收回去」，
  而不是整块闪烁；
- **位移**：裁切容器内部保持卡片的自然高度，所以动画过程中卡片内容是整体滑动并被裁掉，
  不会被压扁——直接对卡片本身做 `maximumHeight` 动画会把内部的输入框挤成 1px 高；
- **透明度**：裁切容器自己挂一个短暂的透明度效果做淡入淡出。容器里只有卡片，没有文本
  光标或弹层，不会触碰工作台切页刻意回避的 `graphicsEffect` 合成问题。

时序取自后端中立的 `lib/core/render/visuals/motion.py`。动画对象挂在调用方传入的宿主
（通常是页面）上，卡片被销毁时动画跟着停，避免在已释放对象上写属性。
"""

from __future__ import annotations

from PyQt5.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PyQt5.QtWidgets import QGraphicsOpacityEffect, QVBoxLayout, QWidget

from lib.core.render.visuals import motion

#: 卡片联动动画的状态键，挂在裁切容器与宿主上。
_STATE_KEY = "_section_collapse_state"


class _SectionClipState:
    """一张卡片参与滑入/滑出所需的全部状态。"""

    __slots__ = ("outer", "inner", "inner_height", "height_anim", "fade_anim")

    def __init__(self, outer: QWidget, inner: QWidget, inner_height: int) -> None:
        self.outer = outer
        self.inner = inner
        self.inner_height = inner_height
        self.height_anim = None
        self.fade_anim = None


def _wrap_section(widget: QWidget) -> _SectionClipState:
    """把卡片包进裁切容器；已经包过就复用。

    容器的**位置继承自卡片原来在父布局里的位置**：拿掉卡片后把容器放回同一个位置，
    所以包裹本身不会让卡片跳到页面末尾。容器内部布局用 `SetNoConstraint`，卡片对齐
    `AlignTop` 且保持自然高度——这样容器变矮时卡片是被裁切，而不是被挤压。
    """
    existing = getattr(widget, _STATE_KEY, None)
    if isinstance(existing, _SectionClipState):
        return existing

    parent = widget.parentWidget()
    # 位置必须在 reparent **之前**取：`addWidget()` 到容器内部后，父布局里那一项已经
    # 失效，`indexOf()` 会返回 -1，容器就再也进不了布局（整张卡片消失）。
    slot = _capture_slot(widget, parent)
    outer = QWidget(parent)
    outer.setObjectName("SettingsSectionClip")
    outer.setMinimumHeight(0)
    outer_layout = QVBoxLayout(outer)
    outer_layout.setContentsMargins(0, 0, 0, 0)
    outer_layout.setSpacing(0)
    outer_layout.setSizeConstraint(QVBoxLayout.SetNoConstraint)
    # 卡片必须**不**随容器收缩，否则内部控件会被挤扁。
    widget.setMinimumHeight(0)
    widget.setMaximumHeight(16777215)
    outer_layout.addWidget(widget, 0, Qt.AlignTop)

    effect = QGraphicsOpacityEffect(outer)
    effect.setOpacity(1.0)
    outer.setGraphicsEffect(effect)

    state = _SectionClipState(outer, widget, 0)
    setattr(outer, _STATE_KEY, state)
    #: 调用方（设置页）按原卡片对象找回来，因此两边各存一份。
    setattr(widget, _STATE_KEY, state)
    setattr(outer, "_section_effect", effect)
    _apply_slot(widget, outer, slot)
    return state


#: 布局槽位：`("index", i)` 表示普通布局的第 i 项，`("form", row, role)` 表示表单行。
_Slot = tuple


def _capture_slot(widget: QWidget, parent: QWidget | None) -> _Slot | None:
    """在 reparent 之前记录卡片在父布局里的位置。"""
    from PyQt5.QtWidgets import QFormLayout

    if parent is None:
        return None
    layout = parent.layout()
    if layout is None:
        return None
    if isinstance(layout, QFormLayout):
        row, role = layout.getWidgetPosition(widget)
        return ("form", row, role) if row >= 0 else None
    index = layout.indexOf(widget)
    return ("index", index) if index >= 0 else None


def _apply_slot(widget: QWidget, outer: QWidget, slot: _Slot | None) -> None:
    """把容器放回卡片原来的位置。"""
    from PyQt5.QtWidgets import QFormLayout

    if slot is None:
        return
    kind = slot[0]
    if kind == "form":
        layout = outer.parentWidget().layout() if outer.parentWidget() else None
        if isinstance(layout, QFormLayout):
            layout.setWidget(slot[1], slot[2], outer)
        return
    layout = outer.parentWidget().layout() if outer.parentWidget() else None
    if layout is None:
        return
    if isinstance(layout, QFormLayout):
        # 卡片原先不在表单里，但父布局换成了表单：退回追加。
        layout.addRow(outer)
        return
    if layout.indexOf(widget) >= 0:
        layout.removeWidget(widget)
    layout.insertWidget(max(0, min(slot[1], layout.count())), outer)


def _measure(state: _SectionClipState) -> int:
    """卡片自身的自然高度；量不到时退回容器当前高度。"""
    hint = state.inner.sizeHint().height()
    if hint <= 0:
        hint = state.inner.minimumSizeHint().height()
    return hint if hint > 0 else max(1, state.outer.height())


def _should_animate(widget: QWidget, host: QWidget | None, animate: bool) -> bool:
    """是否真的需要播动画。

    宿主还没显示时（页面构造阶段、离屏构建、工作台尚未打开）Qt 不会推进动画，
    此时「先播动画再收尾」等于把卡片永久留在中间态。这类情况下直接落到终态，
    与侧边栏抽屉在未显示窗口里 `animate=False` 的处理一致。

    `host` 只用于判断宿主是否可见，可能是一个只提供少量方法的产品对象（测试替身）；
    拿不到 Qt 可见性时按「不播动画」处理——那样仍会走到终态，行为可预期。
    """
    if not animate:
        return False
    if motion.section_reveal_ms() <= 0:
        return False
    target = host if host is not None else None
    if target is None:
        target = widget.parentWidget()
    is_visible = getattr(target, "isVisible", None)
    if not callable(is_visible):
        return False
    try:
        return bool(is_visible())
    except (RuntimeError, AttributeError):
        return False


def _is_qt_widget(widget) -> bool:
    """是不是真正的 QWidget。

    调用点有时传入只提供 `setVisible` 的替身（单元测试），对它们做包裹、测尺寸、
    挂动画都无从谈起；这类对象直接走原来的 `setVisible()` 语义。
    """
    return isinstance(widget, QWidget)


def _start(animation, owner: QWidget) -> None:
    """启动一条动画，并把它绑到宿主的销毁上。

    宿主（通常是设置页）先于动画被销毁时，动画仍会继续在已释放的容器/效果上写属性，
    实测会直接把进程带崩（无 traceback）。这里让宿主销毁时停掉动画，
    与侧边栏抽屉的 `destroyed.connect(anim.stop)` 同一处理。
    """
    try:
        owner.destroyed.connect(animation.stop)
    except (RuntimeError, AttributeError, TypeError):
        pass
    animation.start()


def show_section(widget: QWidget, *, host: QWidget | None = None, animate: bool = True) -> None:
    """把卡片滑入（已经展开时是空操作）。"""
    if widget is None:
        return
    if not _is_qt_widget(widget) or not _should_animate(widget, host, animate):
        state = getattr(widget, _STATE_KEY, None)
        if isinstance(state, _SectionClipState):
            _stop(state)
            state.outer.setMaximumHeight(16777215)
            state.outer.setVisible(True)
            state.inner.setMinimumHeight(0)
            state.inner.setMaximumHeight(16777215)
            _effect(state).setOpacity(1.0)
        widget.setVisible(True)
        return
    if is_section_visible(widget):
        return

    duration = motion.section_reveal_ms()
    state = _wrap_section(widget)
    owner = host or widget.parentWidget() or state.outer
    _stop(state)
    # 收起到位后卡片与容器都是隐藏的，展开前要一起放出来。
    state.outer.setVisible(True)
    widget.setVisible(True)
    state.outer.setMaximumHeight(0)
    # 先把高度定下来再量：容器此刻是 0 高，`sizeHint()` 才是卡片的真实高度。
    height = _measure(state)
    state.inner_height = height
    # 内层固定成自然高度，外层从 0 长到它——卡片是被「推出来」的。
    state.inner.setMinimumHeight(height)
    state.inner.setMaximumHeight(height)
    state.outer.layout().activate()

    height_anim = QPropertyAnimation(state.outer, b"maximumHeight", owner)
    height_anim.setDuration(duration)
    height_anim.setEasingCurve(QEasingCurve.OutCubic)
    height_anim.setStartValue(0)
    height_anim.setEndValue(height)
    height_anim.finished.connect(lambda s=state: _settle_open(s))

    effect = _effect(state)
    effect.setOpacity(motion.SECTION_CONTENT_MIN_OPACITY)
    fade_anim = QPropertyAnimation(effect, b"opacity", owner)
    fade_anim.setDuration(duration)
    fade_anim.setEasingCurve(QEasingCurve.OutCubic)
    fade_anim.setStartValue(motion.SECTION_CONTENT_MIN_OPACITY)
    fade_anim.setEndValue(1.0)

    state.height_anim = height_anim
    state.fade_anim = fade_anim
    _start(height_anim, owner)
    _start(fade_anim, owner)


def hide_section(widget: QWidget, *, host: QWidget | None = None, animate: bool = True) -> None:
    """把卡片滑出（已隐藏时是空操作）。"""
    if widget is None:
        return
    duration = motion.section_reveal_ms()
    state = getattr(widget, _STATE_KEY, None)
    if not _is_qt_widget(widget) or not _should_animate(widget, host, animate) or not widget.isVisible():
        if isinstance(state, _SectionClipState):
            _stop(state)
            _effect(state).setOpacity(1.0)
            state.outer.setMaximumHeight(0)
            state.outer.setVisible(False)
        widget.setVisible(False)
        return

    state = _wrap_section(widget)
    owner = host or widget.parentWidget() or state.outer
    _stop(state)
    height = max(1, state.outer.height() or _measure(state))
    state.inner_height = height
    state.inner.setMinimumHeight(height)
    state.inner.setMaximumHeight(height)

    height_anim = QPropertyAnimation(state.outer, b"maximumHeight", owner)
    height_anim.setDuration(duration)
    height_anim.setEasingCurve(QEasingCurve.InCubic)
    height_anim.setStartValue(height)
    height_anim.setEndValue(0)
    height_anim.finished.connect(lambda s=state: _settle_closed(s))

    effect = _effect(state)
    fade_anim = QPropertyAnimation(effect, b"opacity", owner)
    fade_anim.setDuration(max(1, int(duration * 0.7)))
    fade_anim.setEasingCurve(QEasingCurve.InCubic)
    fade_anim.setStartValue(1.0)
    fade_anim.setEndValue(motion.SECTION_CONTENT_MIN_OPACITY)

    state.height_anim = height_anim
    state.fade_anim = fade_anim
    _start(height_anim, owner)
    _start(fade_anim, owner)


def set_section_visible(
    widget: QWidget,
    visible: bool,
    *,
    host: QWidget | None = None,
    animate: bool = True,
) -> None:
    """按布尔值滑入或滑出，供「开关联动」这类一行改完的调用点使用。"""
    if visible:
        show_section(widget, host=host, animate=animate)
    else:
        hide_section(widget, host=host, animate=animate)


def is_section_visible(widget: QWidget) -> bool:
    """卡片当前是否处于展开（或正在展开）状态。

    动画收尾前裁切容器仍然可见但高度趋近 0，`isVisible()` 会短暂为 True；判定联动
    目标时应当看卡片自己的期望状态，避免重复触发动画。
    """
    if widget is None:
        return False
    state = getattr(widget, _STATE_KEY, None)
    if isinstance(state, _SectionClipState):
        animation = state.height_anim
        if animation is not None:
            end = animation.endValue()
            if end is not None:
                return int(end) > 0
        return state.outer.maximumHeight() > 0 and not widget.isHidden()
    return not widget.isHidden()


def _effect(state: _SectionClipState) -> QGraphicsOpacityEffect:
    effect = getattr(state.outer, "_section_effect", None)
    if isinstance(effect, QGraphicsOpacityEffect):
        return effect
    effect = QGraphicsOpacityEffect(state.outer)
    state.outer.setGraphicsEffect(effect)
    setattr(state.outer, "_section_effect", effect)
    return effect


def _stop(state: _SectionClipState) -> None:
    for attribute in ("height_anim", "fade_anim"):
        animation = getattr(state, attribute, None)
        if animation is None:
            continue
        try:
            animation.stop()
        except RuntimeError:
            pass


def _alive(state: _SectionClipState) -> bool:
    """容器与卡片是否都还活着。

    动画 `finished` 可能在宿主/容器已被 `deleteLater` 处理之后才排到，此时再访问
    已释放的 Qt 对象会直接结束进程（离屏环境下实测无 traceback）。
    """
    for attribute in ("outer", "inner"):
        target = getattr(state, attribute, None)
        if target is None:
            return False
        try:
            target.objectName()
        except RuntimeError:
            return False
    return True


def _settle_open(state: _SectionClipState) -> None:
    """展开落位：解除高度限制，让卡片重新跟随内容尺寸。"""
    if not _alive(state):
        return
    state.inner.setMinimumHeight(0)
    state.inner.setMaximumHeight(16777215)
    state.outer.setMaximumHeight(16777215)
    _effect(state).setOpacity(1.0)


def _settle_closed(state: _SectionClipState) -> None:
    """收起落位：容器归零并隐藏卡片本身。

    卡片保持自己的自然高度，下次展开不必重新测量；裁切容器一并隐藏，避免留下一个
    高度为 0 却仍可见的空壳（`isVisible()` 会因此说谎）。
    """
    if not _alive(state):
        return
    state.outer.setMaximumHeight(0)
    state.inner.setVisible(False)
    state.outer.setVisible(False)
    _effect(state).setOpacity(1.0)


__all__ = [
    "hide_section",
    "is_section_visible",
    "set_section_visible",
    "show_section",
]
