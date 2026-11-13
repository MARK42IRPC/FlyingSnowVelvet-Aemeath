from __future__ import annotations

import unittest
from unittest.mock import patch

from lib.core.effect_utils import spawn_effect
from lib.core.event.center import EventType
from lib.script.effects.flash_text_effect import FlashTextEffectScript
from lib.script.effects.smooth_image_show_effect import SmoothImageShowEffectScript


class _EventSink:
    def __init__(self) -> None:
        self.events = []

    def publish(self, event) -> None:
        self.events.append(event)


class EffectContractTests(unittest.TestCase):
    def test_public_effect_request_deep_copies_plain_data(self):
        sink = _EventSink()
        options = {"color": [255, 128, 64], "nested": {"enabled": True}}

        with patch("lib.core.effect_utils.get_event_center", return_value=sink):
            spawn_effect("example", anchor_data=(10, 20), effect_options=options, z=7)

        options["color"][0] = 0
        event = sink.events[0]
        self.assertIs(event.type, EventType.EFFECT_REQUEST)
        self.assertEqual(event.data["anchor_data"], (10, 20))
        self.assertEqual(event.data["effect_options"]["color"], [255, 128, 64])
        self.assertEqual(event.data["effect_options"]["z"], 7)

    def test_public_effect_request_rejects_opaque_backend_objects(self):
        with patch("lib.core.effect_utils.get_event_center") as event_center:
            with self.assertRaises(TypeError):
                spawn_effect("example", effect_options={"image": object()})

        event_center.return_value.publish.assert_not_called()

    def test_smooth_image_script_keeps_only_resource_identity(self):
        effects = SmoothImageShowEffectScript().create_effects(
            anchor_type="point",
            anchor_data=(100, 100),
            effect_options={
                "resource_path": "resc/example.webp",
                "intro_start_pos": (0, 10),
                "intro_duration": 0.2,
                "display_pos": (100, 110),
                "display_duration": 1.0,
                "outro_end_pos": (200, 10),
                "outro_duration": 0.2,
                "scale": 0.5,
            },
            request_context={"offset_x": 10, "offset_y": 20},
        )

        self.assertEqual(len(effects), 1)
        effect = effects[0]
        self.assertEqual(effect.resource_path, "resc/example.webp")
        self.assertEqual(effect.display_pos, (90.0, 90.0))
        self.assertFalse(hasattr(effect, "pixmap"))

    def test_flash_text_script_keeps_plain_render_data(self):
        effects = FlashTextEffectScript().create_effects(
            anchor_type="point",
            anchor_data=(50, 60),
            effect_options={
                "text": "test",
                "fade_in_duration": 0.1,
                "hold_duration": 0.2,
                "fade_out_duration": 0.1,
                "color": (300, -1, 20),
            },
        )

        self.assertEqual(len(effects), 1)
        effect = effects[0]
        self.assertEqual(effect.color, (255, 0, 20))
        self.assertFalse(hasattr(effect, "pixmap"))


if __name__ == "__main__":
    unittest.main()
