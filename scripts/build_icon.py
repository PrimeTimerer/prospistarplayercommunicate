#!/usr/bin/env python3
"""Build the web mark and multi-resolution Windows icon from one RGBA source."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 192, 256)


def _meaningful_bbox(image: Image.Image) -> tuple[int, int, int, int]:
    alpha = image.getchannel("A")
    mask = alpha.point(lambda value: 255 if value >= 18 else 0)
    pixels = mask.load()
    row_floor = max(8, image.width // 80)
    column_floor = max(8, image.height // 80)
    rows = [sum(bool(pixels[x, y]) for x in range(image.width)) for y in range(image.height)]
    columns = [sum(bool(pixels[x, y]) for y in range(image.height)) for x in range(image.width)]
    valid_rows = [index for index, count in enumerate(rows) if count >= row_floor]
    valid_columns = [index for index, count in enumerate(columns) if count >= column_floor]
    if not valid_rows or not valid_columns:
        return mask.getbbox() or (0, 0, image.width, image.height)
    return (
        valid_columns[0],
        valid_rows[0],
        valid_columns[-1] + 1,
        valid_rows[-1] + 1,
    )


def _square_crop(image: Image.Image) -> Image.Image:
    left, top, right, bottom = _meaningful_bbox(image)
    width = right - left
    height = bottom - top
    side = max(width, height)
    padding = max(12, round(side * 0.035))
    side += padding * 2
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    piece = image.crop((left, top, right, bottom))
    destination = (
        round(side / 2 - width / 2),
        round(side / 2 - height / 2),
    )
    canvas.alpha_composite(piece, destination)
    return canvas


def build(source: Path, web_output: Path, ico_output: Path) -> None:
    image = Image.open(source).convert("RGBA")
    icon = _square_crop(image).resize((512, 512), Image.Resampling.LANCZOS)
    web_output.parent.mkdir(parents=True, exist_ok=True)
    ico_output.parent.mkdir(parents=True, exist_ok=True)
    icon.save(web_output, "PNG", optimize=True)
    icon.save(ico_output, "ICO", sizes=[(size, size) for size in ICON_SIZES])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("web_output", type=Path)
    parser.add_argument("ico_output", type=Path)
    args = parser.parse_args()
    build(args.source, args.web_output, args.ico_output)


if __name__ == "__main__":
    main()
