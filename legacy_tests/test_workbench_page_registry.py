import unittest

from lib.script.workbench.builtin_pages import builtin_tool_page_specs
from lib.script.workbench.page_registry import (
    WorkbenchPageRegistry,
    WorkbenchPageSpec,
    default_page_spec,
)


class WorkbenchPageRegistryTests(unittest.TestCase):
    def test_default_metadata_groups_existing_pages(self):
        self.assertEqual(default_page_spec("ai").group, "智能交互")
        self.assertEqual(default_page_spec("office").group, "智能交互")
        self.assertEqual(default_page_spec("scene_objects").group, "桌宠与场景")
        self.assertEqual(default_page_spec("audio_music").group, "声音与媒体")
        self.assertEqual(default_page_spec("game_manager").group, "扩展与游戏")
        self.assertEqual(default_page_spec("bug_tracker").group, "系统与维护")

    def test_about_pages_are_not_in_primary_navigation(self):
        registry = WorkbenchPageRegistry()
        registry.extend(
            (
                default_page_spec("overview"),
                default_page_spec("contribution_list"),
                default_page_spec("sponsor_author"),
            )
        )

        self.assertEqual(
            tuple(spec.page_id for spec in registry.navigation_pages()),
            ("overview",),
        )
        self.assertEqual(len(registry.all()), 3)

    def test_search_matches_title_description_and_keywords(self):
        registry = WorkbenchPageRegistry()
        registry.extend(
            (
                default_page_spec("ai"),
                default_page_spec("audio_music"),
                default_page_spec("bug_tracker"),
            )
        )

        self.assertEqual(
            tuple(spec.page_id for spec in registry.search("麦克风")),
            ("audio_music",),
        )
        self.assertEqual(
            tuple(spec.page_id for spec in registry.search("日志 错误")),
            ("bug_tracker",),
        )
        self.assertEqual(registry.search("不存在"), ())

    def test_duplicate_page_id_is_rejected(self):
        registry = WorkbenchPageRegistry()
        registry.register(WorkbenchPageSpec("page", "Page", "Group"))

        with self.assertRaisesRegex(ValueError, "duplicate"):
            registry.register(WorkbenchPageSpec("page", "Other", "Group"))

    def test_invalid_page_metadata_is_rejected(self):
        with self.assertRaises(ValueError):
            WorkbenchPageSpec("", "Page", "Group")
        with self.assertRaises(ValueError):
            WorkbenchPageSpec("page", "", "Group")
        with self.assertRaises(ValueError):
            WorkbenchPageSpec("page", "Page", "")

    def test_builtin_tool_pages_use_one_metadata_and_factory_registry(self):
        specs = builtin_tool_page_specs()

        self.assertEqual(
            tuple(spec.page_id for spec in specs),
            ("office", "game_manager", "bug_tracker"),
        )
        self.assertEqual(
            tuple(spec.title for spec in specs),
            ("办公模式", "游戏包", "故障跟踪"),
        )
        self.assertTrue(all(callable(spec.factory) for spec in specs))


if __name__ == "__main__":
    unittest.main()
