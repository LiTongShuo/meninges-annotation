from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees, floor, hypot, log10
from typing import Any


@dataclass(frozen=True)
class Calibration:
    """An anisotropic calibration expressed in source-image pixels."""

    calibration_id: str
    x_um_per_px: float
    y_um_per_px: float
    display_scale: float
    display_x_um_per_px: float
    display_y_um_per_px: float

    @classmethod
    def from_project_config(cls, config: dict[str, Any]) -> "Calibration":
        calibration = config["calibration"]
        source = calibration["source_space"]
        provided = calibration["provided_reference"]
        return cls(
            calibration_id=calibration["id"],
            x_um_per_px=float(source["x_um_per_px"]),
            y_um_per_px=float(source["y_um_per_px"]),
            display_scale=float(provided["scale_factor_from_source"]),
            display_x_um_per_px=float(provided["x_um_per_px"]),
            display_y_um_per_px=float(provided["y_um_per_px"]),
        )

    def distance_um(
        self, p1_px: tuple[float, float], p2_px: tuple[float, float]
    ) -> float:
        dx_px = float(p2_px[0]) - float(p1_px[0])
        dy_px = float(p2_px[1]) - float(p1_px[1])
        return hypot(dx_px * self.x_um_per_px, dy_px * self.y_um_per_px)

    def angle_degrees(
        self, p1_px: tuple[float, float], p2_px: tuple[float, float]
    ) -> float:
        dx_um = (float(p2_px[0]) - float(p1_px[0])) * self.x_um_per_px
        dy_um = (float(p2_px[1]) - float(p1_px[1])) * self.y_um_per_px
        return degrees(atan2(dy_um, dx_um))

    def measurement(
        self, p1_px: tuple[float, float], p2_px: tuple[float, float]
    ) -> dict[str, float | str]:
        dx_px = float(p2_px[0]) - float(p1_px[0])
        dy_px = float(p2_px[1]) - float(p1_px[1])
        return {
            "calibration_id": self.calibration_id,
            "dx_px": dx_px,
            "dy_px": dy_px,
            "dx_um": dx_px * self.x_um_per_px,
            "dy_um": dy_px * self.y_um_per_px,
            "distance_um": self.distance_um(p1_px, p2_px),
            "angle_degrees": self.angle_degrees(p1_px, p2_px),
        }


def project_point_to_line_px(
    calibration: Calibration,
    point_px: tuple[float, float],
    line_p1_px: tuple[float, float],
    line_p2_px: tuple[float, float],
) -> tuple[float, float]:
    """Project a source-pixel point onto a line in calibrated physical space."""

    line_dx_um = (line_p2_px[0] - line_p1_px[0]) * calibration.x_um_per_px
    line_dy_um = (line_p2_px[1] - line_p1_px[1]) * calibration.y_um_per_px
    denominator = line_dx_um**2 + line_dy_um**2
    if denominator == 0:
        raise ValueError("Reference line has zero length")
    point_dx_um = (point_px[0] - line_p1_px[0]) * calibration.x_um_per_px
    point_dy_um = (point_px[1] - line_p1_px[1]) * calibration.y_um_per_px
    projection = (
        point_dx_um * line_dx_um + point_dy_um * line_dy_um
    ) / denominator
    return (
        line_p1_px[0] + projection * (line_p2_px[0] - line_p1_px[0]),
        line_p1_px[1] + projection * (line_p2_px[1] - line_p1_px[1]),
    )


def shared_foot_geometry_errors(
    calibration: Calibration,
    first_measurement: dict[str, Any],
    second_measurement: dict[str, Any],
    reference_line: dict[str, Any],
) -> list[str]:
    """Return geometry errors for two point-to-line distances sharing one foot."""

    errors: list[str] = []
    first_point = tuple(float(value) for value in first_measurement["p1_px"])
    first_foot = tuple(float(value) for value in first_measurement["p2_px"])
    second_point = tuple(float(value) for value in second_measurement["p1_px"])
    second_foot = tuple(float(value) for value in second_measurement["p2_px"])
    line_p1 = tuple(float(value) for value in reference_line["p1_px"])
    line_p2 = tuple(float(value) for value in reference_line["p2_px"])

    if calibration.distance_um(first_foot, second_foot) > 1e-4:
        errors.append("the two perpendicular feet do not coincide")

    for label, point, foot in (
        ("first", first_point, first_foot),
        ("second", second_point, second_foot),
    ):
        projected_foot = project_point_to_line_px(
            calibration, foot, line_p1, line_p2
        )
        if calibration.distance_um(foot, projected_foot) > 1e-4:
            errors.append(f"{label} foot is not on L001")

        line_dx_um = (line_p2[0] - line_p1[0]) * calibration.x_um_per_px
        line_dy_um = (line_p2[1] - line_p1[1]) * calibration.y_um_per_px
        measure_dx_um = (foot[0] - point[0]) * calibration.x_um_per_px
        measure_dy_um = (foot[1] - point[1]) * calibration.y_um_per_px
        line_length = hypot(line_dx_um, line_dy_um)
        measure_length = hypot(measure_dx_um, measure_dy_um)
        if measure_length == 0:
            errors.append(f"{label} distance is zero")
            continue
        normalized_dot = abs(
            line_dx_um * measure_dx_um + line_dy_um * measure_dy_um
        ) / (line_length * measure_length)
        if normalized_dot > 1e-5:
            errors.append(f"{label} distance is not perpendicular to L001")
    return errors


def refractive_index_calculation(
    first_distance_um: float,
    second_distance_um: float,
) -> dict[str, float | str]:
    """Calculate n = 1 + smaller distance / larger distance."""

    if first_distance_um <= 0 or second_distance_um <= 0:
        raise ValueError("Refractive-index distances must be positive")
    smaller = min(first_distance_um, second_distance_um)
    larger = max(first_distance_um, second_distance_um)
    ratio = smaller / larger
    return {
        "formula": "1 + smaller_distance_um / larger_distance_um",
        "smaller_distance_um": smaller,
        "larger_distance_um": larger,
        "distance_ratio": ratio,
        "refractive_index": 1.0 + ratio,
    }


def nice_scale_length(physical_span: float, target_fraction: float = 0.2) -> float:
    """Return the largest 1/2/5 × 10ⁿ length below the target fraction."""

    if physical_span <= 0:
        raise ValueError("physical_span must be positive")
    if not 0 < target_fraction < 1:
        raise ValueError("target_fraction must be between 0 and 1")

    target = physical_span * target_fraction
    exponent = floor(log10(target))
    base = 10.0**exponent
    for factor in (5.0, 2.0, 1.0):
        candidate = factor * base
        if candidate <= target:
            return candidate
    return 5.0 * base / 10.0
