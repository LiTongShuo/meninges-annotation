import xml.etree.ElementTree as ET
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from meninges_annotation.calibration import (
    Calibration,
    refractive_index_calculation,
)
from meninges_annotation.fiji_import import import_fiji_objects
from meninges_annotation.project import (
    find_project_root,
    image_id_from_project_path,
    load_config,
)
from meninges_annotation.svg_export import (
    _adaptive_arrow_head_length,
    render_svgs,
    square_crop_bounds,
)
from project_data import has_complete_project_data


@unittest.skipUnless(
    has_complete_project_data(),
    "Requires an authorized local image, annotation, and calibration dataset.",
)
class SvgExportTests(unittest.TestCase):
    BSCAN_X_ID = image_id_from_project_path("raw/脑膜/bscan_x-1.png")
    BSCAN_Y_MENINGES_ID = image_id_from_project_path("raw/脑膜/bscan_y-脑膜.png")
    PICTURE_1_ID = image_id_from_project_path("raw/标注图片/图片 1.png")

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = find_project_root(Path(__file__).resolve())

    def test_square_crop_expands_and_clamps(self) -> None:
        crop = square_crop_bounds(
            {"x_px": 0, "y_px": 10, "width_px": 20, "height_px": 40},
            800,
            180,
        )
        self.assertEqual(crop, (0.0, 10.0, 40.0, 40.0))

    def test_short_measurements_use_smaller_nonoverlapping_heads(self) -> None:
        short_head = _adaptive_arrow_head_length(60, 12, 48)
        long_head = _adaptive_arrow_head_length(400, 12, 48)
        self.assertLess(short_head, long_head)
        self.assertLess(short_head * 2, 60)
        self.assertEqual(long_head, 48)

    def test_annotation_without_roi_renders_global_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            for directory in ("config", "metadata", "annotations", "raw"):
                shutil.copytree(self.root / directory, temp_root / directory)
            outputs = render_svgs(temp_root, self.BSCAN_Y_MENINGES_ID)
            self.assertEqual(set(outputs), {"global"})
            self.assertEqual(
                outputs["global"].name,
                "脑膜__bscan_y-脑膜_global.svg",
            )

            global_svg = ET.parse(outputs["global"]).getroot()
            self.assertEqual(global_svg.attrib["width"], "12800")
            self.assertEqual(global_svg.attrib["height"], "2880")

            global_text = outputs["global"].read_text(encoding="utf-8")
            self.assertIn('inkscape:label="GLOBAL_IMAGE"', global_text)
            self.assertIn('inkscape:label="GLOBAL_LABELS"', global_text)
            self.assertNotIn('id="R001"', global_text)
            self.assertEqual(global_text.count(">200 µm</text>"), 2)
            self.assertEqual(
                global_text.count('data-label-side="outside"'), 2
            )
            self.assertNotIn("vector-effect", global_text)

    def test_ppt_extract_renders_on_content_canvas(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            for directory in ("config", "metadata", "annotations", "raw"):
                shutil.copytree(self.root / directory, temp_root / directory)
            outputs = render_svgs(temp_root, self.PICTURE_1_ID)
            self.assertEqual(set(outputs), {"global"})

            global_svg = ET.parse(outputs["global"]).getroot()
            self.assertEqual(global_svg.attrib["width"], "18304")
            self.assertEqual(global_svg.attrib["height"], "4112")

            global_text = outputs["global"].read_text(encoding="utf-8")
            metadata = json.loads(
                global_svg.find("{http://www.w3.org/2000/svg}metadata").text
            )
            self.assertEqual(metadata["content_width_px"], 1144)
            self.assertEqual(metadata["content_height_px"], 257)
            self.assertAlmostEqual(metadata["source_x_um_per_px"], 4.589151328671329)

    def test_fiji_import_renders_roi_measurement_and_inset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            for directory in ("config", "metadata", "annotations", "raw"):
                shutil.copytree(self.root / directory, temp_root / directory)
            annotation_path = temp_root / "annotations" / f"{self.BSCAN_X_ID}.json"
            source_annotation = json.loads(
                annotation_path.read_text(encoding="utf-8")
            )
            source_annotation["status"] = "in_progress"
            annotation_path.write_text(
                json.dumps(source_annotation, ensure_ascii=False),
                encoding="utf-8",
            )

            fixture = (
                self.root / "tests" / "fixtures" / "bscan_x-1.objects.csv"
            )
            image_id, measurement_count, roi_count = import_fiji_objects(
                temp_root, fixture
            )
            self.assertEqual(image_id, self.BSCAN_X_ID)
            self.assertEqual(measurement_count, 1)
            self.assertEqual(roi_count, 1)

            annotation = json.loads(
                (temp_root / "annotations" / f"{self.BSCAN_X_ID}.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertGreater(
                annotation["measurements"][0]["calculated"]["distance_um"], 65
            )
            annotation["reference_lines"] = [
                {
                    "id": "L001",
                    "p1_px": [260, 90],
                    "p2_px": [360, 70],
                }
            ]
            (temp_root / "annotations" / f"{self.BSCAN_X_ID}.json").write_text(
                json.dumps(annotation, ensure_ascii=False),
                encoding="utf-8",
            )

            outputs = render_svgs(temp_root, image_id)
            global_text = outputs["global"].read_text(encoding="utf-8")
            crop_text = outputs["crop"].read_text(encoding="utf-8")
            self.assertIn('id="T001"', global_text)
            self.assertIn('id="R001"', global_text)
            self.assertNotIn(">R001</text>", global_text)
            self.assertIn('id="T001"', crop_text)
            self.assertIn('id="L001"', global_text)
            self.assertIn('id="L001"', crop_text)
            self.assertIn('inkscape:label="GLOBAL_REFERENCE"', global_text)
            self.assertIn('inkscape:label="CROP_REFERENCE"', crop_text)
            self.assertIn('data-geometry="infinite-line"', crop_text)
            self.assertIn('class="reference-line"', crop_text)
            self.assertIn('id="crop-source"', crop_text)
            self.assertNotIn("等待 ROI", crop_text)
            self.assertIn('class="measurement-outline"', global_text)
            self.assertIn('class="measurement-head"', global_text)
            self.assertIn('class="measurement-head-outline"', global_text)
            self.assertNotIn('class="measurement-halo"', global_text)
            self.assertNotIn('class="measurement-head-halo"', global_text)
            self.assertIn('data-shaft-inset="', global_text)
            self.assertIn('data-head-length="', global_text)
            self.assertIn('data-arrow-direction="outward"', global_text)
            self.assertNotIn("<marker", global_text)
            self.assertIn('stroke: #000000;', global_text)
            self.assertIn('stroke: #ffffff;', global_text)
            self.assertIn("font-size: 144px;", global_text)
            self.assertIn("fill: #ffff00;", global_text)
            self.assertIn("font-size: 96px;", crop_text)
            self.assertIn('xlink:href="data:image/png;base64,', global_text)
            self.assertIn('inkscape:label="GLOBAL_LABELS"', global_text)
            self.assertIn('data-placement="auto-avoid"', global_text)

    def test_shared_foot_group_renders_common_foot_and_refractive_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            for directory in ("config", "metadata", "annotations", "raw"):
                shutil.copytree(self.root / directory, temp_root / directory)
            annotation_path = temp_root / "annotations" / f"{self.BSCAN_X_ID}.json"
            annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
            calibration = Calibration.from_project_config(load_config(temp_root))
            first = {
                "id": "T001",
                "kind": "shared_foot_distance",
                "group_id": "G001",
                "group_member": "A",
                "p1_px": [300, 70],
                "p2_px": [300, 90],
                "views": ["main"],
                "calculated": calibration.measurement((300, 70), (300, 90)),
            }
            second = {
                "id": "T002",
                "kind": "shared_foot_distance",
                "group_id": "G001",
                "group_member": "B",
                "p1_px": [300, 100],
                "p2_px": [300, 90],
                "views": ["main"],
                "calculated": calibration.measurement((300, 100), (300, 90)),
            }
            annotation["status"] = "in_progress"
            annotation["measurements"] = [first, second]
            annotation["reference_lines"] = [
                {"id": "L001", "p1_px": [200, 90], "p2_px": [400, 90]}
            ]
            annotation["refractive_index_groups"] = [
                {
                    "id": "G001",
                    "reference_id": "L001",
                    "measurement_ids": ["T001", "T002"],
                    "foot_px": [300, 90],
                    "calculated": refractive_index_calculation(
                        first["calculated"]["distance_um"],
                        second["calculated"]["distance_um"],
                    ),
                }
            ]
            annotation["rois"] = []
            annotation_path.write_text(
                json.dumps(annotation, ensure_ascii=False),
                encoding="utf-8",
            )

            outputs = render_svgs(temp_root, self.BSCAN_X_ID)
            self.assertEqual(set(outputs), {"global"})
            global_text = outputs["global"].read_text(encoding="utf-8")
            self.assertIn('id="G001_foot"', global_text)
            self.assertIn('id="G001_label"', global_text)
            self.assertIn(">n = 1.500</text>", global_text)
            self.assertEqual(
                global_text.count('data-arrow-direction="point-to-foot"'),
                2,
            )


if __name__ == "__main__":
    unittest.main()
