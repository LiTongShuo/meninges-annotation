import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from meninges_annotation.cli import command_export_wps
from meninges_annotation.project import find_project_root, image_id_from_project_path
from meninges_annotation.wps_export import (
    SVG_NS,
    XLINK_NS,
    postprocess_wps_svg,
)
from project_data import has_complete_project_data


class WpsExportTests(unittest.TestCase):
    BSCAN_X_ID = image_id_from_project_path("raw/脑膜/bscan_x-1.png")

    def test_postprocess_embeds_dual_href_and_inlines_styles(self) -> None:
        style = {
            "font_family": "Arial, sans-serif",
            "font_size_px": 96,
            "stroke_width_px": 12,
            "measurement_color": "#00ffff",
            "measurement_label_color": "#ffff00",
            "measurement_outline_color": "#000000",
            "measurement_outline_width_px": 20,
            "roi_color": "#00ffff",
            "scale_bar_color": "#ffffff",
            "panel_border_color": "#808080",
        }
        document = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg">
  <defs><style>.label { fill: red; }</style></defs>
  <image href="data:image/png;base64,AAAA" width="10" height="10"/>
  <path class="label" d="M 0 0"/>
  <line class="measurement" x1="0" y1="0" x2="1" y2="1"/>
</svg>
"""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "wps.svg"
            path.write_text(document, encoding="utf-8")
            postprocess_wps_svg(path, style)

            tree = ET.parse(path)
            root = tree.getroot()
            image = root.find(f".//{{{SVG_NS}}}image")
            self.assertIsNotNone(image)
            assert image is not None
            self.assertEqual(
                image.get("href"), "data:image/png;base64,AAAA"
            )
            self.assertEqual(
                image.get(f"{{{XLINK_NS}}}href"),
                "data:image/png;base64,AAAA",
            )
            self.assertIsNone(root.find(f".//{{{SVG_NS}}}style"))
            label = root.find(f".//{{{SVG_NS}}}path")
            self.assertIsNotNone(label)
            assert label is not None
            self.assertEqual(
                label.get("style"), "fill:#ffff00;stroke:none"
            )
            line = root.find(f".//{{{SVG_NS}}}line")
            self.assertIsNotNone(line)
            assert line is not None
            self.assertIn("stroke:#00ffff", line.get("style", ""))

    @unittest.skipUnless(
        has_complete_project_data(),
        "Requires an authorized local image, annotation, and calibration dataset.",
    )
    def test_cli_uses_existing_master_svgs_for_wps_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            project_root = Path(__file__).resolve().parents[1]
            for directory in ("config", "metadata", "annotations", "raw"):
                shutil.copytree(project_root / directory, temp_root / directory)
            master_dir = temp_root / "layouts" / "masters"
            master_dir.mkdir(parents=True)
            masters = {
                view: master_dir / f"脑膜__bscan_x-1_{view}.svg"
                for view in ("global", "crop")
            }
            for view, path in masters.items():
                path.write_text(f"<svg data-view='{view}'/>", encoding="utf-8")

            args = SimpleNamespace(
                root=temp_root,
                image_id=self.BSCAN_X_ID,
                view="both",
            )
            with patch(
                "meninges_annotation.cli.export_wps_svg",
                side_effect=lambda root, image_id, view, source: (
                    temp_root / f"{image_id}_{view}_wps.svg"
                ),
            ) as export_mock:
                self.assertEqual(command_export_wps(args), 0)

            source_paths = [
                call.args[3] for call in export_mock.call_args_list
            ]
            self.assertEqual(
                source_paths,
                [masters["global"], masters["crop"]],
            )
            self.assertEqual(
                masters["global"].read_text(encoding="utf-8"),
                "<svg data-view='global'/>",
            )


if __name__ == "__main__":
    unittest.main()
