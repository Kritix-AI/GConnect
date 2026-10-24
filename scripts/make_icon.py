"""Generates the GConnect app icon (GPU chip with a 'G' core).

Outputs a 1024x1024 master PNG used by `npx tauri icon` to derive the
full icon set (ico/icns/png sizes).
"""

from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = REPO_ROOT / "src-tauri" / "app-icon.png"

SIZE = 1024
BG = (13, 17, 23, 255)          # #0d1117 GitHub dark
OUTLINE = (88, 166, 255, 255)   # #58a6ff blue
PIN = (56, 139, 253, 255)       # #388fd
CORE_BG = (13, 17, 23, 255)
GREEN = (63, 185, 80, 255)      # #3fb950


def rounded_rect(draw, box, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def draw_g(draw, cx, cy, r, color, width):
    """A blocky terminal-style 'G'."""
    x0, y0 = cx - r, cy - r
    x1, y1 = cx + r, cy + r
    # top arc + left + bottom (open on the right top)
    draw.arc([x0, y0, x1, y1], start=25, end=335, fill=color, width=width)
    # right vertical stub into center
    draw.line([(cx + r, cy), (cx + r * 0.15, cy)], fill=color, width=width)
    draw.line([(cx + r, cy), (cx + r, cy - r * 0.55)], fill=color, width=width)


def main():
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    margin = 96
    chip = [margin, margin, SIZE - margin, SIZE - margin]

    # Chip body
    rounded_rect(d, chip, radius=110, fill=BG, outline=OUTLINE, width=18)

    # Circuit pins (top/bottom/left/right)
    pin_len = 46
    pin_w = 18
    positions = [0.28, 0.5, 0.72]
    for t in positions:
        x = margin + int((SIZE - 2 * margin) * t)
        # top / bottom
        d.rounded_rectangle(
            [x - pin_w // 2, margin - pin_len, x + pin_w // 2, margin + 6],
            radius=6, fill=PIN,
        )
        d.rounded_rectangle(
            [x - pin_w // 2, SIZE - margin - 6, x + pin_w // 2, SIZE - margin + pin_len],
            radius=6, fill=PIN,
        )
        y = margin + int((SIZE - 2 * margin) * t)
        d.rounded_rectangle(
            [margin - pin_len, y - pin_w // 2, margin + 6, y + pin_w // 2],
            radius=6, fill=PIN,
        )
        d.rounded_rectangle(
            [SIZE - margin - 6, y - pin_w // 2, SIZE - margin + pin_len, y + pin_w // 2],
            radius=6, fill=PIN,
        )

    # Inner core plate
    inner = [margin + 150, margin + 150, SIZE - margin - 150, SIZE - margin - 150]
    rounded_rect(d, inner, radius=64, fill=CORE_BG, outline=(46, 160, 67, 255), width=12)

    # The G
    draw_g(d, cx=SIZE // 2, cy=SIZE // 2, r=170, color=GREEN, width=54)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT)
    print(f"Icon written to {OUT}")


if __name__ == "__main__":
    main()
