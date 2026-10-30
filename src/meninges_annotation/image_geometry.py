from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from .calibration import Calibration


def detect_white_borders(path: Path | str) -> tuple[int, int, int, int]:
    """Return (left, top, right, bottom) all-white edge strips in pixels."""

    with Image.open(path) as image:
        gray = image.convert("L")
        width, height = gray.size
        data = gray.tobytes()

    def row_is_white(y: int) -> bool:
        start = y * width
        return data[start : start + width] == b"\xff" * width

    def column_is_white(x: int) -> bool:
        return all(data[y * width + x] == 255 for y in range(height))

    left = 0
    while left < width and column_is_white(left):
        left += 1
    right = 0
    while right < width - left and column_is_white(width - 1 - right):
        right += 1
    top = 0
    while top < height and row_is_white(top):
        top += 1
    bottom = 0
    while bottom < height - top and row_is_white(height - 1 - bottom):
        bottom += 1
    return left, top, right, bottom


def content_bounds(
    manifest_row: dict[str, str],
) -> tuple[int, int, int, int, int, int]:
    """Return (left, top, right, bottom, content_width, content_height)."""

    width = int(manifest_row["width_px"])
    height = int(manifest_row["height_px"])
    left = int(manifest_row.get("crop_left_px", "0") or 0)
    top = int(manifest_row.get("crop_top_px", "0") or 0)
    right = int(manifest_row.get("crop_right_px", "0") or 0)
    bottom = int(manifest_row.get("crop_bottom_px", "0") or 0)
    content_width = width - left - right
    content_height = height - top - bottom
    if content_width <= 0 or content_height <= 0:
        raise ValueError(
            f"{manifest_row.get('image_id', '?')}: "
            f"content area must be positive; got {content_width}×{content_height}"
        )
    return left, top, right, bottom, content_width, content_height


def calibration_for_content(
    base: Calibration,
    source_width: float,
    source_height: float,
    content_width: float,
    content_height: float,
) -> Calibration:
    """Scale a source-space calibration to a resized content canvas."""

    x_scale = source_width / content_width
    y_scale = source_height / content_height
    return Calibration(
        calibration_id=base.calibration_id,
        x_um_per_px=base.x_um_per_px * x_scale,
        y_um_per_px=base.y_um_per_px * y_scale,
        display_scale=base.display_scale,
        display_x_um_per_px=base.display_x_um_per_px * x_scale,
        display_y_um_per_px=base.display_y_um_per_px * y_scale,
    )


def source_dimensions(config: dict[str, Any]) -> tuple[float, float]:
    source = config["source_images"]
    return float(source["expected_width_px"]), float(source["expected_height_px"])
