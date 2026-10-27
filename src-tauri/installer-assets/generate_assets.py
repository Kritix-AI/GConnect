"""Generate GConnect's NSIS artwork at its required installer dimensions."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
ICONS = ROOT.parent / "icons"
SCALE = 3
GREEN = (33, 205, 112)
GREEN_DARK = (12, 74, 43)
INK = (14, 43, 29)
WHITE = (255, 255, 255)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "segoeuib.ttf" if bold else "segoeui.ttf"
    return ImageFont.truetype(str(Path("C:/Windows/Fonts") / name), size)


def save_bmp(image: Image.Image, name: str, size: tuple[int, int]) -> None:
    image = image.resize((size[0] * SCALE, size[1] * SCALE), Image.Resampling.LANCZOS)
    image = image.resize(size, Image.Resampling.LANCZOS).convert("RGB")
    image.save(ROOT / name, format="BMP")


def draw_chip(draw: ImageDraw.ImageDraw, cx: int, cy: int, radius: int) -> None:
    # Fine white chip traces around a soft green GPU tile.
    left, top, right, bottom = cx - radius, cy - radius, cx + radius, cy + radius
    draw.rounded_rectangle((left, top, right, bottom), radius=radius // 4,
                           fill=GREEN_DARK, outline=(58, 167, 104), width=2 * SCALE)
    pad = radius // 5
    draw.rounded_rectangle((left + pad, top + pad, right - pad, bottom - pad),
                           radius=radius // 6, fill=INK, outline=GREEN, width=2 * SCALE)
    pin = max(5, radius // 5)
    for offset in (-radius // 2, 0, radius // 2):
        for x in (cx - radius - pin, cx + radius):
            draw.rounded_rectangle((x, cy + offset - pin, x + pin, cy + offset + pin),
                                   radius=pin // 2, fill=GREEN)
        for y in (cy - radius - pin, cy + radius):
            draw.rounded_rectangle((cx + offset - pin, y, cx + offset + pin, y + pin),
                                   radius=pin // 2, fill=GREEN)

    # G shaped like a circuit trace, echoing the existing GConnect mark.
    ring_r = radius * 0.42
    box = (cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r)
    draw.arc(box, 36, 324, fill=GREEN, width=max(5, radius // 5))
    bar_h = max(5, radius // 4)
    bar_y = cy - bar_h // 2
    draw.rounded_rectangle((cx + radius // 10, bar_y, cx + radius // 2, bar_y + bar_h),
                           radius=bar_h // 3, fill=GREEN)
    draw.ellipse((cx + radius // 3, bar_y - bar_h // 3,
                  cx + radius // 3 + bar_h * 2 // 3, bar_y + bar_h * 2 // 3), fill=WHITE)


def create_sidebar() -> None:
    width, height = 164 * SCALE, 314 * SCALE
    image = Image.new("RGB", (width, height), INK)
    pixels = image.load()
    for y in range(height):
        blend = y / max(1, height - 1)
        color = tuple(round(GREEN_DARK[i] * (1 - blend) + INK[i] * blend) for i in range(3))
        for x in range(width):
            pixels[x, y] = color

    draw = ImageDraw.Draw(image, "RGBA")
    # Subtle binary columns give the background its GPU / distributed-compute texture.
    columns = ["0100110", "1011010", "0110101", "1100101"]
    for col, digits in enumerate(columns):
        x = (12 + col * 45) * SCALE
        for row, digit in enumerate(digits):
            y = (14 + row * 20) * SCALE
            alpha = 34 if (row + col) % 3 else 58
            draw.text((x, y), digit, font=font(9 * SCALE, True), fill=(*GREEN, alpha))

    # Thin framing and a small status-like accent at the top.
    draw.line((17 * SCALE, 18 * SCALE, 147 * SCALE, 18 * SCALE), fill=(*GREEN, 150), width=1 * SCALE)
    draw.ellipse((17 * SCALE, 15 * SCALE, 21 * SCALE, 19 * SCALE), fill=GREEN)
    draw.text((27 * SCALE, 10 * SCALE), "GCONNECT / GPU", font=font(6 * SCALE, True), fill=(*WHITE, 220))

    draw_chip(draw, 82 * SCALE, 119 * SCALE, 43 * SCALE)
    draw.text((16 * SCALE, 190 * SCALE), "GConnect", font=font(17 * SCALE, True), fill=WHITE)
    draw.text((17 * SCALE, 215 * SCALE), "GPU COMPUTE NETWORK", font=font(6 * SCALE, True), fill=GREEN)
    draw.line((17 * SCALE, 239 * SCALE, 147 * SCALE, 239 * SCALE), fill=(*GREEN, 120), width=1 * SCALE)
    draw.text((17 * SCALE, 251 * SCALE), "Share. Connect. Compute.", font=font(7 * SCALE), fill=(*WHITE, 230))
    draw.text((17 * SCALE, 273 * SCALE), "DISTRIBUTED GPU POWER", font=font(5 * SCALE, True), fill=(*GREEN, 230))
    save_bmp(image, "gconnect-sidebar.bmp", (164, 314))


def create_header() -> None:
    width, height = 150 * SCALE, 57 * SCALE
    image = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 5 * SCALE, height), fill=GREEN)

    # Small white-on-green G chip mark.
    cx, cy, radius = 24 * SCALE, 28 * SCALE, 17 * SCALE
    draw.rounded_rectangle((cx - radius, cy - radius, cx + radius, cy + radius),
                           radius=6 * SCALE, fill=GREEN_DARK)
    draw.arc((cx - 9 * SCALE, cy - 9 * SCALE, cx + 9 * SCALE, cy + 9 * SCALE),
             35, 325, fill=WHITE, width=4 * SCALE)
    draw.rectangle((cx + 1 * SCALE, cy - 2 * SCALE, cx + 10 * SCALE, cy + 2 * SCALE), fill=WHITE)
    draw.ellipse((cx + 7 * SCALE, cy - 3 * SCALE, cx + 11 * SCALE, cy + 1 * SCALE), fill=GREEN)

    draw.text((49 * SCALE, 12 * SCALE), "GConnect", font=font(11 * SCALE, True), fill=INK)
    draw.text((50 * SCALE, 30 * SCALE), "GPU COMPUTE NETWORK", font=font(5 * SCALE, True), fill=GREEN_DARK)
    draw.line((50 * SCALE, 41 * SCALE, 138 * SCALE, 41 * SCALE), fill=(194, 235, 211), width=1 * SCALE)
    draw.text((50 * SCALE, 44 * SCALE), "SHARE  /  CONNECT  /  COMPUTE", font=font(4 * SCALE, True), fill=(83, 115, 96))
    save_bmp(image, "gconnect-header.bmp", (150, 57))


def create_installer_icon() -> None:
    size = 256
    image = Image.new("RGBA", (size, size), (*GREEN_DARK, 255))
    draw = ImageDraw.Draw(image)
    margin = 24
    draw.rounded_rectangle((margin, margin, size - margin, size - margin), radius=48,
                           fill=INK, outline=GREEN, width=10)
    draw.arc((67, 67, 189, 189), 35, 325, fill=GREEN, width=24)
    draw.rounded_rectangle((125, 111, 187, 145), radius=8, fill=GREEN)
    draw.ellipse((167, 105, 186, 124), fill=WHITE)
    image.save(ICONS / "gconnect-installer.ico", format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)])


if __name__ == "__main__":
    create_sidebar()
    create_header()
    create_installer_icon()
