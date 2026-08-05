import tempfile
from pathlib import Path

from PIL import Image

from lib.core.graphics.gif_loader import GifLoader
from lib.core.graphics.resources import ImageResource, RasterFrame


def test_gif_loader_returns_pure_rgba_resources():
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "idle.gif"
        first = Image.new("RGBA", (2, 1), (255, 0, 0, 255))
        second = Image.new("RGBA", (2, 1), (0, 0, 255, 255))
        first.save(
            source,
            save_all=True,
            append_images=[second],
            duration=40,
            loop=0,
            disposal=2,
        )

        resources = GifLoader([str(source)]).load_all()

    resource = resources["idle"]
    assert isinstance(resource, ImageResource)
    assert len(resource.frames) == 2
    assert all(isinstance(frame, RasterFrame) for frame in resource.frames)
    assert resource.frames[0].stride == 8
    assert resource.frames[0].duration_ms == 40
    assert resource.frames[0].pixels[:4] == bytes((255, 0, 0, 255))
