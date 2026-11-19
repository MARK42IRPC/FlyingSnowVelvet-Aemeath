"""Background asset warm-up for Dango Showdown (no GUI toolkit).

Decoding the twelve 512 px dango frames and the 1.6 MB starburst GIF costs
roughly a quarter second. Doing that in the widget constructor freezes the
window on open, so a daemon worker decodes everything up front and the widget
only picks up immutable results. ``ImageResource``/``RasterFrame`` are frozen
value objects, so handing them to the Qt thread is safe without locks on the
data itself; only the small ``assets()`` snapshot is guarded.

Tests can pass ``inline=True`` to build everything on the calling thread.
"""

from __future__ import annotations

import threading
from pathlib import Path

from lib.core.render.visuals.image_loader import load_image_resource, resize_image_resource
from lib.core.render.visuals.resources import ImageResource
from lib.core.render.visuals.visuals import load_effect_resource

from . import constants as C
from .roster import ROSTER


def laser_palette(position: float) -> tuple[int, int, int]:
    """Blue-to-pink identity shared by the decoded beam and existing sparks."""
    stops = ((0.0, (72, 158, 255)), (.40, (115, 218, 255)),
             (.65, (207, 199, 255)), (1.0, (255, 122, 215)))
    unit = max(0.0, min(1.0, float(position)))
    for (start, first), (end, second) in zip(stops, stops[1:]):
        if unit <= end:
            ratio = (unit - start) / (end - start)
            return tuple(round(a + (b - a) * ratio) for a, b in zip(first, second))
    return stops[-1][1]


def _gradient_laser(resource: ImageResource) -> ImageResource:
    """Colour the original white beam once, retaining its complete alpha mask.

    Hue follows the beam's long axis. A narrow near-white centre keeps the
    hot core, while the original soft alpha supplies the coloured glow.
    This is resource preparation; the existing image effect still animates
    and draws the beam, and the source PNG is never modified.
    """
    frames = []
    for frame in resource.frames:
        pixels = bytearray(frame.pixels)
        palette = [laser_palette(x / max(1, frame.width - 1)) for x in range(frame.width)]
        for y in range(frame.height):
            distance = abs(y + .5 - frame.height / 2) / max(1.0, frame.height * .14)
            white = .95 * max(0.0, 1.0 - distance) ** 2
            row = y * frame.width * 4
            for x, color in enumerate(palette):
                index = row + x * 4
                for channel in range(3):
                    pixels[index + channel] = round(color[channel] * (1 - white) + 255 * white)
        frames.append(type(frame)(frame.width, frame.height, bytes(pixels), frame.duration_ms))
    return ImageResource(f"{resource.resource_id}:blue-pink", tuple(frames))


def _keyed_sun_spirit(resource: ImageResource) -> ImageResource:
    """Remove the supplied flat violet matte once on the decode worker.

    The artwork is pale green/cyan; its background has blue > green and
    red > green. A soft matte and colour unmix preserve its white rim without
    leaving a violet rectangle or a dark fringe when drawn over the arena.
    Original package/source PNG files remain byte-for-byte unchanged.
    """
    frames = []
    for frame in resource.frames:
        pixels = bytearray(frame.pixels)
        corner = tuple(pixels[:3])
        for index in range(0, len(pixels), 4):
            r, g, b, alpha = pixels[index:index + 4]
            purple = min(r - g, b - g)
            matte = max(0.0, min(1.0, (purple - 8.0) / 88.0))
            opacity = 1.0 - matte
            pixels[index + 3] = round(alpha * opacity)
            if 0.01 < opacity < 1.0:
                for channel in range(3):
                    unmix = (pixels[index + channel] - corner[channel] * matte) / opacity
                    pixels[index + channel] = round(max(0.0, min(255.0, unmix)))
        frames.append(type(frame)(frame.width, frame.height, bytes(pixels), frame.duration_ms))
    return ImageResource(f"{resource.resource_id}:keyed", tuple(frames))


def _tinted(resource: ImageResource, tint, strength: float) -> ImageResource:
    """Recolour hurt frames; pure Python so it can run off the GUI thread."""
    frames = []
    for frame in resource.frames:
        pixels = bytearray(frame.pixels)
        for index in range(0, len(pixels), 4):
            alpha = pixels[index + 3] / 255.0
            if alpha <= 0.0:
                continue
            blend = alpha * strength
            pixels[index] = int(pixels[index] * (1.0 - blend) + tint.red * blend)
            pixels[index + 1] = int(pixels[index + 1] * (1.0 - blend) + tint.green * blend)
            pixels[index + 2] = int(pixels[index + 2] * (1.0 - blend) + tint.blue * blend)
        frames.append(type(frame)(frame.width, frame.height, bytes(pixels), frame.duration_ms))
    return ImageResource(f"{resource.resource_id}:hurt_tint", tuple(frames))


def _blade_content(resource: ImageResource) -> tuple[ImageResource, tuple[float, float]]:
    """Trim only transparent margins; locate the supplied sword's handle pivot.

    The original 960x540 artwork is kept intact on disk. The grip end at
    (35, 268) is the rotation anchor, rather than the texture's centre.
    """
    frame = resource.frames[0]
    visible = [(i % frame.width, i // frame.width)
               for i, alpha in enumerate(frame.pixels[3::4]) if alpha]
    if not visible:
        return resource, (.5, .5)
    left, top = min(x for x, _ in visible), min(y for _, y in visible)
    right, bottom = max(x for x, _ in visible) + 1, max(y for _, y in visible) + 1
    width, height = right - left, bottom - top
    pixels = b"".join(frame.pixels[(y * frame.width + left) * 4:(y * frame.width + right) * 4]
                      for y in range(top, bottom))
    cropped = type(frame)(width, height, pixels, frame.duration_ms)
    pivot = ((35 * frame.width / 960 - left) / width, (268 * frame.height / 540 - top) / height)
    return ImageResource(resource.resource_id + ":content", (cropped,)), pivot


class DangoAssetLoader:
    """Decode sprites, hurt tints and effect textures on a worker thread."""

    def __init__(self, assets_root: str | Path, *, inline: bool = False) -> None:
        self._root = Path(assets_root)
        self._lock = threading.Lock()
        self._assets: dict | None = None
        self._error: str = ""
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
        if inline:
            self._build()
        else:
            self._thread = threading.Thread(
                target=self._build,
                name="dango-assets",
                daemon=True,
            )
            self._thread.start()

    # ── public surface ───────────────────────────────────────────
    @property
    def ready(self) -> bool:
        return self._done.is_set() and self._assets is not None

    @property
    def error(self) -> str:
        return self._error

    def assets(self) -> dict | None:
        with self._lock:
            return self._assets

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)

    # ── worker ───────────────────────────────────────────────────
    def _build(self) -> None:
        try:
            self._assets = self._decode()
        except Exception as exc:  # a broken asset must not kill the game
            self._error = f"{type(exc).__name__}: {exc}"
            self._assets = None
        finally:
            self._done.set()

    def _decode(self) -> dict:
        root = self._root
        target = C.SPRITE_TARGET_PX
        states: dict[tuple[str, str], dict] = {}
        for entry in ROSTER:
            character_id = entry.character_id
            for state, filename in (("idle", "idle.png"), ("attack", "attack.png"), ("hurt", "hurt.png")):
                resource = load_image_resource(
                    root / "dango" / character_id / filename,
                    resource_id=f"dango_showdown.{character_id}.{state}",
                )
                if resource is None:
                    continue
                resource = resize_image_resource(resource, (target, target))
                states[(character_id, state)] = {
                    "resource": resource,
                    "hurt_tint": _tinted(resource, C.HURT_TINT, C.HURT_FLASH_ALPHA) if state == "hurt" else None,
                }
        starburst = load_image_resource(
            root / "effects" / "amis_starburst.gif",
            resource_id="dango_showdown.amis_starburst",
        )
        laser = load_image_resource(
            root / "effects" / "laser_beam.png",
            resource_id="dango_showdown.laser_beam",
        )
        if laser is not None:
            laser = _gradient_laser(laser)
        blade = load_image_resource(root / "effects" / "feixue_blade.png",
                                    resource_id="dango_showdown.feixue_blade")
        blade_pivot = (.5, .5)
        if blade is not None:
            blade, blade_pivot = _blade_content(blade)
        petal = load_image_resource(root / "effects" / "feixue_petal.png",
                                    resource_id="dango_showdown.feixue_petal")
        red_petal = load_image_resource(root / "effects" / "feixue_petal_red.png",
                                        resource_id="dango_showdown.feixue_petal_red")
        slash_trail = load_image_resource(root / "effects" / "feixue_slash_trail.png",
                                          resource_id="dango_showdown.feixue_slash_trail")
        slash_flash = load_image_resource(root / "effects" / "feixue_slash_flash.png",
                                          resource_id="dango_showdown.feixue_slash_flash")
        reverse_trail = None
        if slash_trail is not None:
            frame = slash_trail.frames[0]
            stride = frame.width * 4
            pixels = b"".join(frame.pixels[y * stride:(y + 1) * stride] for y in reversed(range(frame.height)))
            reverse_trail = ImageResource(slash_trail.resource_id + ":reverse", (
                type(frame)(frame.width, frame.height, pixels, frame.duration_ms),))
        # The hand and watermark are static; feathering at load keeps the fire
        # path allocation-free (only straight rotations/alpha in the frame).
        void_hand = load_effect_resource(
            root / "effects" / "void_hand.png",
            {"edge_feather": True, "feather_ratio": C.VOID_HAND_FEATHER_PX / 512.0},
        )
        watermark = load_image_resource(
            root / "effects" / "arena_watermark.webp",
            resource_id="dango_showdown.arena_watermark",
        )
        sun_spirits = {}
        for color in ("yellow", "cyan"):
            resource = load_image_resource(
                root / "effects" / f"sun_spirit_{color}.png",
                resource_id=f"dango_showdown.sun_spirit_{color}",
            )
            sun_spirits[color] = _keyed_sun_spirit(resource) if resource is not None else None
        return {
            "states": states,
            "starburst": starburst,
            "laser": laser,
            "feixue_blade": blade,
            "feixue_blade_pivot": blade_pivot,
            "feixue_petal": petal,
            "feixue_petal_red": red_petal,
            "feixue_slash_trail": slash_trail,
            "feixue_slash_flash": slash_flash,
            "feixue_slash_trail_reverse": reverse_trail,
            "void_hand": void_hand,
            "watermark": watermark,
            "sun_spirits": sun_spirits,
        }


__all__ = ["DangoAssetLoader"]
