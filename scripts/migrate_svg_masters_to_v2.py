from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from meninges_annotation.project import find_project_root


ARCHIVE_SUFFIX = "__cal-v1-y6.4319024"
SVG_NS = "http://www.w3.org/2000/svg"
INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
SODIPODI_NS = "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"
XLINK_NS = "http://www.w3.org/1999/xlink"

ET.register_namespace("", SVG_NS)
ET.register_namespace("inkscape", INKSCAPE_NS)
ET.register_namespace("sodipodi", SODIPODI_NS)
ET.register_namespace("xlink", XLINK_NS)


def _by_id(root: ET.Element, element_id: str) -> ET.Element | None:
    return root.find(f".//*[@id='{element_id}']")


def _class_tokens(element: ET.Element) -> set[str]:
    return set(element.get("class", "").split())


def _label_text(group: ET.Element) -> ET.Element | None:
    for element in group.iter():
        if element.tag == f"{{{SVG_NS}}}text":
            return element
    return None


def _copy_v2_values(old_root: ET.Element, new_root: ET.Element) -> None:
    old_metadata = old_root.find(f"{{{SVG_NS}}}metadata")
    new_metadata = new_root.find(f"{{{SVG_NS}}}metadata")
    if old_metadata is None or new_metadata is None:
        raise ValueError("SVG metadata is missing")
    old_metadata.text = new_metadata.text

    for new_group in new_root.findall(f".//{{{SVG_NS}}}g"):
        group_id = new_group.get("id", "")
        if not re.fullmatch(r"[TG]\d{3}_label", group_id):
            continue
        old_group = _by_id(old_root, group_id)
        if old_group is None:
            continue
        old_text = _label_text(old_group)
        new_text = _label_text(new_group)
        if old_text is not None and new_text is not None:
            old_text.text = new_text.text

    old_scale_paths = [
        element
        for element in old_root.findall(f".//{{{SVG_NS}}}path")
        if "scale-bar" in _class_tokens(element)
    ]
    new_scale_paths = [
        element
        for element in new_root.findall(f".//{{{SVG_NS}}}path")
        if "scale-bar" in _class_tokens(element)
    ]
    old_vertical = [element for element in old_scale_paths if " V " in element.get("d", "")]
    new_vertical = [element for element in new_scale_paths if " V " in element.get("d", "")]
    if len(old_vertical) != len(new_vertical):
        raise ValueError("Vertical scale-bar count changed")
    for old_path, new_path in zip(old_vertical, new_vertical, strict=True):
        old_path.set("d", new_path.get("d", ""))

    old_scale_labels = [
        element
        for element in old_root.findall(f".//{{{SVG_NS}}}text")
        if "scale-label" in _class_tokens(element)
        and "rotate(-90" in element.get("transform", "")
    ]
    new_scale_labels = [
        element
        for element in new_root.findall(f".//{{{SVG_NS}}}text")
        if "scale-label" in _class_tokens(element)
        and "rotate(-90" in element.get("transform", "")
    ]
    if len(old_scale_labels) != len(new_scale_labels):
        raise ValueError("Vertical scale-label count changed")
    for old_label, new_label in zip(old_scale_labels, new_scale_labels, strict=True):
        for attribute in ("x", "y", "transform"):
            value = new_label.get(attribute)
            if value is not None:
                old_label.set(attribute, value)


def main() -> int:
    root = find_project_root(Path.cwd())
    masters = root / "layouts" / "masters"
    archived = sorted(masters.glob(f"*{ARCHIVE_SUFFIX}.svg"))
    migrated = 0

    for archived_path in archived:
        current_path = archived_path.with_name(
            archived_path.name.replace(f"{ARCHIVE_SUFFIX}.svg", ".svg")
        )
        if not current_path.is_file():
            continue

        old_tree = ET.parse(archived_path)
        new_tree = ET.parse(current_path)
        _copy_v2_values(old_tree.getroot(), new_tree.getroot())
        old_tree.write(current_path, encoding="utf-8", xml_declaration=True)
        migrated += 1

    print(f"Migrated SVG masters while preserving v1 visual edits: {migrated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
