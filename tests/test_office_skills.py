from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "resc" / "agent"

EXPECTED_SKILLS = (
    "fsv-office-workflow",
    "fsv-desktop-pet-architecture",
    "fsv-safe-editing",
    "fsv-test-verification",
    "fsv-windows-powershell",
    "fsv-workbench-ui",
    "fsv-browser-ui-check",
    "fsv-browser-research",
    "fsv-dependency-maintenance",
    "fsv-release-validation",
)


def _frontmatter(text: str) -> str:
    match = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    if match is None:
        raise AssertionError("SKILL.md is missing YAML frontmatter")
    return match.group(1)


class OfficeSkillTests(unittest.TestCase):
    def test_bundled_skill_set_has_expected_names_and_valid_frontmatter(self):
        for skill_name in EXPECTED_SKILLS:
            path = SKILL_ROOT / skill_name / "SKILL.md"
            with self.subTest(skill=skill_name):
                self.assertTrue(path.is_file())
                body = path.read_text(encoding="utf-8")
                frontmatter = _frontmatter(body)
                self.assertRegex(frontmatter, rf"(?m)^name: {re.escape(skill_name)}$")
                self.assertRegex(frontmatter, r"(?m)^description:\s+.+$")
                self.assertNotIn("agent.txt", body)

    def test_only_explicit_user_only_skills_disable_model_invocation(self):
        user_only = {
            "fsv-browser-research",
            "fsv-dependency-maintenance",
            "fsv-release-validation",
        }
        for skill_name in EXPECTED_SKILLS:
            frontmatter = _frontmatter(
                (SKILL_ROOT / skill_name / "SKILL.md").read_text(encoding="utf-8")
            )
            has_disable_flag = bool(
                re.search(r"(?m)^disable-model-invocation:\s*true\s*$", frontmatter)
            )
            has_user_flag = bool(re.search(r"(?m)^user-invocable:\s*true\s*$", frontmatter))
            with self.subTest(skill=skill_name):
                self.assertEqual(has_disable_flag, skill_name in user_only)
                self.assertEqual(has_user_flag, skill_name in user_only)


if __name__ == "__main__":
    unittest.main()
