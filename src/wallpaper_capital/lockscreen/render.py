"""Drawing the capital/country tag onto a wallpaper."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from wallpaper_capital.imaging import fit, save_jpeg
from wallpaper_capital.lockscreen.labels import Labels

log = logging.getLogger(__name__)

TARGET_SIZE = (2560, 1440)

# The main display is 16:10, so Windows covers it with the 16:9 image and crops
# roughly 5 % of the width off each side. A tag flush with the edge gets cut.
DEFAULT_MARGIN_X = 0.07
DEFAULT_MARGIN_Y = 0.06

PILL_FILL = (0, 0, 0, 115)
TEXT_PRIMARY = (255, 255, 255, 255)
TEXT_SECONDARY = (255, 255, 255, 190)

FONTS_DIR = Path(os.environ.get("WINDIR") or r"C:\Windows") / "Fonts"
PRIMARY_FONTS = ("seguisb.ttf", "segoeuib.ttf", "arialbd.ttf", "calibrib.ttf")
SECONDARY_FONTS = ("segoeui.ttf", "arial.ttf", "calibri.ttf")

AnyFont = ImageFont.FreeTypeFont | ImageFont.ImageFont
Rgba = tuple[int, int, int, int]


@dataclass(frozen=True)
class TextRow:
    """One measured line of the tag, ready to be drawn."""

    text: str
    font: AnyFont
    color: Rgba
    offset: tuple[int, int]
    size: tuple[int, int]


def load_font(candidates: tuple[str, ...], size: int) -> AnyFont:
    """First available Windows font from `candidates`, or Pillow's built-in one."""
    for name in candidates:
        path = FONTS_DIR / name
        if not path.is_file():
            continue
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            continue
    log.warning("No TrueType font found in %s, falling back to the default", FONTS_DIR)
    return ImageFont.load_default()


def measure(
    draw: ImageDraw.ImageDraw, rows: list[tuple[str, AnyFont, Rgba]]
) -> list[TextRow]:
    """Measure each line.

    `textbbox` returns bounds relative to the anchor; keeping the offsets lets the
    glyphs be placed to the pixel (`textsize` was removed in Pillow 10).
    """
    measured: list[TextRow] = []
    for text, font, color in rows:
        bounds = draw.textbbox((0, 0), text, font=font)
        left, top, right, bottom = (round(bound) for bound in bounds)
        measured.append(
            TextRow(
                text=text,
                font=font,
                color=color,
                offset=(left, top),
                size=(right - left, bottom - top),
            )
        )
    return measured


def draw_tag(
    image: Image.Image,
    labels: Labels,
    margin_x: float = DEFAULT_MARGIN_X,
    margin_y: float = DEFAULT_MARGIN_Y,
) -> Image.Image:
    """Overlay a translucent pill carrying the labels, bottom right."""
    width, height = image.size
    primary_text, secondary_text = labels.lines()

    size_primary = max(14, round(height * 0.030))
    size_secondary = max(11, round(height * 0.019))

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    rows: list[tuple[str, AnyFont, Rgba]] = [
        (primary_text, load_font(PRIMARY_FONTS, size_primary), TEXT_PRIMARY)
    ]
    if secondary_text:
        rows.append((secondary_text, load_font(SECONDARY_FONTS, size_secondary), TEXT_SECONDARY))
    measured = measure(draw, rows)

    gap = round(size_secondary * 0.35)
    block_width = max(row.size[0] for row in measured)
    block_height = sum(row.size[1] for row in measured) + gap * (len(measured) - 1)

    padding_x = round(size_primary * 0.75)
    padding_y = round(size_primary * 0.50)

    pill_right = width - round(width * margin_x)
    pill_bottom = height - round(height * margin_y)
    pill_left = max(0, pill_right - (block_width + 2 * padding_x))
    pill_top = max(0, pill_bottom - (block_height + 2 * padding_y))

    pill_height = pill_bottom - pill_top
    draw.rounded_rectangle(
        (pill_left, pill_top, pill_right, pill_bottom),
        radius=min(round(pill_height * 0.28), pill_height // 2),
        fill=PILL_FILL,
    )

    cursor_y = pill_top + padding_y
    for row in measured:
        text_width, text_height = row.size
        offset_x, offset_y = row.offset
        draw.text(
            (pill_right - padding_x - text_width - offset_x, cursor_y - offset_y),
            row.text,
            font=row.font,
            fill=row.color,
        )
        cursor_y += text_height + gap

    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


def render(
    source: Path,
    labels: Labels,
    output: Path,
    margin_x: float = DEFAULT_MARGIN_X,
    margin_y: float = DEFAULT_MARGIN_Y,
) -> None:
    """Crop to 16:9, draw the tag, and write the JPEG atomically."""
    with Image.open(source) as raw:
        fitted = fit(raw, TARGET_SIZE)
    save_jpeg(draw_tag(fitted, labels, margin_x, margin_y), output)
    log.debug("Image written: %s", output)
