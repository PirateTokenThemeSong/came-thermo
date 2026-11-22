"""Generate the project logo from the official CAME Connect thermo app icon.

Takes the original raster, keeps its artwork (thermometer + CAME wordmark),
recolours every stroke to white, and puts it on a plain rounded square filled
with Home Assistant blue (#18BCF2).

Outputs:
  assets/icon.svg      white artwork on HA blue, normal rounded-square mask
  assets/icon.png      1024x1024
  assets/icon@2x.png   512x512
  custom_components/came_thermo/brand/icon.png (+ @2x)

Run from the repo root:  python tools/make_logo.py
"""

from __future__ import annotations

import io
import urllib.request
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SOURCE_CACHE = ROOT / "original.webp"

SOURCE_URL = (
    "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fwww.came.com"
    "%2Fglobal%2Fsites%2Fdefault%2Ffiles%2Fnew-connect-landing%2Fassets%2Fimg"
    "%2Fconnect-app-icon-app-thermo.webp&f=1&nofb=1&ipo=images"
)

HA_BLUE = "#18BCF2"
SIZE = 1024
RADIUS = round(SIZE * 0.224)  # plain app-icon rounding
TRACE_SCALE = 8  # contour tracing resolution multiplier
RENDER_SCALE = 4  # supersampling for raster output


def load_source() -> Image.Image:
    if SOURCE_CACHE.exists():
        raw = SOURCE_CACHE.read_bytes()
    else:
        with urllib.request.urlopen(SOURCE_URL) as resp:  # noqa: S310
            raw = resp.read()
        SOURCE_CACHE.write_bytes(raw)
    img = Image.open(io.BytesIO(raw)).convert("RGBA")
    flat = Image.new("RGBA", img.size, (255, 255, 255, 255))
    flat.alpha_composite(img)
    return flat.convert("RGB")


def ink_mask(img: Image.Image) -> np.ndarray:
    """Coverage (0..255) of non-white pixels: blue strokes and black text alike."""
    rgb = np.asarray(img).astype(np.float32)
    return 255.0 - rgb.min(axis=2)


def trace_polygons(mask: np.ndarray, scale: float) -> list[np.ndarray]:
    """Vectorise the artwork into closed polygons, in SVG viewBox coordinates."""
    big = cv2.resize(
        mask, None, fx=TRACE_SCALE, fy=TRACE_SCALE, interpolation=cv2.INTER_CUBIC
    )
    binary = (big > 127.5).astype(np.uint8) * 255
    contours, _ = cv2.findContours(binary, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    polys: list[np.ndarray] = []
    for contour in contours:
        if cv2.contourArea(contour) < 12 * TRACE_SCALE**2:
            continue
        approx = cv2.approxPolyDP(contour, 1.2, True).reshape(-1, 2).astype(np.float64)
        if len(approx) >= 3:
            polys.append(approx * scale)
    return polys


def build_svg(polys: list[np.ndarray]) -> str:
    parts: list[str] = []
    for pts in polys:
        head = f"M{pts[0, 0]:.2f} {pts[0, 1]:.2f}"
        body = "L" + "L".join(f"{x:.2f} {y:.2f}" for x, y in pts[1:])
        parts.append(f"{head}{body}Z")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}" '
        f'width="{SIZE}" height="{SIZE}">\n'
        f"  <title>CAME Thermostat</title>\n"
        f'  <rect width="{SIZE}" height="{SIZE}" rx="{RADIUS}" ry="{RADIUS}" '
        f'fill="{HA_BLUE}"/>\n'
        f'  <path fill="#ffffff" fill-rule="evenodd" d="{"".join(parts)}"/>\n'
        "</svg>\n"
    )


def artwork_mask(polys: list[np.ndarray], size: int) -> Image.Image:
    """Rasterise the polygons (even-odd fill) at size x size."""
    w = size * RENDER_SCALE
    k = w / SIZE
    canvas = np.zeros((w, w), np.uint8)
    for pts in polys:
        layer = np.zeros((w, w), np.uint8)
        cv2.fillPoly(
            layer, [np.round(pts * k).astype(np.int32)], 255, lineType=cv2.LINE_AA
        )
        canvas = cv2.bitwise_xor(canvas, layer)  # even-odd: islands punch holes
    mask = Image.fromarray(canvas, "L").resize((size, size), Image.LANCZOS)
    return mask


def shape_mask(size: int) -> Image.Image:
    w = size * RENDER_SCALE
    layer = Image.new("L", (w, w), 0)
    ImageDraw.Draw(layer).rounded_rectangle(
        (0, 0, w - 1, w - 1), radius=RADIUS * w / SIZE, fill=255
    )
    return layer.resize((size, size), Image.LANCZOS)


def build_png(polys: list[np.ndarray], out: Path, size: int) -> None:
    ink = artwork_mask(polys, size)
    canvas = Image.composite(
        Image.new("RGB", (size, size), "#ffffff"),
        Image.new("RGB", (size, size), HA_BLUE),
        ink,
    ).convert("RGBA")
    canvas.putalpha(shape_mask(size))
    canvas.save(out, optimize=True)


def main() -> None:
    src = load_source()
    polys = trace_polygons(ink_mask(src), scale=SIZE / (src.width * TRACE_SCALE))
    ASSETS.mkdir(exist_ok=True)
    (ASSETS / "icon.svg").write_text(build_svg(polys), encoding="utf-8")
    build_png(polys, ASSETS / "icon.png", 1024)
    build_png(polys, ASSETS / "icon@2x.png", 512)

    # Home Assistant picks these up for the integration card / device icon.
    brand = ROOT / "custom_components" / "came_thermo" / "brand"
    brand.mkdir(parents=True, exist_ok=True)
    build_png(polys, brand / "icon.png", 256)
    build_png(polys, brand / "icon@2x.png", 512)
    print(f"wrote {ASSETS / 'icon.svg'} ({len(polys)} polygons)")


if __name__ == "__main__":
    main()
