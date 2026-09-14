import os
import unittest

from lib.script.SEanima.clip import (
    DEFAULT_EXIT_ANIMATION_FOLDER,
    DEFAULT_START_ANIMATION_FOLDER,
    get_animation_root,
    list_animation_folder_choices,
    list_detected_animation_folder_names,
    resolve_animation_clip,
)


class SEAnimaClipConfigTests(unittest.TestCase):
    def test_animation_folder_choices_include_defaults(self):
        choices = list_animation_folder_choices()

        self.assertIn(DEFAULT_START_ANIMATION_FOLDER, choices)
        self.assertIn(DEFAULT_EXIT_ANIMATION_FOLDER, choices)

    def test_shipped_config_defaults_match_the_clip_constants(self):
        """config_animation.py 的出厂值必须与 clip.py 的默认目录一致。

        LTS1.0.6beta2 就把退出动画默认写成了 `爱弥斯联合_anima`，但配置字典一直是
        `星炬学院_anima`：带配置的默认值覆盖了代码默认值，用户没改过设置也会拿到旧动画。
        """
        from config.config import ANIMATION

        self.assertEqual(
            ANIMATION["start_animation_folder"], DEFAULT_START_ANIMATION_FOLDER
        )
        self.assertEqual(
            ANIMATION["exit_animation_folder"], DEFAULT_EXIT_ANIMATION_FOLDER
        )

    def test_default_animation_folders_ship_frames(self):
        if not os.path.isdir(get_animation_root()):
            self.skipTest("动画资源目录不在源码树中")
        detected = list_detected_animation_folder_names()

        self.assertIn(DEFAULT_START_ANIMATION_FOLDER, detected)
        self.assertIn(DEFAULT_EXIT_ANIMATION_FOLDER, detected)

    def test_default_start_clip_resolves_to_configured_folder(self):
        clip = resolve_animation_clip("start", {
            "frame_fps": 60,
            "start_exit_enabled": True,
            "start_animation_folder": DEFAULT_START_ANIMATION_FOLDER,
        })

        self.assertEqual(clip.folder_name, DEFAULT_START_ANIMATION_FOLDER)
        self.assertTrue(clip.folder_path.endswith(DEFAULT_START_ANIMATION_FOLDER))

    def test_default_exit_clip_resolves_to_configured_folder(self):
        clip = resolve_animation_clip("exit", {
            "frame_fps": 60,
            "start_exit_enabled": True,
            "exit_animation_folder": DEFAULT_EXIT_ANIMATION_FOLDER,
        })

        self.assertEqual(clip.folder_name, DEFAULT_EXIT_ANIMATION_FOLDER)
        self.assertTrue(clip.folder_path.endswith(DEFAULT_EXIT_ANIMATION_FOLDER))


if __name__ == "__main__":
    unittest.main()
