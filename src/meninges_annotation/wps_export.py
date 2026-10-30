from __future__ import annotations

import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .project import artifact_stem, load_config, manifest_by_id


SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


def _fmt(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _tool_path(root: Path, configured: str) -> Path:
    path = Path(configured)
    return path if path.is_absolute() else root / path


def _inline_styles(style: dict[str, Any]) -> dict[str, str]:
    stroke_width = float(style["stroke_width_px"])
    outline_width = float(
        style.get("measurement_outline_width_px", stroke_width + 8.0)
    )
    head_outline_width = max(1.0, outline_width - stroke_width)
    measurement_color = str(style["measurement_color"])
    outline_color = str(
        style.get("measurement_outline_color", "#000000")
    )
    label_color = str(
        style.get("measurement_label_color", measurement_color)
    )
    font_family = str(style["font_family"])
    font_size = float(style["font_size_px"])
    return {
        "measurement-outline": (
            f"stroke:{outline_color};stroke-width:{_fmt(outline_width)};"
            "stroke-linecap:butt;fill:none"
        ),
        "measurement": (
            f"stroke:{measurement_color};stroke-width:{_fmt(stroke_width)};"
            "stroke-linecap:butt;fill:none"
        ),
        "measurement-head-outline": (
            f"fill:{measurement_color};stroke:{outline_color};"
            f"stroke-width:{_fmt(head_outline_width)};"
            "stroke-linejoin:round"
        ),
        "measurement-head": f"fill:{measurement_color};stroke:none",
        "shared-foot-outline": (
            f"fill:{measurement_color};stroke:{outline_color};"
            f"stroke-width:{_fmt(head_outline_width)}"
        ),
        "shared-foot": f"fill:{measurement_color};stroke:none",
        "roi": (
            f"stroke:{style['roi_color']};"
            f"stroke-width:{_fmt(stroke_width)};fill:none"
        ),
        "scale-bar": (
            f"stroke:{style['scale_bar_color']};"
            f"stroke-width:{_fmt(stroke_width * 1.35)};"
            "stroke-linecap:square;fill:none"
        ),
        # Text has already become paths. Avoid text strokes and paint-order,
        # which older WPS SVG renderers can turn into nearly black glyphs.
        "label": f"fill:{label_color};stroke:none",
        "refractive-index-label": f"fill:{label_color};stroke:none",
        "scale-label": (
            f"fill:{style['scale_bar_color']};stroke:none"
        ),
        "roi-label": f"fill:{style['roi_color']};stroke:none",
        "panel-border": (
            f"stroke:{style['panel_border_color']};"
            f"stroke-width:{_fmt(stroke_width)};fill:none"
        ),
        "panel-placeholder": "fill:#111111;stroke:none",
        "placeholder-label": (
            f"font-family:{font_family};font-size:{_fmt(font_size)}px;"
            "fill:#808080;stroke:none"
        ),
    }


def postprocess_wps_svg(
    path: Path,
    style: dict[str, Any],
) -> None:
    tree = ET.parse(path)
    root = tree.getroot()
    root.set("version", "1.1")

    images = root.findall(f".//{{{SVG_NS}}}image")
    if not images:
        raise ValueError("WPS SVG contains no embedded image")
    for image in images:
        image_uri = image.get("href") or image.get(f"{{{XLINK_NS}}}href")
        if not image_uri or not image_uri.startswith("data:image/"):
            raise ValueError("WPS SVG image is not embedded as a data URI")
        image.set("href", image_uri)
        image.set(f"{{{XLINK_NS}}}href", image_uri)

    class_styles = _inline_styles(style)
    for element in root.iter():
        classes = element.get("class", "").split()
        for class_name in classes:
            if class_name in class_styles:
                element.set("style", class_styles[class_name])
                break

    style_tag = f"{{{SVG_NS}}}style"
    for parent in root.iter():
        for child in list(parent):
            if child.tag == style_tag:
                parent.remove(child)

    if root.findall(f".//{{{SVG_NS}}}text"):
        raise ValueError("WPS SVG still contains text elements")

    tree.write(
        path,
        encoding="utf-8",
        xml_declaration=True,
        short_empty_elements=True,
    )


def export_wps_svg(
    root: Path,
    image_id: str,
    view: str,
    source_svg: Path,
) -> Path:
    config = load_config(root)
    inkscape = _tool_path(root, config["tools"]["inkscape_executable"])
    if not inkscape.is_file():
        raise FileNotFoundError(f"Inkscape executable not found: {inkscape}")

    output_stem = artifact_stem(manifest_by_id(root)[image_id])
    output = (
        root
        / "exports"
        / "vector"
        / "wps"
        / f"{output_stem}_{view}_wps.svg"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="wps-svg-", dir=output.parent
    ) as temporary:
        temporary_svg = Path(temporary) / output.name
        subprocess.run(
            [
                str(inkscape),
                "--export-type=svg",
                f"--export-filename={temporary_svg}",
                "--export-plain-svg",
                "--export-text-to-path",
                str(source_svg),
            ],
            check=True,
        )
        if not temporary_svg.is_file():
            raise FileNotFoundError(
                f"Inkscape did not create WPS SVG: {temporary_svg}"
            )
        postprocess_wps_svg(
            temporary_svg,
            config["layout"]["style"],
        )
        temporary_svg.replace(output)
    return output
