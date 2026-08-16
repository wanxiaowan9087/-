"""Import generated product renders into the frontend asset catalog.

The X9 Edge export has a light checkerboard baked into an RGB image. Convert
that known background range to transparency before copying it into the app.
"""

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(r"D:\edge下载")
ASSETS = ROOT / "frontend" / "src" / "assets"


def remove_light_checkerboard(source: Path, target: Path) -> None:
    image = Image.open(source).convert("RGB")
    pixels = []
    for red, green, blue in image.getdata():
        # Checkerboard cells are neutral near-white (#f2f2f2 to #ffffff).
        is_background = min(red, green, blue) >= 232 and max(red, green, blue) - min(red, green, blue) <= 12
        pixels.append((red, green, blue, 0 if is_background else 255))
    result = Image.new("RGBA", image.size)
    result.putdata(pixels)
    result.save(target, format="PNG", optimize=True)


def copy_transparent(source: Path, target: Path) -> None:
    Image.open(source).convert("RGBA").save(target, format="PNG", optimize=True)


def main() -> None:
    copy_transparent(SOURCE / "image-1786866333991-01.png", ASSETS / "robot-s8-air.png")
    remove_light_checkerboard(SOURCE / "image-1786873172213-01 (1).png", ASSETS / "robot-x9-edge.png")
    copy_transparent(SOURCE / "image-1786873282387-01.png", ASSETS / "robot-m6-mini.png")


if __name__ == "__main__":
    main()
