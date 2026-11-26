"""人格词拆成用户可编辑段与项目维护段。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lib.script.chat import persona_storage
from lib.script.chat.handler_persona import ChatHandlerPersonaMixin
from lib.script.chat.persona_storage import (
    default_persona_text,
    load_persona_contract,
    reset_user_persona_file,
    resolve_persona_contract_path,
    strip_managed_persona_sections,
)

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_PERSONA = _ROOT / 'resc' / 'persona.txt'


class PersonaContractFileTests(unittest.TestCase):
    def test_contract_file_starts_with_the_tool_section(self):
        text = resolve_persona_contract_path().read_text(encoding='utf-8-sig')

        self.assertTrue(text.lstrip().startswith('[输出格式]'))

    def test_default_persona_keeps_only_the_character_part(self):
        text = (_ROOT / 'resc' / 'persona.txt').read_text(encoding='utf-8-sig')

        self.assertIn('[核心目标]', text)
        for header in persona_storage.MANAGED_PERSONA_SECTION_HEADERS:
            self.assertNotIn(header, text)

    def test_contract_contains_output_format_and_tool_rules(self):
        contract = load_persona_contract()

        self.assertTrue(contract.lstrip().startswith('[输出格式]'))
        self.assertIn('每次回复必须以“[主题]”开头', contract)
        self.assertIn('[工具调用]', contract)
        self.assertIn('[执行前自检]', contract)


class DefaultPersonaAndResetTests(unittest.TestCase):
    def _sandbox(self, tmpdir: str) -> tuple[Path, Path]:
        user_path = Path(tmpdir) / 'user' / 'persona.txt'
        return user_path, _DEFAULT_PERSONA

    def test_default_persona_text_is_the_builtin_template_without_managed_sections(self):
        expected = _DEFAULT_PERSONA.read_text(encoding='utf-8-sig')

        with tempfile.TemporaryDirectory() as tmpdir:
            user_path, template = self._sandbox(tmpdir)
            with patch.object(persona_storage, 'get_user_persona_path', return_value=user_path), \
                    patch.object(persona_storage, 'persona_file_candidates', return_value=(user_path, template)):
                text = default_persona_text()

        self.assertEqual(text, expected)
        for header in persona_storage.MANAGED_PERSONA_SECTION_HEADERS:
            self.assertNotIn(header, text)

    def test_reset_creates_the_default_when_no_user_file_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            user_path, template = self._sandbox(tmpdir)
            with patch.object(persona_storage, 'get_user_persona_path', return_value=user_path), \
                    patch.object(persona_storage, 'persona_file_candidates', return_value=(user_path, template)):
                path, backup = reset_user_persona_file(stamp='20260101_000000')

            self.assertEqual(path, user_path)
            self.assertIsNone(backup)
            self.assertEqual(user_path.read_text(encoding='utf-8-sig'), template.read_text(encoding='utf-8-sig'))

    def test_reset_backs_up_the_old_file_before_writing_the_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            user_path, template = self._sandbox(tmpdir)
            user_path.parent.mkdir(parents=True)
            user_path.write_text('你是旧人格。', encoding='utf-8')

            with patch.object(persona_storage, 'get_user_persona_path', return_value=user_path), \
                    patch.object(persona_storage, 'persona_file_candidates', return_value=(user_path, template)):
                path, backup = reset_user_persona_file(stamp='20260101_000000')

            self.assertEqual(path, user_path)
            self.assertIsNotNone(backup)
            self.assertEqual(backup.name, 'persona_backup_20260101_000000.txt')
            self.assertEqual(backup.read_text(encoding='utf-8'), '你是旧人格。')
            self.assertEqual(user_path.read_text(encoding='utf-8-sig'), template.read_text(encoding='utf-8-sig'))

    def test_two_resets_in_the_same_second_keep_both_backups(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            user_path, template = self._sandbox(tmpdir)
            user_path.parent.mkdir(parents=True)
            user_path.write_text('第一版', encoding='utf-8')

            with patch.object(persona_storage, 'get_user_persona_path', return_value=user_path), \
                    patch.object(persona_storage, 'persona_file_candidates', return_value=(user_path, template)):
                _, first = reset_user_persona_file(stamp='20260101_000000')
                _, second = reset_user_persona_file(stamp='20260101_000000')

            self.assertEqual(first.name, 'persona_backup_20260101_000000.txt')
            self.assertEqual(second.name, 'persona_backup_20260101_000000_2.txt')
            self.assertEqual(first.read_text(encoding='utf-8'), '第一版')
            # 第二次备份的是重置后的默认人设，不是被覆盖掉的旧备份。
            self.assertEqual(
                second.read_text(encoding='utf-8-sig'), template.read_text(encoding='utf-8-sig')
            )


class ManagedSectionStripTests(unittest.TestCase):
    def test_strip_cuts_from_the_first_managed_header(self):
        legacy = (
            '你是爱弥斯。\n\n[输出格式]\n1. 正常。\n\n'
            '[工具调用]\n1. 直接调用函数。\n\n[执行前自检]\n1. 以[主题]开头。'
        )

        cleaned = strip_managed_persona_sections(legacy)

        self.assertTrue(cleaned.startswith('你是爱弥斯。'))
        self.assertNotIn('[输出格式]', cleaned)
        self.assertNotIn('[工具调用]', cleaned)
        self.assertNotIn('[执行前自检]', cleaned)

    def test_strip_keeps_persona_without_managed_sections(self):
        text = '你是爱弥斯。\n\n[说话风格]\n1. 轻快。'

        self.assertEqual(strip_managed_persona_sections(text), text)


class RuntimePersonaTests(unittest.TestCase):
    def test_runtime_persona_concats_the_contract_exactly_once(self):
        legacy = (
            '你是爱弥斯。\n\n[输出格式]\n1. 用户旧的格式规则。\n\n'
            '[工具调用]\n1. 旧的段落。'
        )
        holder = SimpleNamespace(_persona=legacy, _build_recent_memory_block=lambda: '')

        runtime = ChatHandlerPersonaMixin._build_runtime_persona(holder)

        self.assertEqual(runtime.count('[输出格式]'), 1)
        self.assertEqual(runtime.count('[工具调用]'), 1)
        self.assertNotIn('用户旧的格式规则', runtime)
        self.assertNotIn('旧的段落', runtime)
        self.assertIn('[系统时间]', runtime)
        self.assertIn(load_persona_contract(), runtime)


if __name__ == '__main__':
    unittest.main()
