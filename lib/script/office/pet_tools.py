"""办公模式的桌宠能力工具。

桌宠能力（音乐、雪豹、沙发、摩托、倒计时、音量、瞬移）都活在主进程里：ToolDispatcher
直接操作音响窗口、动画管理器和音乐服务。DSH 办公侧车是独立的 Node 进程，拿不到这些对象，
所以办公模式走「工具登记 + 命令回传」：

1. 侧车桥把陪伴模式的原生工具注册成同名工具，模型的工具调用因此带 taskId 回到主进程；
2. 本模块复用 chat/native_tools 的既有映射，把 (name, arguments) 翻译成桌宠指令；
3. 指令交给 ToolDispatcher 执行，结果（或失败原因）回传侧车：既还给模型，也写进
   任务详情页的「工具记录」。

未开放的桌宠工具：`recall_memory`、`inspect_screen` 都要往陪伴聊天流里注入
INPUT_CHAT（办公模式没有这条通道，窥屏还会把整屏内容送给办公模型）；`open_browser`
会在办公沙箱与审批边界之外打开系统浏览器，模型被文件内容诱导时风险不可控。
"""

from __future__ import annotations

from lib.core.logger import get_logger
from lib.script.chat.native_tools import get_native_tool_definitions, native_tool_to_dispatch

logger = get_logger(__name__)

# 开放给办公模式的桌宠工具，名称与陪伴模式原生工具完全一致，顺序也保持一致。
PET_TOOL_NAMES: tuple[str, ...] = (
    "play_music",
    "next_track",
    "toggle_play_pause",
    "spawn_snow_leopard",
    "spawn_sofa",
    "spawn_motorcycle",
    "start_timer",
    "set_volume",
    "change_volume",
    "teleport_pet",
)

# 刻意不开放的原生桌宠工具，留在这里是为了让「每个原生工具都被显式取舍过」可被测试断言。
EXCLUDED_PET_TOOL_NAMES: tuple[str, ...] = (
    "recall_memory",
    "inspect_screen",
    "open_browser",
)

_PET_TOOL_NAME_SET = frozenset(PET_TOOL_NAMES)

# 模型看到的结果文案；{detail} 替换成实际下发的桌宠指令参数。
_PET_TOOL_RESULT_TEXT = {
    "play_music": "已让音响搜索并播放：{detail}",
    "next_track": "已切到下一曲。",
    "toggle_play_pause": "已切换播放与暂停。",
    "spawn_snow_leopard": "已在桌面生成雪豹 {detail} 只。",
    "spawn_sofa": "已在桌宠身边生成沙发 {detail} 个。",
    "spawn_motorcycle": "已在桌宠身边生成摩托 {detail} 辆。",
    "start_timer": "已生成 {detail} 秒倒计时。",
    "set_volume": "音量已设为 {detail}%。",
    "change_volume": "音量已调整 {detail}%。",
    "teleport_pet": "桌宠已瞬移到 ({detail})。",
}

# 只有必填参数缺失才会走到这里；数量类参数在 native_tools 里有默认值。
_PET_TOOL_MISSING_ARGUMENT_TEXT = {
    "set_volume": "set_volume 缺少 percent 参数，未调音量。",
    "change_volume": "change_volume 缺少 delta_percent 参数，未调音量。",
    "teleport_pet": "teleport_pet 需要 x 与 y 参数，未瞬移。",
}


def pet_tool_definitions() -> list[dict]:
    """返回办公模式开放的桌宠工具定义（OpenAI 函数工具格式）。

    定义直接取自陪伴模式的原生工具表，侧车桥注册的 schema 必须与之一致：
    同一套工具名与参数名，桌面能力在两种模式下才是同一个契约。
    """
    return [
        definition
        for definition in get_native_tool_definitions()
        if definition["function"]["name"] in _PET_TOOL_NAME_SET
    ]


def build_pet_dispatch(
    name: object,
    arguments: object = None,
) -> tuple[str, str] | None:
    """把桌宠工具调用翻译成 ToolDispatcher 的 (指令, 参数)，未开放或参数不足返回 None。"""
    tool_name = str(name or "").strip()
    if tool_name not in _PET_TOOL_NAME_SET:
        return None
    payload = arguments if isinstance(arguments, dict) else {}
    return native_tool_to_dispatch({"name": tool_name, "arguments": payload})


def execute_pet_tool(
    name: object,
    arguments: object = None,
    *,
    dispatcher=None,
) -> dict:
    """执行一次桌宠工具调用，返回 {"ok": bool, "message": str}。

    结果文案直接给模型看，因此失败也要是可读的整句：工具清单之外的调用、参数不足、
    桌宠能力不可用或执行抛异常，都返回 ok=False 而不是让异常穿透事件处理。
    """
    tool_name = str(name or "").strip()
    if tool_name not in _PET_TOOL_NAME_SET:
        return {"ok": False, "message": f"未知的桌宠工具：{tool_name or '（空）'}"}
    dispatch = build_pet_dispatch(tool_name, arguments)
    if dispatch is None:
        return {
            "ok": False,
            "message": _PET_TOOL_MISSING_ARGUMENT_TEXT.get(
                tool_name,
                f"{tool_name} 的参数不完整，未执行。",
            ),
        }
    command, argument = dispatch
    try:
        runner = dispatcher if dispatcher is not None else _resolve_dispatcher()
        handled = bool(runner.execute_command(command, argument))
    except Exception as exc:  # 桌宠能力是可选依赖，坏了也不能拖垮办公任务
        logger.warning("[PetTools] 桌宠工具 %s 执行失败: %s", tool_name, exc)
        return {"ok": False, "message": f"桌宠能力执行失败：{exc}"}
    if not handled:
        return {"ok": False, "message": f"桌宠能力不可用：{command}"}
    return {"ok": True, "message": _pet_tool_result_text(tool_name, argument)}


def _pet_tool_result_text(tool_name: str, argument: str) -> str:
    template = _PET_TOOL_RESULT_TEXT.get(tool_name, "已执行桌宠指令：{detail}")
    detail = str(argument or "").strip() or "随机歌曲"
    return template.replace("{detail}", detail)


def _resolve_dispatcher():
    # 延迟导入：tool_dispatcher 在模块导入期会反向引用 office.mode，放在这里断环。
    from lib.script.tool_dispatcher import get_tool_dispatcher

    return get_tool_dispatcher()
