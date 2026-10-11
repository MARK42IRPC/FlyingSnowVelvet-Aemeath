"""Push-to-talk 热键文本的解析（后端中立纯逻辑）。

原先与监听器一起长在 `lib/script/microphone_stt/push_to_talk.py`。AI 设置面板的按键字段校验
（`lib/core/ai_settings/validation.py`）只需要「把 `Ctrl+Shift+V` 解析成按键 + 修饰键」，不需要
事件订阅与 STT 会话，因此这段纯逻辑下沉到 core：`lib/core` 不得反向 import `lib.script`。

监听器仍从 `push_to_talk` 再导出 `HotkeyBinding` / `parse_hotkey_binding`，既有导入面不变。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from lib.core.input.types import Key, KeyModifier


_MODIFIER_MASK = KeyModifier.SHIFT | KeyModifier.CONTROL | KeyModifier.ALT | KeyModifier.META
_MODIFIER_TOKENS: dict[str, KeyModifier] = {
    "CTRL": KeyModifier.CONTROL,
    "CONTROL": KeyModifier.CONTROL,
    "SHIFT": KeyModifier.SHIFT,
    "ALT": KeyModifier.ALT,
    "OPTION": KeyModifier.ALT,
    "WIN": KeyModifier.META,
    "CMD": KeyModifier.META,
    "META": KeyModifier.META,
    "SUPER": KeyModifier.META,
}
_SPECIAL_KEYS: dict[str, Key] = {
    "SPACE": Key.SPACE,
    "TAB": Key.TAB,
    "ENTER": Key.RETURN,
    "RETURN": Key.RETURN,
    "ESC": Key.ESCAPE,
    "ESCAPE": Key.ESCAPE,
    "BACKSPACE": Key.BACKSPACE,
    "DELETE": Key.DELETE,
    "INS": Key.INSERT,
    "INSERT": Key.INSERT,
    "HOME": Key.HOME,
    "END": Key.END,
    "PGUP": Key.PAGE_UP,
    "PAGEUP": Key.PAGE_UP,
    "PGDN": Key.PAGE_DOWN,
    "PAGEDOWN": Key.PAGE_DOWN,
    "UP": Key.UP,
    "DOWN": Key.DOWN,
    "LEFT": Key.LEFT,
    "RIGHT": Key.RIGHT,
}


@dataclass(frozen=True)
class HotkeyBinding:
    """Parsed push-to-talk hotkey information."""

    key: Key
    modifiers: KeyModifier
    display: str

    def matches(self, key_code: Key | int, modifiers) -> bool:
        if key_code != self.key:
            return False
        return _normalize_modifiers(modifiers) == self.modifiers


def _normalize_modifiers(modifiers) -> KeyModifier:
    try:
        value = int(modifiers)
    except Exception:
        value = 0
    return KeyModifier(value & int(_MODIFIER_MASK))


def _resolve_letter_or_digit(token: str) -> Optional[Key]:
    if len(token) != 1:
        return None
    if "A" <= token <= "Z":
        return Key[token]
    if "0" <= token <= "9":
        return Key[f"DIGIT_{token}"]
    return None


def _resolve_function_key(token: str) -> Optional[Key]:
    if not token.startswith("F"):
        return None
    suffix = token[1:]
    if not suffix.isdigit():
        return None
    index = int(suffix)
    if not 1 <= index <= 24:
        return None
    return Key[f"F{index}"]


def _resolve_key_code(token: str) -> Optional[Key]:
    letter_or_digit = _resolve_letter_or_digit(token)
    if letter_or_digit is not None:
        return letter_or_digit

    special = _SPECIAL_KEYS.get(token)
    if special is not None:
        return special

    function_key = _resolve_function_key(token)
    if function_key is not None:
        return function_key
    return None


def parse_hotkey_binding(text: str) -> Optional[HotkeyBinding]:
    """Parse a textual hotkey definition (e.g. ``Ctrl+Shift+V``)."""
    normalized = str(text or "").strip()
    if not normalized:
        return None

    tokens = [tok for tok in re.split(r"[+\s]+", normalized) if tok]
    if not tokens:
        return None

    key_token = tokens[-1].upper()
    modifier_tokens = [tok.upper() for tok in tokens[:-1]]

    modifiers = KeyModifier.NONE
    for modifier in modifier_tokens:
        mapped = _MODIFIER_TOKENS.get(modifier)
        if mapped is None:
            return None
        modifiers |= mapped

    key_code = _resolve_key_code(key_token)
    if key_code is None:
        return None

    display_tokens = modifier_tokens + [key_token]
    display = "+".join(display_tokens)
    return HotkeyBinding(key=key_code, modifiers=modifiers, display=display)
