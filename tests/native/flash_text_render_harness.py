"""Windows-platform Qt render harness for the flash text clipping regression.

Qt's ``offscreen`` plugin ships no font engine on Windows, so this script runs
under the real ``windows`` platform and rasterises the shared presenter output
into PNGs. ``tests/test_flash_text_visual.py`` inspects those pixels.
"""

from __future__ import annotations

import json
import os
import sys

import PyQt5

_QT_ROOT = os.path.dirname(PyQt5.__file__)
os.environ.setdefault(
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    os.path.join(_QT_ROOT, "Qt5", "plugins", "platforms"),
)
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_QT_ROOT, "Qt5", "plugins"))
os.environ.pop("QT_QPA_PLATFORM", None)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtWidgets import QApplication

from lib.core.graphics.commands import DrawBatch, TextCommand
from lib.core.graphics.types import Color, Rect
from lib.core.graphics.visuals import build_effect_batch, resolve_effect_font
from lib.core.layer import Layer
from lib.core.qt_bridge.draw_backend import QtDrawBackend
from lib.core.qt_bridge.effect_system import _prepare_effect_backend_state
from lib.script.effects.flash_text_effect import FlashTextEffectScript

CANVAS = (960, 160)
CENTER = (480.0, 80.0)
FONT_SIZE = 40
TEXTS = (
    "随机消除三行",
    "重力压实所有方块",
    "短时间大幅提升红条概率",
    "填充填充率最低的三列",
    "消除颜色大于4的行",
    "引爆并生成日灵方块",
)


def make_effect(text: str):
    effect = FlashTextEffectScript().create_effects(
        anchor_type="point",
        anchor_data=CENTER,
        effect_options={
            "text": text,
            "center_pos": CENTER,
            "fade_in_duration": 0.0,
            "fade_in_frequency": 0.0,
            "hold_duration": 1.0,
            "fade_out_duration": 0.0,
            "fade_out_frequency": 0.0,
            "font_size": FONT_SIZE,
            "color": [255, 255, 255],
            "glow": 0.0,
        },
        request_context={"offset_x": 0.0, "offset_y": 0.0},
    )[0]
    effect.layer = int(Layer.EFFECT)
    effect._render_x = effect.x
    effect._render_y = effect.y
    effect._render_opacity = 1.0
    effect._render_scale = 1.0
    effect._render_rotation = 0.0
    return effect


def text_command(text: str, rect: Rect) -> TextCommand:
    return TextCommand(
        text,
        resolve_effect_font(make_effect(text)),
        Color(255, 255, 255),
        rect,
        int(Qt.AlignHCenter | Qt.AlignVCenter),
        1.0,
        int(Layer.EFFECT),
        0,
        0,
    )


def render(command: TextCommand, path: str) -> None:
    image = QImage(CANVAS[0], CANVAS[1], QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    try:
        QtDrawBackend().render(DrawBatch((command,)), painter)
    finally:
        painter.end()
    if not image.save(path, "PNG"):
        raise SystemExit(f"failed to write {path}")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        return 2
    output = argv[1]
    os.makedirs(output, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    if application.platformName() != "windows":
        return 3

    report = {"font_family": resolve_effect_font(make_effect("中")).family, "cases": []}
    center_x, center_y = CENTER
    for index, text in enumerate(TEXTS):
        measured = make_effect(text)
        _prepare_effect_backend_state(measured)
        estimate_case = make_effect(text)
        legacy_width = len(text) * FONT_SIZE * 0.72
        legacy_height = FONT_SIZE * 1.6
        cases = {
            "measured": build_effect_batch([measured]).commands[-1],
            "estimate": build_effect_batch([estimate_case]).commands[-1],
            "legacy": text_command(text, Rect(
                center_x - legacy_width / 2.0,
                center_y - legacy_height / 2.0,
                legacy_width,
                legacy_height,
            )),
            "reference": text_command(text, Rect(
                center_x - 460.0,
                center_y - 60.0,
                920.0,
                120.0,
            )),
        }
        entry = {"text": text, "rects": {}}
        for name, command in cases.items():
            path = os.path.join(output, f"{index}-{name}.png")
            render(command, path)
            entry["rects"][name] = [
                command.rect.x,
                command.rect.y,
                command.rect.width,
                command.rect.height,
            ]
        entry["measured_text_w"] = float(getattr(measured, "_text_w", 0.0))
        entry["measured_text_h"] = float(getattr(measured, "_text_h", 0.0))
        report["cases"].append(entry)

    with open(os.path.join(output, "report.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
