import unittest

from lib.script.SEanima.clip import (
    DEFAULT_EXIT_ANIMATION_FOLDER,
    DEFAULT_START_ANIMATION_FOLDER,
    list_animation_folder_choices,
    resolve_animation_clip,
)


class SEAnimaClipConfigTests(unittest.TestCase):
    def test_animation_folder_choices_include_defaults(self):
        choices = list_animation_folder_choices()

        self.assertIn(DEFAULT_START_ANIMATION_FOLDER, choices)
        self.assertIn(DEFAULT_EXIT_ANIMATION_FOLDER, choices)

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
