"""Generate PWA icons (192, 512, maskable) with Pillow."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "frontend" / "public" / "icons"


def draw_icon(size: int, maskable: bool = False) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    inset = int(size * 0.18) if maskable else 0
    radius = int(size * 0.14)
    draw.rounded_rectangle(
        [inset, inset, size - inset, size - inset],
        radius=radius if not maskable else int(size * 0.5),
        fill=(18, 50, 31, 255),
    )
    scale = size / 64.0
    # Leaf: two quadratic-ish curves built from a polygon.
    def p(x: float, y: float) -> tuple[float, float]:
        return (x * scale, y * scale)

    draw.line([p(18, 44), p(22, 36), p(30, 27), p(40, 22), p(46, 20)], fill=(224, 163, 62, 255), width=max(2, int(3.5 * scale)), joint="curve")
    draw.line([p(18, 46), p(24, 38), p(32, 31), p(40, 26)], fill=(63, 168, 143, 255), width=max(2, int(3 * scale)), joint="curve")
    draw.ellipse([p(43.4, 43.4)[0], p(43.4, 43.4)[1], p(48.6, 48.6)[0], p(48.6, 48.6)[1]], fill=(224, 163, 62, 255))
    return image


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size in (192, 512):
        draw_icon(size).save(OUT / f"icon-{size}.png", format="PNG", optimize=True)
        print("wrote", OUT / f"icon-{size}.png")
    draw_icon(512, maskable=True).save(OUT / "icon-maskable-512.png", format="PNG", optimize=True)
    print("wrote", OUT / "icon-maskable-512.png")


if __name__ == "__main__":
    main()
