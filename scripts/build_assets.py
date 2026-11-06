#!/usr/bin/env python3
"""Dev-time conversion of the Armbian wordmark SVG into 1-bit PNGs for the OLED.

Runtime never touches the SVG; it only pastes the committed bitmaps.
Usage: uv run scripts/build_assets.py [--check]
"""

import argparse
import io
import re
import sys
from pathlib import Path

import resvg_py
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "assets-src" / "logo-armbian.svg"
OUT = ROOT / "src" / "sys_oled" / "assets"
WIDTHS = (128, 96)
# Light grey fills in the source are stray specks/anti-aliasing debris, not design elements.
SPECK_FILLS = ("#757575", "#b6b6b6", "#d8d8d8")
ALPHA_THRESHOLD = 128
MIN_COMPONENT = 3


def clean_svg(text: str) -> str:
    for fill in SPECK_FILLS:
        text = re.sub(r"<path\s[^>]*fill:" + re.escape(fill) + r"[^>]*/>", "", text, flags=re.S)
    return text


def rasterize(svg: str) -> Image.Image:
    png = resvg_py.svg_to_bytes(svg_string=svg, width=6400)
    img = Image.open(io.BytesIO(bytes(png))).convert("RGBA")
    return img.crop(img.getchannel("A").getbbox())


def drop_small_components(img: Image.Image, min_size: int) -> Image.Image:
    w, h = img.size
    px = img.load()
    seen = set()
    for y in range(h):
        for x in range(w):
            if not px[x, y] or (x, y) in seen:
                continue
            stack, comp = [(x, y)], []
            seen.add((x, y))
            while stack:
                cx, cy = stack.pop()
                comp.append((cx, cy))
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if 0 <= nx < w and 0 <= ny < h and px[nx, ny] and (nx, ny) not in seen:
                        seen.add((nx, ny))
                        stack.append((nx, ny))
            if len(comp) < min_size:
                for cx, cy in comp:
                    px[cx, cy] = 0
    return img


def to_1bit(img: Image.Image, width: int) -> Image.Image:
    height = round(img.height * width / img.width)
    alpha = img.getchannel("A").resize((width, height), Image.Resampling.LANCZOS)
    mono = alpha.point(lambda a: 255 if a >= ALPHA_THRESHOLD else 0).convert("1")
    return drop_small_components(mono, MIN_COMPONENT)


def build() -> dict[str, bytes]:
    master = rasterize(clean_svg(SVG.read_text()))
    out = {}
    for width in WIDTHS:
        buf = io.BytesIO()
        to_1bit(master, width).save(buf, format="PNG", optimize=True)
        out[f"armbian-wordmark-{width}.png"] = buf.getvalue()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if committed PNGs are stale")
    args = parser.parse_args()
    stale = []
    for name, data in build().items():
        path = OUT / name
        if args.check:
            if not path.exists() or path.read_bytes() != data:
                stale.append(name)
        else:
            path.write_bytes(data)
            print(f"wrote {path.relative_to(ROOT)} {Image.open(io.BytesIO(data)).size}")
    if stale:
        print(f"stale assets, run scripts/build_assets.py: {', '.join(stale)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
