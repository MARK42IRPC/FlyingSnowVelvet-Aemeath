"""人格词拆成用户可编辑段与项目维护段。"""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from lib.script.chat.handler_persona import ChatHandlerPersonaMixin
from lib.script.chat.persona_storage import (
    load_persona_contract,
    resolve_persona_contract_path,
    strip_managed_persona_sections,
)

_ROOT = Path(__file__).resolve().parents[1]


class PersonaContractFileTests(unittest.TestCase):
    def test_contract_file_starts_with_the_tool_section(self):
        text = resolve_persona_contract_path().read_text(encoding='utf-8-sig')

        self.assertTrue(text.lstrip().startswith('[工具调用]'))

    def test_default_persona_keeps_only_the_character_part(self):
        text = (_ROOT / 'resc' / 'persona.txt').read_text(encoding='utf-8-sig')

        self.assertIn('[输出格式]', text)
        for header in ('[工具调用]', '[工具使用规则]', '[执行前自检]'):
            self.assertNotIn(header, text)

    def test_contract_is_loaded(self):
        self.assertIn('[执行前自检]', load_persona_contract())


class ManagedSectionStripTests(unittest.TestCase):
    def test_strip_cuts_from_the_first_managed_header(self):
        legacy = (
            '你是爱弥斯。\n\n[输出格式]\n1. 正常。\n\n'
            '[工具调用]\n1. 直接调用函数。\n\n[执行前自检]\n1. 以[主题]开头。'
        )

        cleaned = strip_managed_persona_sections(legacy)

        self.assertTrue(cleaned.startswith('你是爱弥斯。'))
        self.assertIn('[输出格式]', cleaned)
        self.assertNotIn('[工具调用]', cleaned)
        self.assertNotIn('[执行前自检]', cleaned)

    def test_strip_keeps_persona_without_managed_sections(self):
        text = '你是爱弥斯。\n\n[说话风格]\n1. 轻快。'

        self.assertEqual(strip_managed_persona_sections(text), text)


class RuntimePersonaTests(unittest.TestCase):
    def test_runtime_persona_concats_the_contract_exactly_once(self):
        legacy = '你是爱弥斯。\n\n[工具调用]\n1. 旧的段落。'
        holder = SimpleNamespace(_persona=legacy, _build_recent_memory_block=lambda: '')

        runtime = ChatHandlerPersonaMixin._build_runtime_persona(holder)

        self.assertEqual(runtime.count('[工具调用]'), 1)
        self.assertNotIn('旧的段落', runtime)
        self.assertIn('[系统时间]', runtime)
        self.assertIn(load_persona_contract(), runtime)


if __name__ == '__main__':
    unittest.main()
