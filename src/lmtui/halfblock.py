# ------ Imports ------
from pathlib import Path

from PIL import Image, ImageOps
from rich.style import Style
from rich.text import Text


# ------ Constants ------
# Upper-half-block character. Foreground paints the top pixel of the
# cell, background paints the bottom pixel. Two pixels per cell.
_UPPER_HALF = "\u2580"


# ------ Public API ------

def image_to_halfblocks(
    path: Path,
    width_cells: int = 40,
    max_height_cells: int | None = None,
) -> Text:
    """
    Load a PNG and return a rich.Text where each character is a
    half-block encoding two vertical pixels of the source image.

    Aspect ratio is preserved under the assumption that terminal cells
    are roughly twice as tall as they are wide. If `max_height_cells`
    is given, the render is clamped to that many text rows.

    `ImageOps.autocontrast` is applied before downscaling so dark,
    low-contrast album covers don't collapse to a single flat tone.
    """
    img = Image.open(path).convert("RGB")

    # Stretch the histogram so dark covers keep visible structure.
    img = ImageOps.autocontrast(img, cutoff=1)

    aspect = img.height / img.width
    height_cells = max(1, round(aspect * width_cells / 2))

    if max_height_cells is not None and height_cells > max_height_cells:
        height_cells = max_height_cells
        width_cells = max(1, round(width_cells * height_cells / max(1, round(aspect * width_cells / 2))))

    pixel_height = height_cells * 2
    img = img.resize((width_cells, pixel_height), Image.LANCZOS)

    text = Text(no_wrap=True)
    for y in range(0, pixel_height, 2):
        for x in range(width_cells):
            top = img.getpixel((x, y))
            bottom = (
                img.getpixel((x, y + 1))
                if y + 1 < pixel_height
                else (0, 0, 0)
            )
            fg = f"#{top[0]:02x}{top[1]:02x}{top[2]:02x}"
            bg = f"#{bottom[0]:02x}{bottom[1]:02x}{bottom[2]:02x}"
            text.append(_UPPER_HALF, style=Style(color=fg, bgcolor=bg))
        text.append("\n")

    return text
