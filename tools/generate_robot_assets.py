"""Create six deterministic catalog renders from the three approved base renders.

The source catalog currently has three robot families. Each SKU gets its own
PNG so product records never point at the same image URL. Replace these
generated renders with dedicated product photography when it becomes
available.
"""

from colorsys import rgb_to_hsv, hsv_to_rgb
from pathlib import Path

from PIL import Image, ImageEnhance


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "frontend" / "src" / "assets"


def hue_shift(image: Image.Image, degrees: float) -> Image.Image:
    rgb = image.convert("RGB")
    pixels = []
    shift = degrees / 360.0
    for red, green, blue in rgb.getdata():
        hue, saturation, value = rgb_to_hsv(red / 255, green / 255, blue / 255)
        red, green, blue = hsv_to_rgb((hue + shift) % 1, saturation, value)
        pixels.append((round(red * 255), round(green * 255), round(blue * 255)))
    rgb.putdata(pixels)
    return Image.merge("RGBA", (*rgb.split(), image.getchannel("A")))


def render(source_name: str, target_name: str, *, brightness: float, saturation: float,
           contrast: float, hue: float, angle: float, scale: float) -> None:
    source = Image.open(ASSETS / source_name).convert("RGBA")
    image = hue_shift(source, hue)
    image = ImageEnhance.Color(image).enhance(saturation)
    image = ImageEnhance.Brightness(image).enhance(brightness)
    image = ImageEnhance.Contrast(image).enhance(contrast)

    if scale != 1:
        size = round(image.width * scale)
        image = image.resize((size, size), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", source.size)
        canvas.alpha_composite(image, ((source.width - size) // 2, (source.height - size) // 2))
        image = canvas
    if angle:
        image = image.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False)

    image.save(ASSETS / target_name, format="PNG", optimize=True)


def main() -> None:
    render("robot-ivory.png", "robot-s8-luna.png", brightness=.98, saturation=1.05, contrast=1.03, hue=0, angle=1, scale=1.02)
    render("robot-ivory.png", "robot-s8-air.png", brightness=1.08, saturation=.78, contrast=.96, hue=-8, angle=-3, scale=.90)
    render("robot-graphite.png", "robot-x9-obsidian.png", brightness=.88, saturation=.92, contrast=1.12, hue=0, angle=-1, scale=1.02)
    render("robot-graphite.png", "robot-x9-edge.png", brightness=1.04, saturation=1.24, contrast=1.03, hue=9, angle=2, scale=.94)
    render("robot-terracotta.png", "robot-m6-terra.png", brightness=.93, saturation=1.16, contrast=1.04, hue=0, angle=1, scale=1.02)
    render("robot-terracotta.png", "robot-m6-mini.png", brightness=1.12, saturation=.72, contrast=.96, hue=8, angle=-2, scale=.88)


if __name__ == "__main__":
    main()
