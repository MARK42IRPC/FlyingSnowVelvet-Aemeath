"""Small value types shared by graphics and gameplay code."""
from __future__ import annotations

import colorsys
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Color:
    """An immutable 8-bit RGBA color value."""

    red: int = 0
    green: int = 0
    blue: int = 0
    alpha: int = 255

    def __post_init__(self) -> None:
        for name in ("red", "green", "blue", "alpha"):
            value = max(0, min(255, int(getattr(self, name))))
            object.__setattr__(self, name, value)

    def with_alpha(self, alpha: int) -> "Color":
        return Color(self.red, self.green, self.blue, alpha)

    def lighter(self, factor: int = 150) -> "Color":
        factor = int(factor)
        if factor <= 0:
            raise ValueError("color factor must be positive")
        if factor < 100:
            return self.darker(max(1, 10000 // factor))

        hue, saturation, value = colorsys.rgb_to_hsv(
            self.red / 255.0,
            self.green / 255.0,
            self.blue / 255.0,
        )
        saturation *= 255.0
        value = factor * value * 255.0 / 100.0
        if value > 255.0:
            saturation = max(0.0, saturation - (value - 255.0))
            value = 255.0
        red, green, blue = colorsys.hsv_to_rgb(
            hue,
            saturation / 255.0,
            value / 255.0,
        )
        return Color(round(red * 255), round(green * 255), round(blue * 255), self.alpha)

    def darker(self, factor: int = 200) -> "Color":
        factor = int(factor)
        if factor <= 0:
            raise ValueError("color factor must be positive")
        if factor < 100:
            return self.lighter(max(1, 10000 // factor))

        hue, saturation, value = colorsys.rgb_to_hsv(
            self.red / 255.0,
            self.green / 255.0,
            self.blue / 255.0,
        )
        value = value * 100.0 / factor
        red, green, blue = colorsys.hsv_to_rgb(hue, saturation, value)
        return Color(round(red * 255), round(green * 255), round(blue * 255), self.alpha)


@dataclass(frozen=True, slots=True)
class FontSpec:
    """Backend-neutral font selection for text draw data."""

    family: str
    pixel_size: int
    bold: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "family", str(self.family or ""))
        object.__setattr__(self, "pixel_size", max(1, int(self.pixel_size)))
        object.__setattr__(self, "bold", bool(self.bold))


def coerce_color(value: object) -> Color | None:
    """Convert color-like values without importing a GUI toolkit."""
    if isinstance(value, Color):
        return value
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        try:
            alpha = value[3] if len(value) >= 4 else 255
            return Color(value[0], value[1], value[2], alpha)
        except (TypeError, ValueError):
            return None

    components = []
    for name, fallback in (("red", None), ("green", None), ("blue", None), ("alpha", 255)):
        component = getattr(value, name, fallback)
        if callable(component):
            component = component()
        components.append(component)
    try:
        return Color(*components)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class Point:
    """A two-dimensional point in desktop coordinates."""

    x: float = 0.0
    y: float = 0.0

    def __sub__(self, other: "Point") -> "Point":
        return Point(self.x - other.x, self.y - other.y)

    def __add__(self, other: "Point") -> "Point":
        return Point(self.x + other.x, self.y + other.y)


def coerce_point(value: object) -> Point | None:
    """Convert a point-like value without depending on a GUI toolkit."""
    if isinstance(value, Point):
        return value
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        try:
            return Point(float(value[0]), float(value[1]))
        except (TypeError, ValueError):
            return None

    x = getattr(value, "x", None)
    y = getattr(value, "y", None)
    if callable(x):
        x = x()
    if callable(y):
        y = y()
    try:
        return Point(float(x), float(y))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class Size:
    """A two-dimensional size."""

    width: float = 0.0
    height: float = 0.0


@dataclass(frozen=True, slots=True)
class Rect:
    """A rectangle represented by its top-left point and size."""

    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0

    @property
    def top_left(self) -> Point:
        return Point(self.x, self.y)

    @property
    def size(self) -> Size:
        return Size(self.width, self.height)


def coerce_rect(value: object) -> Rect | None:
    """Convert a rectangle-like value without depending on a GUI toolkit."""
    if isinstance(value, Rect):
        return value
    if isinstance(value, (tuple, list)) and len(value) >= 4:
        try:
            return Rect(*(float(item) for item in value[:4]))
        except (TypeError, ValueError):
            return None

    components = []
    for name in ("x", "y", "width", "height"):
        component = getattr(value, name, None)
        if callable(component):
            component = component()
        components.append(component)
    try:
        return Rect(*(float(component) for component in components))
    except (TypeError, ValueError):
        return None
