from copy import deepcopy
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from meninges_annotation.calibration import (
    Calibration,
    refractive_index_calculation,
)
from meninges_annotation.project import (
    MANIFEST_FIELDS,
    artifact_stem,
    atomic_write_json,
    complete_annotation,
    find_project_root,
    image_id_from_project_path,
    load_annotation,
    load_config,
    load_manifest,
    manifest_by_id,
    migrate_artifact_names,
    read_png_header,
    rename_raw_subfolder,
    sync_raw_images,
    unlock_annotation,
    validate_annotation,
    validate_project,
    write_manifest,
)
from project_data import has_complete_project_data


@unittest.skipUnless(
    has_complete_project_data(),
    "Requires an authorized local image, annotation, and calibration dataset.",
)
class ProjectTests(unittest.TestCase):
    BSCAN_X_ID = image_id_from_project_path("raw/脑膜/bscan_x-1.png")
    BSCAN_Y_ID = image_id_from_project_path("raw/脑膜/bscan_y-1.png")
    BSCAN_Y_MENINGES_ID = image_id_from_project_path("raw/脑膜/bscan_y-脑膜.png")

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = find_project_root(Path(__file__).resolve())

    @staticmethod
    def _image_id(project_path: str) -> str:
        return image_id_from_project_path(project_path)

    def test_project_validates(self) -> None:
        self.assertEqual(validate_project(self.root), [])

    def test_fiji_image_bounds_file_matches_content_fields(self) -> None:
        bounds = (self.root / "fiji" / "image_bounds.csv").read_text(
            encoding="utf-8"
        )
        self.assertIn("1145,258,0,0,1,1\n", bounds)
        self.assertIn("1145,262,0,4,1,1\n", bounds)
        self.assertIn("1145,263,0,5,1,1\n", bounds)

    def test_artifact_stem_includes_raw_subfolders(self) -> None:
        self.assertEqual(
            image_id_from_project_path("raw/脑膜/批次一/bscan_x-1.png"),
            "脑膜__批次一__bscan_x-1",
        )
        self.assertEqual(
            artifact_stem(
                {"project_path": "raw/脑膜/批次一/bscan_x-1.png"}
            ),
            "脑膜__批次一__bscan_x-1",
        )
        self.assertEqual(
            artifact_stem({"project_path": "raw/root_sample.png"}),
            "root_sample",
        )
        self.assertEqual(
            image_id_from_project_path("raw/a%folder/name__part.png"),
            "a%25folder__name_%5Fpart",
        )

    def test_sync_raw_migrates_annotation_history_and_fiji_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            shutil.copytree(self.root / "config", temp_root / "config")
            source_id = self.BSCAN_X_ID
            source_row = manifest_by_id(self.root)[source_id]
            image_path = temp_root / "raw" / "旧分类" / "sample.png"
            image_path.parent.mkdir(parents=True)
            shutil.copy2(self.root / source_row["project_path"], image_path)

            row = dict(source_row)
            row.update(
                {
                    "image_id": "sample",
                    "file_name": "sample.png",
                    "source_path": str(image_path.resolve()),
                    "project_path": "raw/旧分类/sample.png",
                }
            )
            write_manifest(temp_root, [row])

            annotation = {
                "image_id": "sample",
                "image_sha256": row["sha256"],
                "revision": 1,
            }
            atomic_write_json(
                temp_root / "annotations" / "sample.json",
                annotation,
            )
            atomic_write_json(
                temp_root / "annotations" / "history" / "sample.r1.json",
                annotation,
            )
            csv_path = temp_root / "fiji" / "exports" / "sample.objects.csv"
            csv_path.parent.mkdir(parents=True)
            csv_path.write_text(
                "image_id,object_id\nsample,T001\n",
                encoding="utf-8",
            )
            master_path = (
                temp_root / "layouts" / "masters" / "sample_global.svg"
            )
            master_path.parent.mkdir(parents=True)
            master_path.write_text("sample", encoding="utf-8")
            roi_zip = temp_root / "fiji" / "rois" / "sample.zip"
            roi_zip.parent.mkdir(parents=True)
            with zipfile.ZipFile(roi_zip, "w") as archive:
                archive.writestr("sample__L001.roi", b"roi")

            result = sync_raw_images(temp_root)
            new_id = "旧分类__sample"
            self.assertIn(("sample", new_id), result.renamed)
            self.assertFalse((temp_root / "annotations" / "sample.json").exists())
            self.assertTrue((temp_root / "annotations" / f"{new_id}.json").exists())
            self.assertTrue(
                (temp_root / "annotations" / "history" / f"{new_id}.r1.json").exists()
            )
            self.assertEqual(
                load_annotation(temp_root, new_id)["image_id"],
                new_id,
            )
            migrated_csv = (
                temp_root / "fiji" / "exports" / f"{new_id}.objects.csv"
            )
            self.assertIn(f"{new_id},T001", migrated_csv.read_text(encoding="utf-8"))
            self.assertTrue(
                (
                    temp_root
                    / "layouts"
                    / "masters"
                    / f"{new_id}_global.svg"
                ).exists()
            )
            migrated_roi_zip = temp_root / "fiji" / "rois" / f"{new_id}.zip"
            with zipfile.ZipFile(migrated_roi_zip) as archive:
                self.assertEqual(archive.namelist(), [f"{new_id}__L001.roi"])

    def test_migrate_artifact_names_renames_and_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            (temp_root / "metadata").mkdir()
            shutil.copy2(
                self.root / "metadata" / "image_manifest.csv",
                temp_root / "metadata" / "image_manifest.csv",
            )
            migrated_rows = load_manifest(temp_root)
            for row in migrated_rows:
                if row["project_path"] == "raw/脑膜/bscan_x-1.png":
                    row["image_id"] = "bscan_x-1"
            write_manifest(temp_root, migrated_rows)
            master_dir = temp_root / "layouts" / "masters"
            export_dir = temp_root / "exports" / "png"
            master_dir.mkdir(parents=True)
            export_dir.mkdir(parents=True)

            legacy_master = master_dir / "bscan_x-1_global.svg"
            migrated_master = master_dir / "脑膜__bscan_x-1_global.svg"
            legacy_master.write_text("same SVG", encoding="utf-8")
            migrated_master.write_text("same SVG", encoding="utf-8")
            legacy_export = export_dir / "bscan_x-1_crop.png"
            legacy_export.write_bytes(b"PNG")

            result = migrate_artifact_names(temp_root)

            self.assertFalse(legacy_master.exists())
            self.assertTrue(migrated_master.exists())
            self.assertFalse(legacy_export.exists())
            self.assertEqual(
                (export_dir / "脑膜__bscan_x-1_crop.png").read_bytes(),
                b"PNG",
            )
            self.assertEqual(len(result.renamed), 1)
            self.assertEqual(len(result.duplicates_removed), 1)

    def test_rename_raw_subfolder_preserves_artifact_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            (temp_root / "metadata").mkdir()
            source_dir = temp_root / "raw" / "父目录" / "旧分类" / "批次一"
            source_dir.mkdir(parents=True)
            (source_dir / "sample.png").write_bytes(b"raw image")

            row = {field: "" for field in MANIFEST_FIELDS}
            row.update(
                {
                    "image_id": "sample",
                    "file_name": "sample.png",
                    "project_path": "raw/父目录/旧分类/批次一/sample.png",
                }
            )
            write_manifest(temp_root, [row])

            master_dir = temp_root / "layouts" / "masters"
            wps_dir = temp_root / "exports" / "vector" / "wps"
            master_dir.mkdir(parents=True)
            wps_dir.mkdir(parents=True)
            old_stem = "父目录__旧分类__批次一__sample"
            new_stem = "父目录__新分类__批次一__sample"
            (master_dir / f"{old_stem}_global.svg").write_text(
                "hand arranged SVG",
                encoding="utf-8",
            )
            (wps_dir / f"{old_stem}_crop_wps.svg").write_text(
                "WPS output",
                encoding="utf-8",
            )

            result = rename_raw_subfolder(
                temp_root,
                "父目录/旧分类",
                "新分类",
            )

            self.assertFalse((temp_root / "raw" / "父目录" / "旧分类").exists())
            self.assertTrue(
                (
                    temp_root
                    / "raw"
                    / "父目录"
                    / "新分类"
                    / "批次一"
                    / "sample.png"
                ).is_file()
            )
            self.assertEqual(
                load_manifest(temp_root)[0]["project_path"],
                "raw/父目录/新分类/批次一/sample.png",
            )
            self.assertEqual(
                (master_dir / f"{new_stem}_global.svg").read_text(
                    encoding="utf-8"
                ),
                "hand arranged SVG",
            )
            self.assertEqual(
                (wps_dir / f"{new_stem}_crop_wps.svg").read_text(
                    encoding="utf-8"
                ),
                "WPS output",
            )
            self.assertEqual(
                result.image_ids,
                ("父目录__新分类__批次一__sample",),
            )
            self.assertEqual(len(result.artifacts_renamed), 2)

    def test_rename_raw_subfolder_does_not_overwrite_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            (temp_root / "metadata").mkdir()
            source_dir = temp_root / "raw" / "旧分类"
            source_dir.mkdir(parents=True)
            (source_dir / "sample.png").write_bytes(b"raw image")
            row = {field: "" for field in MANIFEST_FIELDS}
            row.update(
                {
                    "image_id": "sample",
                    "file_name": "sample.png",
                    "project_path": "raw/旧分类/sample.png",
                }
            )
            write_manifest(temp_root, [row])
            master_dir = temp_root / "layouts" / "masters"
            master_dir.mkdir(parents=True)
            old_artifact = master_dir / "旧分类__sample_global.svg"
            new_artifact = master_dir / "新分类__sample_global.svg"
            old_artifact.write_text("manual old", encoding="utf-8")
            new_artifact.write_text("different target", encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError,
                "target already exists",
            ):
                rename_raw_subfolder(temp_root, "旧分类", "新分类")

            self.assertTrue(source_dir.is_dir())
            self.assertEqual(
                old_artifact.read_text(encoding="utf-8"),
                "manual old",
            )
            self.assertEqual(
                new_artifact.read_text(encoding="utf-8"),
                "different target",
            )
            self.assertEqual(
                load_manifest(temp_root)[0]["project_path"],
                "raw/旧分类/sample.png",
            )

    def test_all_samples_are_800_by_180_rgb(self) -> None:
        manifest = manifest_by_id(self.root)
        for image_id in (
            self.BSCAN_X_ID,
            self.BSCAN_Y_ID,
            self.BSCAN_Y_MENINGES_ID,
        ):
            header = read_png_header(
                self.root / manifest[image_id]["project_path"]
            )
            self.assertEqual(header["width"], 800)
            self.assertEqual(header["height"], 180)
            self.assertEqual(header["bit_depth"], 8)
            self.assertEqual(header["color_type"], "RGB")

    def test_sync_raw_registers_nested_images_and_tracks_moves(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            (temp_root / "config").mkdir()
            (temp_root / "metadata").mkdir()
            (temp_root / "annotations").mkdir()
            first_folder = temp_root / "raw" / "第一组"
            first_folder.mkdir(parents=True)
            shutil.copy2(
                self.root
                / manifest_by_id(self.root)[
                    self._image_id("raw/脑膜/bscan_x-1.png")
                ]["project_path"],
                first_folder / "nested_sample.png",
            )
            shutil.copy2(
                self.root / "config" / "project.json",
                temp_root / "config" / "project.json",
            )
            (temp_root / "metadata" / "image_manifest.csv").write_text(
                ",".join(MANIFEST_FIELDS) + "\n",
                encoding="utf-8",
            )

            first = sync_raw_images(temp_root)
            self.assertEqual(first.added, ("第一组__nested_sample",))
            self.assertEqual(first.initialized, ("第一组__nested_sample",))
            row = load_manifest(temp_root)[0]
            self.assertEqual(
                row["project_path"],
                "raw/第一组/nested_sample.png",
            )
            self.assertEqual(
                load_annotation(temp_root, "第一组__nested_sample")["image_id"],
                "第一组__nested_sample",
            )

            second_folder = temp_root / "raw" / "第二组"
            second_folder.mkdir()
            (first_folder / "nested_sample.png").replace(
                second_folder / "nested_sample.png"
            )
            second = sync_raw_images(temp_root)
            self.assertEqual(second.added, ())
            self.assertEqual(second.renamed, (("第一组__nested_sample", "第二组__nested_sample"),))
            self.assertEqual(
                load_manifest(temp_root)[0]["project_path"],
                "raw/第二组/nested_sample.png",
            )

    def test_sync_raw_allows_duplicate_file_names_in_different_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            shutil.copytree(self.root / "config", temp_root / "config")
            (temp_root / "metadata").mkdir()
            (temp_root / "annotations").mkdir()
            for folder in ("甲", "乙"):
                target = temp_root / "raw" / folder
                target.mkdir(parents=True)
                shutil.copy2(
                    self.root
                    / manifest_by_id(self.root)[
                        self._image_id("raw/脑膜/bscan_x-1.png")
                    ]["project_path"],
                    target / "same_name.png",
                )
            (temp_root / "metadata" / "image_manifest.csv").write_text(
                ",".join(MANIFEST_FIELDS) + "\n",
                encoding="utf-8",
            )

            result = sync_raw_images(temp_root)
            self.assertEqual(
                set(result.added),
                {"甲__same_name", "乙__same_name"},
            )
            self.assertEqual(
                {row["image_id"] for row in load_manifest(temp_root)},
                {"甲__same_name", "乙__same_name"},
            )

    def test_complete_annotation_locks_a_valid_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            for directory in ("config", "metadata", "annotations", "raw"):
                shutil.copytree(self.root / directory, temp_root / directory)

            draft = load_annotation(temp_root, self.BSCAN_X_ID)
            draft["status"] = "in_progress"
            atomic_write_json(
                temp_root / "annotations" / f"{self.BSCAN_X_ID}.json",
                draft,
            )
            previous_revision = draft["revision"]
            changed, revision = complete_annotation(temp_root, self.BSCAN_X_ID)
            self.assertTrue(changed)
            self.assertEqual(revision, previous_revision + 1)
            annotation = load_annotation(temp_root, self.BSCAN_X_ID)
            self.assertEqual(annotation["status"], "approved")
            self.assertTrue(
                (
                    temp_root
                    / "annotations"
                    / "history"
                    / f"{self.BSCAN_X_ID}.r{previous_revision}.json"
                ).is_file()
            )

            changed_again, same_revision = complete_annotation(
                temp_root, self.BSCAN_X_ID
            )
            self.assertFalse(changed_again)
            self.assertEqual(same_revision, revision)

    def test_unlock_annotation_restores_editing_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            for directory in ("config", "metadata", "annotations", "raw"):
                shutil.copytree(self.root / directory, temp_root / directory)

            _, locked_revision = complete_annotation(temp_root, self.BSCAN_X_ID)
            changed, revision = unlock_annotation(temp_root, self.BSCAN_X_ID)
            self.assertTrue(changed)
            self.assertEqual(revision, locked_revision + 1)
            annotation = load_annotation(temp_root, self.BSCAN_X_ID)
            self.assertEqual(annotation["status"], "in_progress")
            self.assertEqual(
                annotation["history"][-1]["reason"],
                "annotation unlocked for editing",
            )
            self.assertTrue(
                (
                    temp_root
                    / "annotations"
                    / "history"
                    / f"{self.BSCAN_X_ID}.r{locked_revision}.json"
                ).is_file()
            )

            changed_again, same_revision = unlock_annotation(
                temp_root, self.BSCAN_X_ID
            )
            self.assertFalse(changed_again)
            self.assertEqual(same_revision, revision)

    def test_annotation_without_optional_r001_can_be_completed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            for directory in ("config", "metadata", "annotations", "raw"):
                shutil.copytree(self.root / directory, temp_root / directory)

            changed, _revision = complete_annotation(
                temp_root, self.BSCAN_Y_MENINGES_ID
            )
            self.assertTrue(changed)
            self.assertEqual(
                load_annotation(temp_root, self.BSCAN_Y_MENINGES_ID)["status"],
                "approved",
            )

    def test_roi_only_annotation_can_be_completed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            for directory in ("config", "metadata", "annotations", "raw"):
                shutil.copytree(self.root / directory, temp_root / directory)

            annotation = load_annotation(temp_root, self.BSCAN_Y_MENINGES_ID)
            annotation["status"] = "in_progress"
            annotation["measurements"] = []
            annotation["reference_lines"] = []
            annotation["rois"] = [
                {
                    "id": "R001",
                    "x_px": 270,
                    "y_px": 40,
                    "width_px": 80,
                    "height_px": 80,
                }
            ]
            atomic_write_json(
                temp_root / "annotations" / f"{self.BSCAN_Y_MENINGES_ID}.json",
                annotation,
            )

            changed, _revision = complete_annotation(
                temp_root, self.BSCAN_Y_MENINGES_ID
            )
            self.assertTrue(changed)
            self.assertEqual(
                load_annotation(temp_root, self.BSCAN_Y_MENINGES_ID)["status"],
                "approved",
            )

    def _annotation_context(self):
        annotation = load_annotation(self.root, self.BSCAN_X_ID)
        manifest = manifest_by_id(self.root)[self.BSCAN_X_ID]
        config = load_config(self.root)
        return annotation, manifest, config

    def _valid_completed_annotation(self):
        annotation, manifest, config = self._annotation_context()
        annotation["status"] = "in_progress"
        annotation["rois"] = [
            {
                "id": "R001",
                "x_px": 270,
                "y_px": 40,
                "width_px": 80,
                "height_px": 80,
            }
        ]
        annotation["measurements"] = [
            {
                "id": "T001",
                "p1_px": [300, 100],
                "p2_px": [302, 90],
                "views": ["main", "inset"],
            }
        ]
        return annotation, manifest, config

    def test_awaiting_annotation_empty_draft_remains_valid(self) -> None:
        annotation, manifest, config = self._annotation_context()
        self.assertEqual(validate_annotation(annotation, manifest, config), [])

    def test_valid_completed_annotation_passes(self) -> None:
        annotation, manifest, config = self._valid_completed_annotation()
        self.assertEqual(validate_annotation(annotation, manifest, config), [])

    def test_object_ids_must_be_unique_across_measurements_and_rois(self) -> None:
        annotation, manifest, config = self._valid_completed_annotation()
        duplicate = deepcopy(annotation["measurements"][0])
        annotation["measurements"].append(duplicate)
        errors = validate_annotation(annotation, manifest, config)
        self.assertTrue(any("duplicate object ID T001" in error for error in errors))

    def test_roi_id_and_square_shape_are_validated(self) -> None:
        annotation, manifest, config = self._valid_completed_annotation()
        annotation["rois"][0]["id"] = "R002"
        annotation["rois"][0]["height_px"] = 60
        errors = validate_annotation(annotation, manifest, config)
        self.assertTrue(any("ROI ID must be R001" in error for error in errors))
        self.assertTrue(any("ROI must be square" in error for error in errors))

    def test_reference_line_id_bounds_and_length_are_validated(self) -> None:
        annotation, manifest, config = self._valid_completed_annotation()
        annotation["reference_lines"] = [
            {
                "id": "L002",
                "p1_px": [-1, 80],
                "p2_px": [-1, 80],
            }
        ]
        errors = validate_annotation(annotation, manifest, config)
        self.assertTrue(any("reference line ID must be L001" in error for error in errors))
        self.assertTrue(any("p1_px is outside" in error for error in errors))
        self.assertTrue(any("reference line has zero length" in error for error in errors))

    def test_measurement_id_nonzero_length_and_views_are_validated(self) -> None:
        annotation, manifest, config = self._valid_completed_annotation()
        measurement = annotation["measurements"][0]
        measurement["id"] = "M001"
        measurement["p2_px"] = measurement["p1_px"]
        measurement["views"] = ["inset", "inset"]
        errors = validate_annotation(annotation, manifest, config)
        self.assertTrue(any("measurement ID must match" in error for error in errors))
        self.assertTrue(any("measurement has zero length" in error for error in errors))
        self.assertTrue(any("views must contain main" in error for error in errors))

    def test_inset_view_requires_both_endpoints_inside_r001(self) -> None:
        annotation, manifest, config = self._valid_completed_annotation()
        annotation["measurements"][0]["p2_px"] = [500, 90]
        errors = validate_annotation(annotation, manifest, config)
        self.assertTrue(
            any("p2_px is outside R001 but views includes inset" in error for error in errors)
        )

    def test_shared_foot_group_geometry_and_calculation_are_validated(self) -> None:
        annotation, manifest, config = self._annotation_context()
        calibration = Calibration.from_project_config(config)
        first = {
            "id": "T001",
            "kind": "shared_foot_distance",
            "group_id": "G001",
            "group_member": "A",
            "p1_px": [300, 70],
            "p2_px": [300, 90],
            "views": ["main"],
        }
        second = {
            "id": "T002",
            "kind": "shared_foot_distance",
            "group_id": "G001",
            "group_member": "B",
            "p1_px": [300, 100],
            "p2_px": [300, 90],
            "views": ["main"],
        }
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
                    calibration.distance_um((300, 70), (300, 90)),
                    calibration.distance_um((300, 100), (300, 90)),
                ),
            }
        ]
        annotation["rois"] = []
        self.assertEqual(validate_annotation(annotation, manifest, config), [])

        annotation["reference_lines"][0]["p2_px"] = [400, 100]
        errors = validate_annotation(annotation, manifest, config)
        self.assertTrue(
            any("no longer" in error or "not perpendicular" in error for error in errors)
        )


if __name__ == "__main__":
    unittest.main()
