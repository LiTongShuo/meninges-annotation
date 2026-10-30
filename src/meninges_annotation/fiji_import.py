from __future__ import annotations

import csv
import io
import math
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from .calibration import (
    Calibration,
    refractive_index_calculation,
    shared_foot_geometry_errors,
)
from .image_geometry import (
    calibration_for_content,
    content_bounds,
    source_dimensions,
)
from .project import (
    annotation_path,
    atomic_write_json,
    load_annotation,
    load_config,
    manifest_by_id,
    sync_raw_images,
)

MEASUREMENT_ID_PATTERN = re.compile(r"^T\d+$")
GROUP_ID_PATTERN = re.compile(r"^G\d{3}$")
REFERENCE_LINE_ID = "L001"
THICKNESS_KIND = "thickness"
SHARED_FOOT_KIND = "shared_foot_distance"


def _read_csv_rows(
    csv_path: Path,
    known_image_ids: set[str] | None = None,
) -> list[dict[str, str]]:
    data = csv_path.read_bytes()
    decoded_options: list[tuple[str, list[dict[str, str]]]] = []
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        rows = list(csv.DictReader(io.StringIO(text, newline="")))
        decoded_options.append((encoding, rows))

    if not decoded_options:
        raise ValueError(f"Unsupported CSV encoding for {csv_path}")

    if len(decoded_options) > 1 and known_image_ids is not None:

        def matches_manifest(rows: list[dict[str, str]]) -> bool:
            image_ids = {
                str(row.get("image_id", "")).strip()
                for row in rows
                if str(row.get("image_id", "")).strip()
            }
            return bool(image_ids) and image_ids <= known_image_ids

        matched = [
            rows for _, rows in decoded_options if matches_manifest(rows)
        ]
        if len(matched) == 1:
            return matched[0]
        if len(matched) > 1:
            for encoding, rows in decoded_options:
                if encoding == "utf-8-sig" and matches_manifest(rows):
                    return rows

    return decoded_options[0][1]


def _number(row: dict[str, str], key: str) -> float:
    value = row.get(key, "").strip()
    if not value:
        raise ValueError(f"Missing numeric field {key}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"Numeric field {key} must be finite")
    return number


def _point_inside_roi(
    point: list[float],
    roi: dict[str, Any],
) -> bool:
    return (
        float(roi["x_px"]) <= point[0] <= float(roi["x_px"]) + float(roi["width_px"])
        and float(roi["y_px"])
        <= point[1]
        <= float(roi["y_px"]) + float(roi["height_px"])
    )


def import_fiji_objects(root: Path, csv_path: Path) -> tuple[str, int, int]:
    if (root / "raw").is_dir():
        sync_raw_images(root)
    manifest = manifest_by_id(root)
    rows = _read_csv_rows(csv_path, known_image_ids=set(manifest))
    if not rows:
        raise ValueError(f"No Fiji objects found in {csv_path}")

    image_ids = {row["image_id"].strip() for row in rows}
    if len(image_ids) != 1:
        raise ValueError("A Fiji object CSV must contain exactly one image_id")
    image_id = image_ids.pop()

    if image_id not in manifest:
        raise ValueError(f"Unknown image_id: {image_id}")

    config = load_config(root)
    base_calibration = Calibration.from_project_config(config)
    source_width, source_height = source_dimensions(config)
    _left, _top, _right, _bottom, content_width, content_height = content_bounds(
        manifest[image_id]
    )
    calibration = calibration_for_content(
        base_calibration,
        source_width,
        source_height,
        content_width,
        content_height,
    )
    previous = load_annotation(root, image_id)
    if previous.get("status") == "approved":
        raise ValueError(
            f"{image_id}: annotation is completed and locked; Fiji import is disabled"
        )
    updated = deepcopy(previous)
    measurements: list[dict[str, Any]] = []
    rois: list[dict[str, Any]] = []
    reference_lines: list[dict[str, Any]] = []
    object_ids: set[str] = set()
    image_width = float(content_width)
    image_height = float(content_height)

    for row in rows:
        object_type = row["type"].strip().lower()
        object_id = row["object_id"].strip()
        if not object_id:
            raise ValueError("Every Fiji object must have an object_id")
        if object_id in object_ids:
            raise ValueError(f"Duplicate Fiji object_id: {object_id}")
        object_ids.add(object_id)

        if object_type == "measurement":
            p1 = [_number(row, "x1_px"), _number(row, "y1_px")]
            p2 = [_number(row, "x2_px"), _number(row, "y2_px")]
            if not MEASUREMENT_ID_PATTERN.fullmatch(object_id):
                raise ValueError(
                    f"Measurement object_id must match T followed by digits: {object_id}"
                )
            if p1 == p2:
                raise ValueError(f"Measurement {object_id} has zero length")
            for point_name, point in (("p1", p1), ("p2", p2)):
                if not (
                    0 <= point[0] <= image_width
                    and 0 <= point[1] <= image_height
                ):
                    raise ValueError(
                        f"Measurement {object_id} {point_name} is outside the source image"
                    )
            measurement_kind = (
                row.get("measurement_kind", "").strip() or THICKNESS_KIND
            )
            if measurement_kind not in {THICKNESS_KIND, SHARED_FOOT_KIND}:
                raise ValueError(
                    f"Unsupported measurement_kind for {object_id}: "
                    f"{measurement_kind}"
                )
            measurement: dict[str, Any] = {
                "id": object_id,
                "kind": measurement_kind,
                "p1_px": p1,
                "p2_px": p2,
                "views": ["main"],
                "label_offset_px": [0, -72],
                "calculated": calibration.measurement(tuple(p1), tuple(p2)),
            }
            if measurement_kind == SHARED_FOOT_KIND:
                group_id = row.get("group_id", "").strip()
                group_member = row.get("group_member", "").strip().upper()
                if not GROUP_ID_PATTERN.fullmatch(group_id):
                    raise ValueError(
                        f"Shared-foot measurement {object_id} requires a "
                        "group_id such as G001"
                    )
                if group_member not in {"A", "B"}:
                    raise ValueError(
                        f"Shared-foot measurement {object_id} requires "
                        "group_member A or B"
                    )
                measurement["group_id"] = group_id
                measurement["group_member"] = group_member
            measurements.append(measurement)
        elif object_type == "reference_line":
            if object_id != REFERENCE_LINE_ID:
                raise ValueError(
                    f"Reference line object_id must be {REFERENCE_LINE_ID}: {object_id}"
                )
            p1 = [_number(row, "x1_px"), _number(row, "y1_px")]
            p2 = [_number(row, "x2_px"), _number(row, "y2_px")]
            if p1 == p2:
                raise ValueError(f"Reference line {object_id} has zero length")
            for point_name, point in (("p1", p1), ("p2", p2)):
                if not (
                    0 <= point[0] <= image_width
                    and 0 <= point[1] <= image_height
                ):
                    raise ValueError(
                        f"Reference line {object_id} {point_name} is outside the source image"
                    )
            reference_lines.append(
                {
                    "id": object_id,
                    "p1_px": p1,
                    "p2_px": p2,
                }
            )
        elif object_type == "roi":
            if object_id != "R001":
                raise ValueError(f"Inset ROI object_id must be R001: {object_id}")
            rois.append(
                {
                    "id": object_id,
                    "x_px": _number(row, "x_px"),
                    "y_px": _number(row, "y_px"),
                    "width_px": _number(row, "width_px"),
                    "height_px": _number(row, "height_px"),
                }
            )
        else:
            raise ValueError(f"Unsupported Fiji object type: {object_type}")

    if len(rois) > 1:
        raise ValueError("A Fiji import may contain at most one R001 inset ROI")
    if len(reference_lines) > 1:
        raise ValueError("A Fiji import may contain at most one L001 reference line")

    if rois:
        roi = rois[0]
        roi_x = float(roi["x_px"])
        roi_y = float(roi["y_px"])
        roi_width = float(roi["width_px"])
        roi_height = float(roi["height_px"])
        if roi_width <= 0 or roi_height <= 0:
            raise ValueError("R001 dimensions must be positive")
        if not math.isclose(roi_width, roi_height, rel_tol=0, abs_tol=1e-6):
            raise ValueError("R001 must be square in source-pixel coordinates")
        if (
            roi_x < 0
            or roi_y < 0
            or roi_x + roi_width > image_width
            or roi_y + roi_height > image_height
        ):
            raise ValueError("R001 is outside the source image")

        for measurement in measurements:
            if _point_inside_roi(measurement["p1_px"], roi) and _point_inside_roi(
                measurement["p2_px"], roi
            ):
                measurement["views"].append("inset")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for measurement in measurements:
        if measurement["kind"] == SHARED_FOOT_KIND:
            grouped.setdefault(str(measurement["group_id"]), []).append(measurement)

    refractive_index_groups: list[dict[str, Any]] = []
    if grouped and len(reference_lines) != 1:
        raise ValueError(
            "Shared-foot refractive-index groups require exactly one L001"
        )
    reference_line = reference_lines[0] if reference_lines else None
    for group_id, members in sorted(grouped.items()):
        if len(members) != 2:
            raise ValueError(
                f"{group_id} must contain exactly two shared-foot measurements"
            )
        by_member = {str(item["group_member"]): item for item in members}
        if set(by_member) != {"A", "B"}:
            raise ValueError(f"{group_id} must contain members A and B exactly once")
        assert reference_line is not None
        geometry_errors = shared_foot_geometry_errors(
            calibration,
            by_member["A"],
            by_member["B"],
            reference_line,
        )
        if geometry_errors:
            raise ValueError(f"{group_id}: {'; '.join(geometry_errors)}")
        first_distance = float(by_member["A"]["calculated"]["distance_um"])
        second_distance = float(by_member["B"]["calculated"]["distance_um"])
        calculation = refractive_index_calculation(
            first_distance, second_distance
        )
        foot = [float(value) for value in by_member["A"]["p2_px"]]
        refractive_index_groups.append(
            {
                "id": group_id,
                "reference_id": REFERENCE_LINE_ID,
                "measurement_ids": [
                    str(by_member["A"]["id"]),
                    str(by_member["B"]["id"]),
                ],
                "foot_px": foot,
                "label_offset_px": [72, 0],
                "calculated": calculation,
            }
        )
        if rois and not all("inset" in item["views"] for item in members):
            for item in members:
                item["views"] = ["main"]

    history_dir = root / "annotations" / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    old_revision = int(previous["revision"])
    backup = history_dir / f"{image_id}.r{old_revision}.json"
    if not backup.exists():
        atomic_write_json(backup, previous)

    updated["revision"] = old_revision + 1
    updated["status"] = "in_progress"
    updated["measurements"] = measurements
    updated["rois"] = rois
    updated["reference_lines"] = reference_lines
    updated["refractive_index_groups"] = refractive_index_groups
    updated.setdefault("history", []).append(
        {
            "revision": updated["revision"],
            "date": datetime.now().astimezone().isoformat(timespec="seconds"),
            "reason": f"imported from {csv_path.name}",
        }
    )
    atomic_write_json(annotation_path(root, image_id), updated)
    return image_id, len(measurements), len(rois)


def recalculate_annotation(
    annotation: dict[str, Any], calibration: Calibration
) -> dict[str, Any]:
    if annotation.get("status") == "approved":
        raise ValueError(
            f"{annotation.get('image_id', '?')}: "
            "annotation is completed and locked; recalculation is disabled"
        )
    updated = deepcopy(annotation)
    for measurement in updated.get("measurements", []):
        p1 = tuple(float(value) for value in measurement["p1_px"])
        p2 = tuple(float(value) for value in measurement["p2_px"])
        measurement["calculated"] = calibration.measurement(p1, p2)
    by_id = {
        str(measurement["id"]): measurement
        for measurement in updated.get("measurements", [])
    }
    for group in updated.get("refractive_index_groups", []):
        measurement_ids = group.get("measurement_ids", [])
        if len(measurement_ids) != 2 or any(
            measurement_id not in by_id for measurement_id in measurement_ids
        ):
            continue
        first = by_id[measurement_ids[0]]
        second = by_id[measurement_ids[1]]
        group["calculated"] = refractive_index_calculation(
            float(first["calculated"]["distance_um"]),
            float(second["calculated"]["distance_um"]),
        )
    return updated
