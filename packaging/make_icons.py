"""Draw the logo (same shapes as assets/logo.svg) and write every icon size the app needs.

Run after changing the logo:  python packaging/make_icons.py   (needs Pillow; .icns needs macOS)
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
GREEN, PAPER, FOLD, PLAY, LINES = "#2f5d50", "#fbfaf6", "#cfe3db", "#e2a03f", "#9db3ab"


def page_polygon(s, ox=0, oy=0):
    """The page with its top-right corner cut off, in 512-unit coordinates scaled by s."""
    pts = [(150, 114), (172, 92), (298, 92), (362, 156), (362, 398), (340, 420), (172, 420), (150, 398)]
    return [(ox + x * s, oy + y * s) for x, y in pts]


def draw_logo(size: int, margin: float = 0.0) -> Image.Image:
    """Full-colour logo. margin = fraction of the canvas left empty on each side (macOS icon grid)."""
    big = size * 4  # draw large, then shrink, for smooth edges
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    inner = big * (1 - 2 * margin)
    o = big * margin
    s = inner / 512
    d.rounded_rectangle([o, o, o + inner, o + inner], radius=112 * s, fill=GREEN)
    # page body with rounded bottom corners
    d.polygon(page_polygon(s, o, o), fill=PAPER)
    d.rounded_rectangle([o + 150 * s, o + 300 * s, o + 362 * s, o + 420 * s], radius=22 * s, fill=PAPER)
    d.rounded_rectangle([o + 150 * s, o + 92 * s, o + 250 * s, o + 200 * s], radius=22 * s, fill=PAPER)
    d.polygon([(o + 298 * s, o + 92 * s), (o + 298 * s, o + 156 * s), (o + 362 * s, o + 156 * s)], fill=FOLD)
    d.polygon([(o + 196 * s, o + 160 * s), (o + 196 * s, o + 232 * s), (o + 258 * s, o + 196 * s)], fill=PLAY)
    for y, w in ((276, 120), (312, 120), (348, 84)):
        d.rounded_rectangle([o + 196 * s, o + y * s, o + (196 + w) * s, o + (y + 18) * s], radius=9 * s, fill=LINES)
    return img.resize((size, size), Image.LANCZOS)


def draw_menubar_glyph(size: int) -> Image.Image:
    """Black page-with-play-button silhouette on transparent; macOS tints it for light/dark menu bars."""
    big = size * 8
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = big / 512
    # Scale the page to fill the glyph box (it only spans x 150-362 / y 92-420 in the logo).
    k = 1.45
    ox, oy = -150 * s * k + (big - 212 * s * k) / 2, -92 * s * k + (big - 328 * s * k) / 2
    d.polygon(page_polygon(s * k, ox, oy), fill=(0, 0, 0, 255))
    tri = [(196, 160), (196, 232), (258, 196)]
    d.polygon([(ox + x * s * k, oy + y * s * k) for x, y in tri], fill=(0, 0, 0, 0))
    for y, w in ((276, 120), (312, 120), (348, 84)):
        d.rounded_rectangle([ox + 196 * s * k, oy + y * s * k, ox + (196 + w) * s * k, oy + (y + 18) * s * k],
                            radius=9 * s * k, fill=(0, 0, 0, 0))
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    static = ROOT / "article_pipeline" / "web" / "static"
    icons = ROOT / "packaging" / "icons"
    icons.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "assets" / "logo.svg", static / "logo.svg")
    draw_logo(512).save(ROOT / "assets" / "logo-512.png")
    draw_logo(180).save(static / "apple-touch-icon.png")
    draw_logo(32).save(static / "favicon-32.png")
    draw_menubar_glyph(36).save(static / "menubar@2x.png")  # 18pt at 2x

    # Windows .ico (several sizes in one file)
    draw_logo(256).save(icons / "app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

    # macOS .icns: the icon grid leaves ~10% transparent margin around the rounded square
    if sys.platform == "darwin":
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "app.iconset"
            iconset.mkdir()
            for pt in (16, 32, 128, 256, 512):
                for scale in (1, 2):
                    px = pt * scale
                    name = f"icon_{pt}x{pt}{'@2x' if scale == 2 else ''}.png"
                    draw_logo(px, margin=0.098).save(iconset / name)
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icons / "app.icns")], check=True)
    print("Icons written to packaging/icons and article_pipeline/web/static")


if __name__ == "__main__":
    main()
