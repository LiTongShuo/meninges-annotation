import unittest
from pathlib import Path

from meninges_annotation.calibration import Calibration
from meninges_annotation.image_geometry import (
    calibration_for_content,
    content_bounds,
    detect_white_borders,
)
from meninges_annotation.project import (
    find_project_root,
    image_id_from_project_path,
    manifest_by_id,
)
from project_data import has_complete_project_data


@unittest.skipUnless(
    has_complete_project_data(),
    "Requires an authorized local image, annotation, and calibration dataset.",
)
class ImageGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = find_project_root(Path(__file__).resolve())

    def test_detect_white_borders_matches_ppt_extracts(self) -> None:
        raw = self.root / "raw" / "标注图片"
        self.assertEqual(detect_white_borders(raw / "图片 1.png"), (0, 0, 1, 1))
        self.assertEqual(detect_white_borders(raw / "图片 10.png"), (0, 4, 1, 1))
        self.assertEqual(detect_white_borders(raw / "图片 9.png"), (0, 5, 1, 1))

    def test_content_bounds_reads_manifest_fields(self) -> None:
        row = manifest_by_id(self.root)[
            image_id_from_project_path("raw/标注图片/图片 1.png")
        ]
        self.assertEqual(content_bounds(row), (0, 0, 1, 1, 1144, 257))

    def test_calibration_scales_with_content_dimensions(self) -> None:
        base = Calibration(
            calibration_id="batch-default-v2",
            x_um_per_px=6.5624864,
            y_um_per_px=10.34538099670395,
            display_scale=16,
            display_x_um_per_px=0.4101554,
            display_y_um_per_px=0.6465863122939969,
        )
        scaled = calibration_for_content(base, 800, 180, 1144, 257)
        self.assertAlmostEqual(scaled.x_um_per_px, 4.589151328671329)
        self.assertAlmostEqual(scaled.y_um_per_px, 7.245792137769303)
        self.assertAlmostEqual(
            scaled.x_um_per_px,
            scaled.display_x_um_per_px * scaled.display_scale,
        )
        self.assertAlmostEqual(
            scaled.y_um_per_px,
            scaled.display_y_um_per_px * scaled.display_scale,
        )


if __name__ == "__main__":
    unittest.main()
