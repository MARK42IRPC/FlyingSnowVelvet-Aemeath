"""PetWindow UI 组件工厂。"""

from lib.script.ui.bubble import Bubble
from lib.script.ui.chat_mode_button import ChatModeButton
from lib.script.ui.clickthrough_button import ClickThroughButton
from lib.script.ui.close_button import CloseButton
from lib.script.ui.command_dialog import CommandDialog
from lib.script.ui.command_hint_box import CommandHintBox
from lib.script.ui.launch_wuwa_button import LaunchWutheringWavesButton
from lib.script.ui.mic_stt_indicator import MicSttIndicator
from lib.script.ui.more_functions_button import MoreFunctionsButton
from lib.script.ui.scale_button import ScaleUpButton, ScaleDownButton


_UI_ATTRS = (
    '_close_btn',
    '_clickthrough_btn',
    '_scale_up_btn',
    '_scale_down_btn',
    '_launch_wuwa_btn',
    '_chat_mode_btn',
    '_more_functions_btn',
    '_bubble',
    '_hint_box',
    '_cmd',
    '_mic_stt_indicator',
)


def create_pet_window_ui(owner, on_close):
    """创建 `PetWindow` 使用的主 UI 组件集合。"""
    close_btn = CloseButton(on_close=on_close)
    clickthrough_btn = ClickThroughButton()
    scale_up_btn = ScaleUpButton(clickthrough_button=clickthrough_btn)
    scale_down_btn = ScaleDownButton(scale_up_button=scale_up_btn)
    launch_wuwa_btn = LaunchWutheringWavesButton(clickthrough_button=clickthrough_btn)
    chat_mode_btn = ChatModeButton(launch_wuwa_button=launch_wuwa_btn)
    more_functions_btn = MoreFunctionsButton(chat_mode_button=chat_mode_btn)
    bubble = Bubble()
    hint_box = CommandHintBox()
    cmd = CommandDialog(
        on_command=lambda text: None,
        bubble=None,
        close_button=close_btn,
        clickthrough_button=clickthrough_btn,
        hint_box=hint_box,
        scale_up_button=scale_up_btn,
        scale_down_button=scale_down_btn,
        launch_wuwa_button=launch_wuwa_btn,
        chat_mode_button=chat_mode_btn,
        more_functions_button=more_functions_btn,
    )
    mic_stt_indicator = MicSttIndicator(owner)

    return {
        '_close_btn': close_btn,
        '_clickthrough_btn': clickthrough_btn,
        '_scale_up_btn': scale_up_btn,
        '_scale_down_btn': scale_down_btn,
        '_launch_wuwa_btn': launch_wuwa_btn,
        '_chat_mode_btn': chat_mode_btn,
        '_more_functions_btn': more_functions_btn,
        '_bubble': bubble,
        '_hint_box': hint_box,
        '_cmd': cmd,
        '_mic_stt_indicator': mic_stt_indicator,
    }


def attach_pet_window_ui(owner, on_close) -> None:
    """将主 UI 组件挂载到 `PetWindow` 实例。"""
    for attr_name, widget in create_pet_window_ui(owner, on_close).items():
        setattr(owner, attr_name, widget)


def iter_pet_window_ui(owner):
    """遍历当前已挂载的 `PetWindow` 主 UI 组件。"""
    for attr_name in _UI_ATTRS:
        yield attr_name, getattr(owner, attr_name, None)
