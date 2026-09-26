"""Draws the MyDevAgent Studio icons from Vio's pixel art (the same as in media/vio.js).

    python tools/make_icons.py      (needs Pillow: pip install pillow)

Writes the extension icons (extension/media) and the Studio ones (studio/branding): the app .ico,
Start menu tiles, the empty editor watermark and the installer images.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
MEDIA = ROOT / "extension" / "media"
BRANDING = ROOT / "studio" / "branding"

COLORS = {"P": "#a855f7", "D": "#581c87", "L": "#d8b4fe", "K": "#1a0b2e", "W": "#ffffff", "C": "#f472b6"}
VIO = [  # "ask" expression: open eyes and a smile
    ".....DDDD.....",
    "...DDPPPPDD...",
    "..DPLPPPPPPD..",
    ".DPPWKPPWKPPD.",
    ".DPPKKPPKKPPD.",
    ".DPCPPKKPPCPD.",
    "DPDPDPPPPDPDPD",
    "D.P.P.DD.P.P.D",
]
BODY = set("PDLC")  # in the silhouette: the eyes and mouth stay holes


def rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def sprite(scale: int) -> Image.Image:
    img = Image.new("RGBA", (14 * scale, 8 * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for y, row in enumerate(VIO):
        for x, cell in enumerate(row):
            if cell in COLORS:
                draw.rectangle([x * scale, y * scale, (x + 1) * scale - 1, (y + 1) * scale - 1], fill=rgb(COLORS[cell]))
    return img


def gradient(size: tuple[int, int], top: str, bottom: str) -> Image.Image:
    w, h = size
    a, b = rgb(top), rgb(bottom)
    img = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        img.putpixel((0, y), tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3)))
    return img.resize((w, h))


def glow(size: tuple[int, int], center: tuple[int, int], radius: int, color: str, alpha: int) -> Image.Image:
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    cx, cy = center
    ImageDraw.Draw(layer).ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=(*rgb(color), alpha))
    return layer.filter(ImageFilter.GaussianBlur(radius / 2))


def app_icon(size: int) -> Image.Image:
    """Black-purple rounded square with Vio in the middle, drawn for each size (no blurry pixels)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=round(size * 0.22), fill=255)
    img.paste(gradient((size, size), "#2e1065", "#0b0714"), (0, 0), mask)
    scale = max(1, (size * 13 // 16) // 14)
    vio = sprite(scale)
    x = (size - vio.width) // 2
    y = (size - vio.height) // 2 + (size // 32)
    if size >= 48:
        img.alpha_composite(glow((size, size), (size // 2, y + vio.height // 2), size // 3, "#a855f7", 120))
        img.putalpha(Image.composite(img.getchannel("A"), Image.new("L", (size, size), 0), mask))
    img.alpha_composite(vio, (x, y))
    return img


def silhouette_svg(color: str, opacity: float, pad: int = 3) -> str:
    """Vio as a single-color silhouette: the eyes and mouth are holes."""
    rects = "".join(f'<rect x="{x}" y="{y + pad}" width="1.02" height="1.02"/>'
                    for y, row in enumerate(VIO) for x, cell in enumerate(row) if cell in BODY)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 14 {8 + 2 * pad}" shape-rendering="crispEdges">'
            f'<g fill="{color}" fill-opacity="{opacity}">{rects}</g></svg>\n')


def color_svg() -> str:
    rects = "".join(f'<rect x="{x}" y="{y + 3}" width="1.02" height="1.02" fill="{COLORS[cell]}"/>'
                    for y, row in enumerate(VIO) for x, cell in enumerate(row) if cell in COLORS)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 14 14" shape-rendering="crispEdges">{rects}</svg>\n'


def wizard(width: int, height: int) -> Image.Image:
    img = gradient((width, height), "#2e1065", "#07050c").convert("RGBA")
    scale = max(2, (width * 7 // 10) // 14)
    vio = sprite(scale)
    x, y = (width - vio.width) // 2, height // 3 - vio.height // 2
    img.alpha_composite(glow((width, height), (width // 2, y + vio.height // 2), width // 2, "#a855f7", 110))
    img.alpha_composite(vio, (x, y))
    return img.convert("RGB")


def main() -> None:
    MEDIA.mkdir(parents=True, exist_ok=True)
    BRANDING.mkdir(parents=True, exist_ok=True)
    app_icon(256).save(MEDIA / "icon.png")
    (MEDIA / "vio-activity.svg").write_text(silhouette_svg("#ffffff", 1.0), encoding="utf-8")

    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    icons = [app_icon(s) for s in sizes]
    icons[-1].save(BRANDING / "vio.ico", sizes=[(s, s) for s in sizes], append_images=icons[:-1])
    app_icon(150).save(BRANDING / "tile-150.png")
    app_icon(70).save(BRANDING / "tile-70.png")
    (BRANDING / "vio.svg").write_text(color_svg(), encoding="utf-8")
    for name, color, opacity in [("dark", "#a855f7", 0.22), ("light", "#7c3aed", 0.16),
                                 ("hcDark", "#ffffff", 0.35), ("hcLight", "#000000", 0.35)]:
        (BRANDING / f"letterpress-{name}.svg").write_text(silhouette_svg(color, opacity, pad=0), encoding="utf-8")
    wizard(164, 314).save(BRANDING / "wizard.bmp")
    wizard(328, 628).save(BRANDING / "wizard-2x.bmp")
    small = Image.new("RGB", (55, 55), rgb("#0b0714"))
    small.paste(app_icon(55), (0, 0), app_icon(55))
    small.save(BRANDING / "wizard-small.bmp")
    big = Image.new("RGB", (110, 110), rgb("#0b0714"))
    big.paste(app_icon(110), (0, 0), app_icon(110))
    big.save(BRANDING / "wizard-small-2x.bmp")
    print("icons written to", MEDIA.relative_to(ROOT), "and", BRANDING.relative_to(ROOT))


if __name__ == "__main__":
    main()
