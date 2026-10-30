from __future__ import annotations

import base64
import json
from html import escape
from math import hypot
from pathlib import Path
from typing import Any, Callable

from .calibration import Calibration, nice_scale_length
from .image_geometry import (
    calibration_for_content,
    content_bounds,
    source_dimensions,
)
from .project import (
    artifact_stem,
    load_annotation,
    load_config,
    manifest_by_id,
    sync_raw_images,
)


Box = tuple[float, float, float, float]
PointMapper = Callable[[float, float], tuple[float, float]]


def square_crop_bounds(
    roi: dict[str, Any], image_width: float, image_height: float
) -> tuple[float, float, float, float]:
    x = float(roi["x_px"])
    y = float(roi["y_px"])
    width = float(roi["width_px"])
    height = float(roi["height_px"])
    side = max(width, height)
    if side > min(image_width, image_height):
        raise ValueError("ROI cannot be expanded to a square inside the source image")

    center_x = x + width / 2.0
    center_y = y + height / 2.0
    crop_x = max(0.0, min(center_x - side / 2.0, image_width - side))
    crop_y = max(0.0, min(center_y - side / 2.0, image_height - side))
    return crop_x, crop_y, side, side


def _image_data_uri(
    path: Path,
    crop: tuple[int, int, int, int] | None = None,
) -> str:
    if crop is None or crop == (0, 0, 0, 0):
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    else:
        from io import BytesIO

        from PIL import Image

        left, top, right, bottom = crop
        with Image.open(path) as image:
            width, height = image.size
            image = image.crop(
                (left, top, width - right, height - bottom)
            )
            buffer = BytesIO()
            image.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _fmt(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _adaptive_arrow_head_length(
    line_length: float,
    stroke_width: float,
    configured_length: float,
) -> float:
    """Scale down heads on short measurements so the two heads cannot collide."""

    preferred = max(stroke_width * 1.8, line_length * 0.26)
    return min(configured_length, preferred, line_length * 0.36)


def _polygon_points(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in points)


def _text(
    x: float,
    y: float,
    value: str,
    css_class: str = "label",
    anchor: str = "middle",
    transform: str | None = None,
    attributes: str = "",
) -> str:
    transform_attr = f' transform="{escape(transform)}"' if transform else ""
    extra = f" {attributes.strip()}" if attributes.strip() else ""
    return (
        f'<text class="{css_class}" x="{_fmt(x)}" y="{_fmt(y)}" '
        f'text-anchor="{anchor}"{transform_attr}{extra}>{escape(value)}</text>'
    )


def _boxes_overlap(a: Box, b: Box, padding: float = 0.0) -> bool:
    return not (
        a[2] + padding <= b[0]
        or b[2] + padding <= a[0]
        or a[3] + padding <= b[1]
        or b[3] + padding <= a[1]
    )


def _box_inside(inner: Box, outer: Box, margin: float = 0.0) -> bool:
    return (
        inner[0] >= outer[0] + margin
        and inner[1] >= outer[1] + margin
        and inner[2] <= outer[2] - margin
        and inner[3] <= outer[3] - margin
    )


def _label_box(
    center_x: float,
    baseline_y: float,
    text_width: float,
    font_size: float,
) -> Box:
    return (
        center_x - text_width / 2.0,
        baseline_y - font_size * 0.86,
        center_x + text_width / 2.0,
        baseline_y + font_size * 0.24,
    )


def _line_obstacle(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    padding: float,
) -> Box:
    return (
        min(x1, x2) - padding,
        min(y1, y2) - padding,
        max(x1, x2) + padding,
        max(y1, y2) + padding,
    )


def _measurement_svg(
    measurements: list[dict[str, Any]],
    map_point: PointMapper,
    *,
    decimals: int,
    font_size: float,
    stroke_width: float,
    arrow_head: float,
    panel_bounds: Box,
    reserved_boxes: list[Box] | None = None,
    clip_id: str | None = None,
) -> tuple[str, str]:
    prepared: list[dict[str, Any]] = []
    line_padding = max(arrow_head * 0.8, stroke_width * 2.5)
    for measurement in measurements:
        x1, y1 = map_point(*measurement["p1_px"])
        x2, y2 = map_point(*measurement["p2_px"])
        value = float(measurement["calculated"]["distance_um"])
        label = f"{value:.{decimals}f} µm"
        prepared.append(
            {
                "measurement": measurement,
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "label": label,
                "line_box": _line_obstacle(
                    x1, y1, x2, y2, line_padding
                ),
            }
        )

    line_boxes = [item["line_box"] for item in prepared]
    occupied = list(reserved_boxes or [])
    arrow_output: list[str] = []
    label_output: list[str] = []
    clip_attr = f' clip-path="url(#{clip_id})"' if clip_id else ""

    for index, item in enumerate(prepared):
        x1 = float(item["x1"])
        y1 = float(item["y1"])
        x2 = float(item["x2"])
        y2 = float(item["y2"])
        dx = x2 - x1
        dy = y2 - y1
        length = hypot(dx, dy)
        if length == 0:
            continue

        tangent_x = dx / length
        tangent_y = dy / length
        normal_x = -tangent_y
        normal_y = tangent_x
        # Explicit polygons allow each short measurement to use smaller
        # heads. The tips remain on the scientific endpoints while the
        # visible shaft starts at the triangular bases.
        head_length = _adaptive_arrow_head_length(
            length, stroke_width, arrow_head
        )
        head_half_width = head_length * 0.44
        shaft_inset = head_length
        shaft_x1 = x1 + tangent_x * shaft_inset
        shaft_y1 = y1 + tangent_y * shaft_inset
        shaft_x2 = x2 - tangent_x * shaft_inset
        shaft_y2 = y2 - tangent_y * shaft_inset
        start_base_x = x1 + tangent_x * head_length
        start_base_y = y1 + tangent_y * head_length
        end_base_x = x2 - tangent_x * head_length
        end_base_y = y2 - tangent_y * head_length
        start_head = _polygon_points(
            [
                (x1, y1),
                (
                    start_base_x + normal_x * head_half_width,
                    start_base_y + normal_y * head_half_width,
                ),
                (
                    start_base_x - normal_x * head_half_width,
                    start_base_y - normal_y * head_half_width,
                ),
            ]
        )
        end_head = _polygon_points(
            [
                (x2, y2),
                (
                    end_base_x + normal_x * head_half_width,
                    end_base_y + normal_y * head_half_width,
                ),
                (
                    end_base_x - normal_x * head_half_width,
                    end_base_y - normal_y * head_half_width,
                ),
            ]
        )
        midpoint_x = (x1 + x2) / 2.0
        midpoint_y = (y1 + y2) / 2.0
        text_width = max(
            font_size * 2.2,
            len(str(item["label"])) * font_size * 0.57,
        )
        normal_clearance = (
            abs(normal_x) * text_width / 2.0
            + abs(normal_y) * font_size * 0.55
            + line_padding
            + font_size * 0.18
        )

        side_order = (1.0, -1.0) if index % 2 == 0 else (-1.0, 1.0)
        tangent_shifts = (
            0.0,
            font_size * 0.95,
            -font_size * 0.95,
            font_size * 1.9,
            -font_size * 1.9,
        )
        candidates: list[tuple[float, float, Box]] = []
        for lane in range(6):
            distance = normal_clearance + lane * font_size * 0.9
            for side in side_order:
                for tangent_shift in tangent_shifts:
                    center_x = (
                        midpoint_x
                        + normal_x * distance * side
                        + tangent_x * tangent_shift
                    )
                    center_y = (
                        midpoint_y
                        + normal_y * distance * side
                        + tangent_y * tangent_shift
                    )
                    baseline_y = center_y + font_size * 0.31
                    box = _label_box(
                        center_x, baseline_y, text_width, font_size
                    )
                    candidates.append((center_x, baseline_y, box))

        selected: tuple[float, float, Box] | None = None
        margin = font_size * 0.22
        for candidate in candidates:
            box = candidate[2]
            if not _box_inside(box, panel_bounds, margin):
                continue
            if any(_boxes_overlap(box, other, font_size * 0.08) for other in occupied):
                continue
            if any(
                _boxes_overlap(box, obstacle, font_size * 0.04)
                for obstacle in line_boxes
            ):
                continue
            selected = candidate
            break

        if selected is None:
            # Use the least-conflicting candidate and clamp it inside the panel.
            def conflict_score(candidate: tuple[float, float, Box]) -> tuple[int, int]:
                box = candidate[2]
                outside = 0 if _box_inside(box, panel_bounds, margin) else 1
                overlaps = sum(
                    _boxes_overlap(box, other, font_size * 0.04)
                    for other in (*occupied, *line_boxes)
                )
                return outside, overlaps

            center_x, baseline_y, box = min(candidates, key=conflict_score)
            half_width = text_width / 2.0
            center_x = max(
                panel_bounds[0] + margin + half_width,
                min(
                    center_x,
                    panel_bounds[2] - margin - half_width,
                ),
            )
            baseline_y = max(
                panel_bounds[1] + margin + font_size * 0.86,
                min(
                    baseline_y,
                    panel_bounds[3] - margin - font_size * 0.24,
                ),
            )
            box = _label_box(center_x, baseline_y, text_width, font_size)
            selected = (center_x, baseline_y, box)

        label_x, label_y, label_box = selected
        occupied.append(label_box)
        measurement = item["measurement"]
        shared_foot = measurement.get("kind") == "shared_foot_distance"
        end_head_output = (
            ""
            if shared_foot
            else (
                f'<polygon class="measurement-head-outline" points="{end_head}"/>'
                f'<polygon class="measurement-head" points="{end_head}"/>'
            )
        )
        if shared_foot:
            shaft_x2 = x2
            shaft_y2 = y2
        label_svg = _text(
            label_x,
            label_y,
            str(item["label"]),
            attributes='data-placement="auto-avoid"',
        )
        measurement_id = escape(measurement["id"])
        arrow_output.append(
            f'<g id="{measurement_id}" data-shaft-inset="'
            f'{_fmt(shaft_inset)}" data-head-length="{_fmt(head_length)}" '
            f'data-arrow-direction="'
            f'{"point-to-foot" if shared_foot else "outward"}"{clip_attr}>'
            f'<line class="measurement-outline" '
            f'x1="{_fmt(shaft_x1)}" y1="{_fmt(shaft_y1)}" '
            f'x2="{_fmt(shaft_x2)}" y2="{_fmt(shaft_y2)}"/>'
            f'<line class="measurement" '
            f'x1="{_fmt(shaft_x1)}" y1="{_fmt(shaft_y1)}" '
            f'x2="{_fmt(shaft_x2)}" y2="{_fmt(shaft_y2)}"/>'
            f'<polygon class="measurement-head-outline" points="{start_head}"/>'
            f'<polygon class="measurement-head" points="{start_head}"/>'
            f"{end_head_output}"
            "</g>"
        )
        label_output.append(
            f'<g id="{measurement_id}_label"{clip_attr}>'
            f"{label_svg}"
            "</g>"
        )

    return "\n".join(arrow_output), "\n".join(label_output)


def _refractive_index_groups_svg(
    groups: list[dict[str, Any]],
    measurements: list[dict[str, Any]],
    map_point: PointMapper,
    *,
    decimals: int,
    font_size: float,
    stroke_width: float,
    panel_bounds: Box,
    clip_id: str | None = None,
) -> tuple[str, str]:
    measurements_by_id = {
        str(measurement["id"]): measurement for measurement in measurements
    }
    markers: list[str] = []
    labels: list[str] = []
    clip_attr = f' clip-path="url(#{clip_id})"' if clip_id else ""
    for group in groups:
        measurement_ids = group.get("measurement_ids", [])
        if len(measurement_ids) != 2 or any(
            measurement_id not in measurements_by_id
            for measurement_id in measurement_ids
        ):
            continue
        members = [measurements_by_id[measurement_id] for measurement_id in measurement_ids]
        point_positions = [map_point(*member["p1_px"]) for member in members]
        foot_x, foot_y = map_point(*group["foot_px"])
        group_id = escape(str(group["id"]))
        radius = max(stroke_width * 1.35, font_size * 0.13)
        markers.append(
            f'<g id="{group_id}_foot"{clip_attr}>'
            f'<circle class="shared-foot-outline" cx="{_fmt(foot_x)}" '
            f'cy="{_fmt(foot_y)}" r="{_fmt(radius)}"/>'
            f'<circle class="shared-foot" cx="{_fmt(foot_x)}" '
            f'cy="{_fmt(foot_y)}" r="{_fmt(radius * 0.58)}"/>'
            "</g>"
        )

        xs = [foot_x, *(point[0] for point in point_positions)]
        ys = [foot_y, *(point[1] for point in point_positions)]
        label_value = (
            f'n = {float(group["calculated"]["refractive_index"]):.{decimals}f}'
        )
        text_width = max(font_size * 3.0, len(label_value) * font_size * 0.57)
        offset = group.get("label_offset_px", [72, 0])
        offset_x = float(offset[0]) * (font_size / 48.0)
        offset_y = float(offset[1]) * (font_size / 48.0)
        center_y = (min(ys) + max(ys)) / 2.0 + offset_y
        right_x = max(xs) + font_size * 0.8 + text_width / 2.0 + offset_x
        left_x = min(xs) - font_size * 0.8 - text_width / 2.0 - offset_x
        margin = font_size * 0.25
        center_x = right_x
        if center_x + text_width / 2.0 > panel_bounds[2] - margin:
            center_x = left_x
        center_x = max(
            panel_bounds[0] + margin + text_width / 2.0,
            min(center_x, panel_bounds[2] - margin - text_width / 2.0),
        )
        baseline_y = max(
            panel_bounds[1] + margin + font_size * 0.86,
            min(
                center_y + font_size * 0.31,
                panel_bounds[3] - margin - font_size * 0.24,
            ),
        )
        labels.append(
            f'<g id="{group_id}_label"{clip_attr}>'
            + _text(
                center_x,
                baseline_y,
                label_value,
                "refractive-index-label",
                attributes=(
                    f'data-group-id="{group_id}" '
                    'data-placement="group-side"'
                ),
            )
            + "</g>"
        )
    return "\n".join(markers), "\n".join(labels)


def _reference_lines_svg(
    reference_lines: list[dict[str, Any]],
    map_point: PointMapper,
    *,
    panel_bounds: Box,
    clip_id: str,
) -> str:
    """Render each saved reference as an infinite line clipped to the panel."""

    output: list[str] = []
    panel_diagonal = hypot(
        panel_bounds[2] - panel_bounds[0],
        panel_bounds[3] - panel_bounds[1],
    )
    for reference_line in reference_lines:
        x1, y1 = map_point(*reference_line["p1_px"])
        x2, y2 = map_point(*reference_line["p2_px"])
        dx = x2 - x1
        dy = y2 - y1
        length = hypot(dx, dy)
        if length == 0:
            continue
        tangent_x = dx / length
        tangent_y = dy / length
        midpoint_x = (x1 + x2) / 2.0
        midpoint_y = (y1 + y2) / 2.0
        reach = panel_diagonal * 2.0
        start_x = midpoint_x - tangent_x * reach
        start_y = midpoint_y - tangent_y * reach
        end_x = midpoint_x + tangent_x * reach
        end_y = midpoint_y + tangent_y * reach
        reference_id = escape(reference_line["id"])
        output.append(
            f'<g id="{reference_id}" clip-path="url(#{clip_id})" '
            f'data-geometry="infinite-line">'
            f'<line class="reference-line-outline" '
            f'x1="{_fmt(start_x)}" y1="{_fmt(start_y)}" '
            f'x2="{_fmt(end_x)}" y2="{_fmt(end_y)}"/>'
            f'<line class="reference-line" '
            f'x1="{_fmt(start_x)}" y1="{_fmt(start_y)}" '
            f'x2="{_fmt(end_x)}" y2="{_fmt(end_y)}"/>'
            "</g>"
        )
    return "\n".join(output)


def _scale_bars(
    *,
    origin_x: float,
    origin_y: float,
    x_length_px: float,
    y_length_px: float,
    x_value_um: float,
    y_value_um: float,
    font_size: float,
) -> str:
    x_label_y = origin_y + font_size * 1.05
    y_label_x = origin_x - font_size * 0.72
    y_mid = origin_y - y_length_px / 2.0
    return "\n".join(
        [
            (
                f'<path class="scale-bar" d="M {_fmt(origin_x)} {_fmt(origin_y)} '
                f'H {_fmt(origin_x + x_length_px)}"/>'
            ),
            (
                f'<path class="scale-bar" d="M {_fmt(origin_x)} {_fmt(origin_y)} '
                f'V {_fmt(origin_y - y_length_px)}"/>'
            ),
            _text(
                origin_x + x_length_px / 2.0,
                x_label_y,
                f"{x_value_um:g} µm",
                "scale-label",
                attributes='data-label-side="outside"',
            ),
            _text(
                y_label_x,
                y_mid,
                f"{y_value_um:g} µm",
                "scale-label",
                transform=f"rotate(-90 {_fmt(y_label_x)} {_fmt(y_mid)})",
                attributes='data-label-side="outside"',
            ),
        ]
    )


def _scale_reserved_box(
    origin_x: float,
    origin_y: float,
    x_length_px: float,
    y_length_px: float,
    font_size: float,
) -> Box:
    return (
        origin_x - font_size * 1.35,
        origin_y - y_length_px - font_size * 0.75,
        origin_x + x_length_px + font_size * 0.45,
        origin_y + font_size * 1.35,
    )


def _scale_values(
    annotation: dict[str, Any],
    config: dict[str, Any],
    view: str,
    *,
    crop_width: float | None = None,
    crop_height: float | None = None,
    calibration: Calibration,
) -> tuple[float, float]:
    if view == "global":
        settings = annotation["layout"].get(
            "main_scale_bar", config["layout"]["main_scale_bar"]
        )
    else:
        settings = annotation["layout"].get(
            "inset_scale_bar", config["layout"]["inset_scale_bar"]
        )
    if "x_um" in settings and "y_um" in settings:
        return float(settings["x_um"]), float(settings["y_um"])
    if crop_width is None or crop_height is None:
        return 200.0, 200.0
    target_fraction = float(
        config["layout"]["inset_scale_bar"].get("target_fraction", 0.2)
    )
    return (
        nice_scale_length(
            crop_width * calibration.x_um_per_px, target_fraction
        ),
        nice_scale_length(
            crop_height * calibration.y_um_per_px, target_fraction
        ),
    )


def _definitions(
    style: dict[str, Any],
    *,
    stroke_width: float,
    font_size: float,
    measurement_font_size: float,
    clip_definition: str,
) -> str:
    outline_color = style.get("measurement_outline_color", "#000000")
    outline_width = float(
        style.get(
            "measurement_outline_width_px",
            stroke_width + max(6.0, stroke_width * 0.65),
        )
    )
    label_color = style.get(
        "measurement_label_color", style["measurement_color"]
    )
    reference_color = style.get("reference_line_color", "#00ff00")
    reference_outline_color = style.get(
        "reference_line_outline_color", outline_color
    )
    reference_dash = float(style.get("reference_line_dash_px", stroke_width * 3))
    head_outline_width = max(1.0, outline_width - stroke_width)
    return f"""
    {clip_definition}
    <style>
      .measurement-outline {{
        stroke: {outline_color};
        stroke-width: {_fmt(outline_width)};
        stroke-linecap: butt;
        fill: none;
      }}
      .measurement {{
        stroke: {style["measurement_color"]};
        stroke-width: {_fmt(stroke_width)};
        stroke-linecap: butt;
        fill: none;
      }}
      .measurement-head-outline {{
        fill: {style["measurement_color"]};
        stroke: {outline_color};
        stroke-width: {_fmt(head_outline_width)};
        stroke-linejoin: round;
        paint-order: stroke fill;
      }}
      .measurement-head {{
        fill: {style["measurement_color"]};
        stroke: none;
      }}
      .shared-foot-outline {{
        fill: {style["measurement_color"]};
        stroke: {outline_color};
        stroke-width: {_fmt(head_outline_width)};
        paint-order: stroke fill;
      }}
      .shared-foot {{
        fill: {style["measurement_color"]};
        stroke: none;
      }}
      .reference-line-outline {{
        stroke: {reference_outline_color};
        stroke-width: {_fmt(outline_width)};
        stroke-linecap: butt;
        fill: none;
      }}
      .reference-line {{
        stroke: {reference_color};
        stroke-width: {_fmt(stroke_width)};
        stroke-dasharray: {_fmt(reference_dash)} {_fmt(reference_dash * 0.65)};
        stroke-linecap: butt;
        fill: none;
      }}
      .roi {{
        stroke: {style["roi_color"]};
        stroke-width: {_fmt(stroke_width)};
        fill: none;
      }}
      .scale-bar {{
        stroke: {style["scale_bar_color"]};
        stroke-width: {_fmt(stroke_width * 1.35)};
        stroke-linecap: square;
        fill: none;
      }}
      .label, .refractive-index-label, .scale-label, .roi-label {{
        font-family: {style["font_family"]};
        fill: #ffffff;
        stroke: #000000;
        paint-order: stroke fill;
      }}
      .label {{
        font-size: {_fmt(measurement_font_size)}px;
        fill: {label_color};
        stroke-width: {_fmt(measurement_font_size * 0.075)}px;
      }}
      .refractive-index-label {{
        font-size: {_fmt(measurement_font_size)}px;
        fill: {label_color};
        stroke-width: {_fmt(measurement_font_size * 0.075)}px;
      }}
      .scale-label, .roi-label {{
        font-size: {_fmt(font_size)}px;
        stroke-width: {_fmt(font_size * 0.075)}px;
      }}
      .roi-label {{ fill: {style["roi_color"]}; }}
      .panel-border {{
        stroke: {style["panel_border_color"]};
        stroke-width: {_fmt(stroke_width)};
        fill: none;
      }}
      .panel-placeholder {{ fill: #111111; }}
      .placeholder-label {{
        font-family: {style["font_family"]};
        font-size: {_fmt(font_size)}px;
        fill: #808080;
      }}
    </style>
"""


def _render_global_svg(
    *,
    image_id: str,
    row: dict[str, str],
    annotation: dict[str, Any],
    config: dict[str, Any],
    calibration: Calibration,
    image_uri: str,
) -> str:
    _left, _top, _right, _bottom, image_width, image_height = content_bounds(row)
    scale = float(config["layout"]["main_display_scale"])
    width = image_width * scale
    height = image_height * scale
    style = config["layout"]["style"]
    font_size = float(style["font_size_px"])
    measurement_font_size = float(
        style.get("global_measurement_font_size_px", font_size)
    )
    stroke_width = float(style["stroke_width_px"])
    arrow_head = float(style["arrow_head_px"])
    decimals = int(config["measurement"]["display_decimals"])
    refractive_decimals = int(
        config["measurement"].get("refractive_index_display_decimals", 3)
    )
    map_point: PointMapper = lambda x, y: (
        float(x) * scale,
        float(y) * scale,
    )

    x_bar_um, y_bar_um = _scale_values(
        annotation, config, "global", calibration=calibration
    )
    margin = font_size * 1.6
    x_bar_px = x_bar_um / calibration.x_um_per_px * scale
    y_bar_px = y_bar_um / calibration.y_um_per_px * scale
    origin_x = margin
    origin_y = height - margin
    scale_svg = _scale_bars(
        origin_x=origin_x,
        origin_y=origin_y,
        x_length_px=x_bar_px,
        y_length_px=y_bar_px,
        x_value_um=x_bar_um,
        y_value_um=y_bar_um,
        font_size=font_size,
    )
    reserved = [
        _scale_reserved_box(
            origin_x,
            origin_y,
            x_bar_px,
            y_bar_px,
            font_size,
        )
    ]

    roi_svg = ""
    rois = annotation.get("rois", [])
    if rois:
        roi = rois[0]
        roi_x = float(roi["x_px"]) * scale
        roi_y = float(roi["y_px"]) * scale
        roi_width = float(roi["width_px"]) * scale
        roi_height = float(roi["height_px"]) * scale
        roi_svg = (
            f'<rect id="{escape(roi["id"])}" class="roi" '
            f'x="{_fmt(roi_x)}" y="{_fmt(roi_y)}" '
            f'width="{_fmt(roi_width)}" height="{_fmt(roi_height)}"/>'
        )

    measurements = [
        item
        for item in annotation.get("measurements", [])
        if "main" in item.get("views", [])
    ]
    measurement_svg, label_svg = _measurement_svg(
        measurements,
        map_point,
        decimals=decimals,
        font_size=measurement_font_size,
        stroke_width=stroke_width,
        arrow_head=arrow_head,
        panel_bounds=(0.0, 0.0, width, height),
        reserved_boxes=reserved,
    )
    group_marker_svg, group_label_svg = _refractive_index_groups_svg(
        annotation.get("refractive_index_groups", []),
        measurements,
        map_point,
        decimals=refractive_decimals,
        font_size=measurement_font_size,
        stroke_width=stroke_width,
        panel_bounds=(0.0, 0.0, width, height),
    )
    measurement_svg = "\n".join(
        value for value in (measurement_svg, group_marker_svg) if value
    )
    label_svg = "\n".join(
        value for value in (label_svg, group_label_svg) if value
    )
    reference_svg = _reference_lines_svg(
        annotation.get("reference_lines", []),
        map_point,
        panel_bounds=(0.0, 0.0, width, height),
        clip_id="panelClip",
    )
    clip_definition = (
        f'<clipPath id="panelClip"><rect x="0" y="0" '
        f'width="{_fmt(width)}" height="{_fmt(height)}"/></clipPath>'
    )
    defs = _definitions(
        style,
        stroke_width=stroke_width,
        font_size=font_size,
        measurement_font_size=measurement_font_size,
        clip_definition=clip_definition,
    )
    metadata = _metadata(
        image_id, row, annotation, calibration, "global"
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:xlink="http://www.w3.org/1999/xlink"
     xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     width="{_fmt(width)}" height="{_fmt(height)}"
     viewBox="0 0 {_fmt(width)} {_fmt(height)}">
  <metadata>{metadata}</metadata>
  <defs>{defs}</defs>
  <rect id="canvas-background" x="0" y="0"
        width="{_fmt(width)}" height="{_fmt(height)}" fill="#000000"/>
  <g inkscape:groupmode="layer" inkscape:label="GLOBAL_IMAGE" id="GLOBAL_IMAGE">
    <image id="global-source" x="0" y="0" width="{_fmt(width)}"
           height="{_fmt(height)}" href="{image_uri}"
           xlink:href="{image_uri}"
           preserveAspectRatio="none"/>
  </g>
  <g inkscape:groupmode="layer" inkscape:label="GLOBAL_ROI" id="GLOBAL_ROI">
    {roi_svg}
  </g>
  <g inkscape:groupmode="layer" inkscape:label="GLOBAL_REFERENCE" id="GLOBAL_REFERENCE">
    {reference_svg}
  </g>
  <g inkscape:groupmode="layer" inkscape:label="GLOBAL_MEASURE" id="GLOBAL_MEASURE">
    {measurement_svg}
  </g>
  <g inkscape:groupmode="layer" inkscape:label="GLOBAL_LABELS" id="GLOBAL_LABELS">
    {label_svg}
  </g>
  <g inkscape:groupmode="layer" inkscape:label="GLOBAL_SCALE_XY" id="GLOBAL_SCALE_XY">
    {scale_svg}
  </g>
</svg>
"""


def _render_crop_svg(
    *,
    image_id: str,
    row: dict[str, str],
    annotation: dict[str, Any],
    config: dict[str, Any],
    calibration: Calibration,
    image_uri: str,
) -> str:
    _left, _top, _right, _bottom, image_width, image_height = content_bounds(row)
    size = image_height * float(config["layout"]["main_display_scale"])
    style = config["layout"]["style"]
    font_size = float(style["font_size_px"])
    stroke_width = float(style["stroke_width_px"])
    arrow_head = float(style["arrow_head_px"])
    decimals = int(config["measurement"]["display_decimals"])
    refractive_decimals = int(
        config["measurement"].get("refractive_index_display_decimals", 3)
    )
    panel_bounds = (0.0, 0.0, size, size)
    clip_definition = (
        f'<clipPath id="panelClip"><rect x="0" y="0" '
        f'width="{_fmt(size)}" height="{_fmt(size)}"/></clipPath>'
    )

    image_svg = (
        f'<rect x="0" y="0" width="{_fmt(size)}" '
        f'height="{_fmt(size)}" class="panel-placeholder"/>'
        + _text(
            size / 2.0,
            size / 2.0,
            "等待 ROI",
            "placeholder-label",
        )
    )
    measurement_svg = ""
    label_svg = ""
    reference_svg = ""
    scale_svg = ""
    rois = annotation.get("rois", [])
    if rois:
        crop_x, crop_y, crop_width, crop_height = square_crop_bounds(
            rois[0], image_width, image_height
        )
        scale_x = size / crop_width
        scale_y = size / crop_height
        image_x = -crop_x * scale_x
        image_y = -crop_y * scale_y
        image_svg = (
            f'<g clip-path="url(#panelClip)">'
            f'<image id="crop-source" x="{_fmt(image_x)}" '
            f'y="{_fmt(image_y)}" '
            f'width="{_fmt(image_width * scale_x)}" '
            f'height="{_fmt(image_height * scale_y)}" '
            f'href="{image_uri}" xlink:href="{image_uri}" '
            f'preserveAspectRatio="none"/>'
            f"</g>"
        )
        map_point: PointMapper = lambda x, y: (
            (float(x) - crop_x) * scale_x,
            (float(y) - crop_y) * scale_y,
        )
        x_bar_um, y_bar_um = _scale_values(
            annotation,
            config,
            "crop",
            crop_width=crop_width,
            crop_height=crop_height,
            calibration=calibration,
        )
        margin = font_size * 1.6
        x_bar_px = x_bar_um / calibration.x_um_per_px * scale_x
        y_bar_px = y_bar_um / calibration.y_um_per_px * scale_y
        origin_x = margin
        origin_y = size - margin
        scale_svg = _scale_bars(
            origin_x=origin_x,
            origin_y=origin_y,
            x_length_px=x_bar_px,
            y_length_px=y_bar_px,
            x_value_um=x_bar_um,
            y_value_um=y_bar_um,
            font_size=font_size,
        )
        reserved = [
            _scale_reserved_box(
                origin_x,
                origin_y,
                x_bar_px,
                y_bar_px,
                font_size,
            )
        ]
        measurements = [
            item
            for item in annotation.get("measurements", [])
            if "inset" in item.get("views", [])
        ]
        measurement_svg, label_svg = _measurement_svg(
            measurements,
            map_point,
            decimals=decimals,
            font_size=font_size,
            stroke_width=stroke_width,
            arrow_head=arrow_head,
            panel_bounds=panel_bounds,
            reserved_boxes=reserved,
            clip_id="panelClip",
        )
        inset_measurement_ids = {
            str(item["id"]) for item in measurements
        }
        groups = [
            group
            for group in annotation.get("refractive_index_groups", [])
            if set(group.get("measurement_ids", [])) <= inset_measurement_ids
        ]
        group_marker_svg, group_label_svg = _refractive_index_groups_svg(
            groups,
            measurements,
            map_point,
            decimals=refractive_decimals,
            font_size=font_size,
            stroke_width=stroke_width,
            panel_bounds=panel_bounds,
            clip_id="panelClip",
        )
        measurement_svg = "\n".join(
            value for value in (measurement_svg, group_marker_svg) if value
        )
        label_svg = "\n".join(
            value for value in (label_svg, group_label_svg) if value
        )
        reference_svg = _reference_lines_svg(
            annotation.get("reference_lines", []),
            map_point,
            panel_bounds=panel_bounds,
            clip_id="panelClip",
        )

    defs = _definitions(
        style,
        stroke_width=stroke_width,
        font_size=font_size,
        measurement_font_size=font_size,
        clip_definition=clip_definition,
    )
    metadata = _metadata(image_id, row, annotation, calibration, "crop")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:xlink="http://www.w3.org/1999/xlink"
     xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     width="{_fmt(size)}" height="{_fmt(size)}"
     viewBox="0 0 {_fmt(size)} {_fmt(size)}">
  <metadata>{metadata}</metadata>
  <defs>{defs}</defs>
  <rect id="canvas-background" x="0" y="0"
        width="{_fmt(size)}" height="{_fmt(size)}" fill="#000000"/>
  <g inkscape:groupmode="layer" inkscape:label="CROP_IMAGE" id="CROP_IMAGE">
    {image_svg}
  </g>
  <g inkscape:groupmode="layer" inkscape:label="CROP_REFERENCE" id="CROP_REFERENCE">
    {reference_svg}
  </g>
  <g inkscape:groupmode="layer" inkscape:label="CROP_MEASURE" id="CROP_MEASURE">
    {measurement_svg}
  </g>
  <g inkscape:groupmode="layer" inkscape:label="CROP_LABELS" id="CROP_LABELS">
    {label_svg}
  </g>
  <g inkscape:groupmode="layer" inkscape:label="CROP_SCALE_XY" id="CROP_SCALE_XY">
    {scale_svg}
  </g>
  <g inkscape:groupmode="layer" inkscape:label="CROP_BORDER" id="CROP_BORDER">
    <rect x="0" y="0" width="{_fmt(size)}" height="{_fmt(size)}"
          class="panel-border"/>
  </g>
</svg>
"""


def _metadata(
    image_id: str,
    row: dict[str, str],
    annotation: dict[str, Any],
    calibration: Calibration,
    view: str,
) -> str:
    _left, _top, _right, _bottom, content_width, content_height = content_bounds(row)
    return escape(
        json.dumps(
            {
                "image_id": image_id,
                "view": view,
                "image_sha256": row["sha256"],
                "calibration_id": calibration.calibration_id,
                "source_x_um_per_px": calibration.x_um_per_px,
                "source_y_um_per_px": calibration.y_um_per_px,
                "content_width_px": content_width,
                "content_height_px": content_height,
                "annotation_revision": annotation["revision"],
            },
            ensure_ascii=False,
        )
    )


def render_svgs(root: Path, image_id: str) -> dict[str, Path]:
    sync_raw_images(root)
    config = load_config(root)
    manifest = manifest_by_id(root)
    if image_id not in manifest:
        raise ValueError(f"Unknown image_id: {image_id}")
    row = manifest[image_id]
    annotation = load_annotation(root, image_id)
    base_calibration = Calibration.from_project_config(config)
    source_width, source_height = source_dimensions(config)
    crop_left, crop_top, crop_right, crop_bottom, content_width, content_height = (
        content_bounds(row)
    )
    calibration = calibration_for_content(
        base_calibration,
        source_width,
        source_height,
        content_width,
        content_height,
    )
    image_path = root / Path(row["project_path"])
    image_uri = _image_data_uri(
        image_path,
        (crop_left, crop_top, crop_right, crop_bottom),
    )

    documents = {
        "global": _render_global_svg(
            image_id=image_id,
            row=row,
            annotation=annotation,
            config=config,
            calibration=calibration,
            image_uri=image_uri,
        ),
    }
    if annotation.get("rois", []):
        documents["crop"] = _render_crop_svg(
            image_id=image_id,
            row=row,
            annotation=annotation,
            config=config,
            calibration=calibration,
            image_uri=image_uri,
        )
    output_dir = root / "layouts" / "masters"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_stem = artifact_stem(row)
    paths: dict[str, Path] = {}
    for view, document in documents.items():
        path = output_dir / f"{output_stem}_{view}.svg"
        path.write_text(document, encoding="utf-8", newline="\n")
        paths[view] = path
    if "crop" not in paths:
        stale_paths = (
            output_dir / f"{output_stem}_crop.svg",
            root / "exports" / "png" / f"{output_stem}_crop.png",
            root / "exports" / "pdf" / f"{output_stem}_crop.pdf",
            root
            / "exports"
            / "vector"
            / "wps"
            / f"{output_stem}_crop_wps.svg",
        )
        for stale_path in stale_paths:
            if stale_path.is_file():
                stale_path.unlink()
    return paths


def render_svg(root: Path, image_id: str) -> Path:
    """Backward-compatible entry point; renders both files and returns global."""

    return render_svgs(root, image_id)["global"]
