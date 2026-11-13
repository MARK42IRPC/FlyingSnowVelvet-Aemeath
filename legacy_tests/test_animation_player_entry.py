import os
import unittest

from lib.core.render.backends.qt.widgets import animation_player_main


class AnimationPlayerEntryTests(unittest.TestCase):
    def test_entry_point_adds_project_root_to_sys_path(self):
        """播放器以绝对脚本路径启动时，必须能把仓库根加入 sys.path。

        回归：`sys.path[0]` 是 `.../qt/widgets`，直接导入 `lib.core...` 会
        `ModuleNotFoundError`，子进程窗口一闪即退。
        """
        import sys

        saved = list(sys.path)
        try:
            sys.path[:] = [p for p in sys.path if not p.endswith("WORKINGSPACE")]
            animation_player_main._ensure_project_root_on_path()
            self.assertIn(os.getcwd(), sys.path)
            import lib.core.render.backends.qt.widgets.animation_player  # noqa: F401
        finally:
            sys.path[:] = saved

    def test_entry_point_registers_seanima_hooks_before_building_window(self):
        """独立进程必须先导入产品垫片，否则 AnimationWindow 解码出 0 帧。"""
        import lib.script.ui.animation_player  # noqa: F401
        from lib.core.render.backends.qt.widgets import tier_d_seams

        for name in (
            "animation_player:resolve_clip",
            "animation_player:build_playback_plan",
            "animation_player:select_playback_files",
            "animation_player:decode_frame_to_bytes",
        ):
            self.assertIsNotNone(tier_d_seams.hook(name), name)

    def test_entry_point_imports_product_shim_inside_main(self):
        with open(animation_player_main.__file__, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('import_module("lib.script.ui.animation_player")', source)
        self.assertLess(
            source.index('import_module("lib.script.ui.animation_player")'),
            source.index("from lib.core.render.backends.qt.widgets.animation_player"),
        )


if __name__ == "__main__":
    unittest.main()
