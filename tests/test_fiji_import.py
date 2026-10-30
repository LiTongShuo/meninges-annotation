import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from meninges_annotation.calibration import Calibration
from meninges_annotation.fiji_import import (
    import_fiji_objects,
    recalculate_annotation,
)
from meninges_annotation.project import find_project_root, load_config
from meninges_annotation.project import image_id_from_project_path
from project_data import has_complete_project_data


CSV_FIELDS = [
    "image_id",
    "object_id",
    "type",
    "x1_px",
    "y1_px",
    "x2_px",
    "y2_px",
    "x_px",
    "y_px",
    "width_px",
    "height_px",
    "preview_value_um",
    "measurement_kind",
    "group_id",
    "group_member",
    "refractive_index",
]

BSCAN_X_ID = image_id_from_project_path("raw/脑膜/bscan_x-1.png")
BSCAN_Y_MENINGES_ID = image_id_from_project_path("raw/脑膜/bscan_y-脑膜.png")
PICTURE_1_ID = image_id_from_project_path("raw/标注图片/图片 1.png")


@unittest.skipUnless(
    has_complete_project_data(),
    "Requires an authorized local image, annotation, and calibration dataset.",
)
class FijiImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = find_project_root(Path(__file__).resolve())

    def _copy_project(self, destination: Path) -> None:
        for directory in ("config", "metadata", "annotations"):
            shutil.copytree(self.root / directory, destination / directory)
        self._make_annotation_editable(destination, BSCAN_X_ID)

    def _make_annotation_editable(
        self, destination: Path, image_id: str
    ) -> None:
        annotation_path = destination / "annotations" / f"{image_id}.json"
        annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
        annotation["status"] = "in_progress"
        annotation_path.write_text(
            json.dumps(annotation, ensure_ascii=False),
            encoding="utf-8",
        )

    def _measurement(
        self,
        object_id: str = "T001",
        p1: tuple[float, float] = (300, 100),
        p2: tuple[float, float] = (302, 90),
        image_id: str = BSCAN_X_ID,
    ) -> dict[str, object]:
        return {
            "image_id": image_id,
            "object_id": object_id,
            "type": "measurement",
            "x1_px": p1[0],
            "y1_px": p1[1],
            "x2_px": p2[0],
            "y2_px": p2[1],
        }

    def _roi(
        self,
        object_id: str = "R001",
        x: float = 270,
        y: float = 40,
        width: float = 80,
        height: float = 80,
        image_id: str = BSCAN_X_ID,
    ) -> dict[str, object]:
        return {
            "image_id": image_id,
            "object_id": object_id,
            "type": "roi",
            "x_px": x,
            "y_px": y,
            "width_px": width,
            "height_px": height,
        }

    def _reference_line(
        self,
        object_id: str = "L001",
        p1: tuple[float, float] = (260, 90),
        p2: tuple[float, float] = (360, 70),
        image_id: str = BSCAN_X_ID,
    ) -> dict[str, object]:
        return {
            "image_id": image_id,
            "object_id": object_id,
            "type": "reference_line",
            "x1_px": p1[0],
            "y1_px": p1[1],
            "x2_px": p2[0],
            "y2_px": p2[1],
        }

    def _shared_measurement(
        self,
        object_id: str,
        point: tuple[float, float],
        foot: tuple[float, float],
        member: str,
        group_id: str = "G001",
    ) -> dict[str, object]:
        row = self._measurement(object_id, point, foot)
        row.update(
            {
                "measurement_kind": "shared_foot_distance",
                "group_id": group_id,
                "group_member": member,
            }
        )
        return row

    def _write_csv(
        self,
        path: Path,
        rows: list[dict[str, object]],
        encoding: str = "utf-8",
    ) -> None:
        with path.open("w", encoding=encoding, newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def test_gb18030_csv_with_chinese_image_id_is_imported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            self._copy_project(temp_root)
            image_id = BSCAN_Y_MENINGES_ID
            annotation_path = temp_root / "annotations" / f"{image_id}.json"
            annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
            annotation["status"] = "awaiting_annotation"
            annotation["measurements"] = []
            annotation["rois"] = []
            annotation_path.write_text(
                json.dumps(annotation, ensure_ascii=False),
                encoding="utf-8",
            )
            csv_path = temp_root / "objects.csv"
            self._write_csv(
                csv_path,
                [
                    self._measurement(image_id=image_id),
                    self._roi(image_id=image_id),
                ],
                encoding="gb18030",
            )

            result = import_fiji_objects(temp_root, csv_path)

            self.assertEqual(result, (image_id, 1, 1))
            annotation = json.loads(
                annotation_path.read_text(encoding="utf-8")
            )
            self.assertEqual(annotation["status"], "in_progress")
            self.assertEqual(annotation["measurements"][0]["id"], "T001")

    def test_gb18030_csv_that_is_also_valid_utf8_is_imported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            self._copy_project(temp_root)
            image_id = PICTURE_1_ID
            self._make_annotation_editable(temp_root, image_id)
            csv_path = temp_root / "objects.csv"
            self._write_csv(
                csv_path,
                [self._measurement(image_id=image_id)],
                encoding="gb18030",
            )

            result = import_fiji_objects(temp_root, csv_path)

            self.assertEqual(result, (image_id, 1, 0))
            annotation = json.loads(
                (temp_root / "annotations" / f"{image_id}.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(annotation["status"], "in_progress")
            self.assertEqual(annotation["measurements"][0]["id"], "T001")

    def test_utf8_csv_with_chinese_image_id_is_imported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            self._copy_project(temp_root)
            image_id = PICTURE_1_ID
            self._make_annotation_editable(temp_root, image_id)
            csv_path = temp_root / "objects.csv"
            self._write_csv(
                csv_path,
                [self._measurement(image_id=image_id)],
                encoding="utf-8",
            )

            result = import_fiji_objects(temp_root, csv_path)

            self.assertEqual(result, (image_id, 1, 0))
            annotation = json.loads(
                (temp_root / "annotations" / f"{image_id}.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(annotation["status"], "in_progress")
            self.assertEqual(annotation["measurements"][0]["id"], "T001")

    def test_views_include_inset_only_when_both_endpoints_are_inside_r001(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            self._copy_project(temp_root)
            csv_path = temp_root / "objects.csv"
            self._write_csv(
                csv_path,
                [
                    self._measurement("T001"),
                    self._measurement("T002", (500, 100), (502, 90)),
                    self._roi(),
                ],
            )

            image_id, measurement_count, roi_count = import_fiji_objects(
                temp_root, csv_path
            )

            self.assertEqual((image_id, measurement_count, roi_count), (BSCAN_X_ID, 2, 1))
            annotation = json.loads(
                (temp_root / "annotations" / f"{BSCAN_X_ID}.json").read_text(
                    encoding="utf-8"
                )
            )
            views = {
                measurement["id"]: measurement["views"]
                for measurement in annotation["measurements"]
            }
            self.assertEqual(views["T001"], ["main", "inset"])
            self.assertEqual(views["T002"], ["main"])

    def test_reference_line_is_imported_as_independent_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            self._copy_project(temp_root)
            csv_path = temp_root / "objects.csv"
            self._write_csv(
                csv_path,
                [
                    self._measurement(),
                    self._reference_line(),
                    self._roi(),
                ],
            )

            self.assertEqual(
                import_fiji_objects(temp_root, csv_path),
                (BSCAN_X_ID, 1, 1),
            )
            annotation = json.loads(
                (temp_root / "annotations" / f"{BSCAN_X_ID}.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                annotation["reference_lines"],
                [
                    {
                        "id": "L001",
                        "p1_px": [260.0, 90.0],
                        "p2_px": [360.0, 70.0],
                    }
                ],
            )

    def test_roi_only_export_is_imported_without_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            self._copy_project(temp_root)
            csv_path = temp_root / "objects.csv"
            self._write_csv(csv_path, [self._roi()])

            self.assertEqual(
                import_fiji_objects(temp_root, csv_path),
                (BSCAN_X_ID, 0, 1),
            )
            annotation = json.loads(
                (temp_root / "annotations" / f"{BSCAN_X_ID}.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(annotation["measurements"], [])
            self.assertEqual(annotation["reference_lines"], [])
            self.assertEqual(len(annotation["rois"]), 1)

    def test_measurements_import_without_optional_roi(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            self._copy_project(temp_root)
            csv_path = temp_root / "objects.csv"
            self._write_csv(csv_path, [self._measurement()])

            self.assertEqual(
                import_fiji_objects(temp_root, csv_path),
                (BSCAN_X_ID, 1, 0),
            )
            annotation = json.loads(
                (temp_root / "annotations" / f"{BSCAN_X_ID}.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(annotation["rois"], [])
            self.assertEqual(annotation["measurements"][0]["views"], ["main"])

    def test_ppt_extract_measurement_uses_content_calibration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            self._copy_project(temp_root)
            self._make_annotation_editable(temp_root, PICTURE_1_ID)
            csv_path = temp_root / "图片 1.objects.csv"
            self._write_csv(
                csv_path,
                [self._measurement(p1=(100, 50), p2=(200, 50), image_id=PICTURE_1_ID)],
            )

            image_id, measurement_count, roi_count = import_fiji_objects(
                temp_root, csv_path
            )
            self.assertEqual(image_id, PICTURE_1_ID)
            self.assertEqual(measurement_count, 1)
            self.assertEqual(roi_count, 0)

            annotation = json.loads(
                (temp_root / "annotations" / f"{PICTURE_1_ID}.json").read_text(
                    encoding="utf-8"
                )
            )
            calculated = annotation["measurements"][0]["calculated"]
            self.assertAlmostEqual(calculated["dx_um"], 100 * 4.589151328671329)
            self.assertAlmostEqual(calculated["dy_um"], 0)
            self.assertAlmostEqual(
                calculated["distance_um"], 100 * 4.589151328671329
            )

    def test_shared_foot_group_calculates_refractive_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            self._copy_project(temp_root)
            csv_path = temp_root / "objects.csv"
            self._write_csv(
                csv_path,
                [
                    self._reference_line(p1=(200, 90), p2=(400, 90)),
                    self._shared_measurement(
                        "T001", (300, 70), (300, 90), "A"
                    ),
                    self._shared_measurement(
                        "T002", (300, 100), (300, 90), "B"
                    ),
                ],
            )

            self.assertEqual(
                import_fiji_objects(temp_root, csv_path),
                (BSCAN_X_ID, 2, 0),
            )
            annotation = json.loads(
                (temp_root / "annotations" / f"{BSCAN_X_ID}.json").read_text(
                    encoding="utf-8"
                )
            )
            group = annotation["refractive_index_groups"][0]
            self.assertEqual(group["id"], "G001")
            self.assertEqual(group["foot_px"], [300.0, 90.0])
            self.assertAlmostEqual(
                group["calculated"]["refractive_index"],
                1.5,
            )

    def test_invalid_csv_is_rejected_before_history_or_annotation_write(self) -> None:
        invalid_cases = {
            "multiple ROIs": [self._measurement(), self._roi(), self._roi("R002")],
            "wrong ROI ID": [self._measurement(), self._roi("R002")],
            "non-square ROI": [self._measurement(), self._roi(width=80, height=60)],
            "out-of-bounds ROI": [self._measurement(), self._roi(x=760)],
            "zero measurement": [
                self._measurement(p1=(300, 100), p2=(300, 100)),
                self._roi(),
            ],
            "wrong measurement ID": [self._measurement("M001"), self._roi()],
            "duplicate object ID": [
                self._measurement("T001"),
                self._measurement("T001", (310, 100), (312, 90)),
                self._roi(),
            ],
            "wrong reference ID": [
                self._measurement(),
                self._reference_line("L002"),
                self._roi(),
            ],
            "zero reference line": [
                self._measurement(),
                self._reference_line(p1=(300, 80), p2=(300, 80)),
                self._roi(),
            ],
        }

        for case_name, rows in invalid_cases.items():
            with self.subTest(case=case_name), tempfile.TemporaryDirectory() as temporary:
                temp_root = Path(temporary)
                self._copy_project(temp_root)
                annotation_path = temp_root / "annotations" / f"{BSCAN_X_ID}.json"
                original = annotation_path.read_bytes()
                history_dir = temp_root / "annotations" / "history"
                history_before = (
                    {
                        path.relative_to(history_dir): path.read_bytes()
                        for path in history_dir.rglob("*")
                        if path.is_file()
                    }
                    if history_dir.exists()
                    else {}
                )
                csv_path = temp_root / "objects.csv"
                self._write_csv(csv_path, rows)

                with self.assertRaises(ValueError):
                    import_fiji_objects(temp_root, csv_path)

                self.assertEqual(annotation_path.read_bytes(), original)
                history_after = (
                    {
                        path.relative_to(history_dir): path.read_bytes()
                        for path in history_dir.rglob("*")
                        if path.is_file()
                    }
                    if history_dir.exists()
                    else {}
                )
                self.assertEqual(history_after, history_before)

    def test_completed_annotation_rejects_import_and_recalculation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            self._copy_project(temp_root)
            annotation_path = temp_root / "annotations" / f"{BSCAN_X_ID}.json"
            annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
            annotation["status"] = "approved"
            annotation_path.write_text(
                json.dumps(annotation, ensure_ascii=False),
                encoding="utf-8",
            )
            original = annotation_path.read_bytes()
            csv_path = temp_root / "objects.csv"
            self._write_csv(csv_path, [self._measurement(), self._roi()])

            with self.assertRaisesRegex(ValueError, "completed and locked"):
                import_fiji_objects(temp_root, csv_path)
            self.assertEqual(annotation_path.read_bytes(), original)

            calibration = Calibration.from_project_config(
                load_config(temp_root)
            )
            with self.assertRaisesRegex(ValueError, "completed and locked"):
                recalculate_annotation(annotation, calibration)


if __name__ == "__main__":
    unittest.main()
