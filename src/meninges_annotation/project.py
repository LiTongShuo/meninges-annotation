from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import struct
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .calibration import (
    Calibration,
    refractive_index_calculation,
    shared_foot_geometry_errors,
)
from .image_geometry import (
    calibration_for_content,
    content_bounds,
    detect_white_borders,
    source_dimensions,
)


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_COLOR_TYPES = {
    0: "grayscale",
    2: "RGB",
    3: "indexed",
    4: "grayscale-alpha",
    6: "RGBA",
}
MEASUREMENT_ID_PATTERN = re.compile(r"^T\d+$")
GROUP_ID_PATTERN = re.compile(r"^G\d{3}$")
THICKNESS_KIND = "thickness"
SHARED_FOOT_KIND = "shared_foot_distance"
MANIFEST_FIELDS = (
    "image_id",
    "file_name",
    "source_path",
    "project_path",
    "width_px",
    "height_px",
    "content_width_px",
    "content_height_px",
    "crop_left_px",
    "crop_top_px",
    "crop_right_px",
    "crop_bottom_px",
    "pixel_format",
    "sha256",
    "calibration_id",
    "status",
)


@dataclass(frozen=True)
class RawSyncResult:
    discovered: int
    added: tuple[str, ...]
    moved: tuple[str, ...]
    initialized: tuple[str, ...]
    renamed: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ArtifactMigrationResult:
    renamed: tuple[tuple[str, str], ...]
    duplicates_removed: tuple[str, ...]


@dataclass(frozen=True)
class RawFolderRenameResult:
    old_folder: str
    new_folder: str
    image_ids: tuple[str, ...]
    artifacts_renamed: tuple[tuple[str, str], ...]


def find_project_root(start: Path | str | None = None) -> Path:
    current = Path(start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "config" / "project.json").is_file():
            return candidate
    raise FileNotFoundError("Could not find config/project.json")


def load_config(root: Path) -> dict[str, Any]:
    return json.loads((root / "config" / "project.json").read_text(encoding="utf-8"))


def load_manifest(root: Path) -> list[dict[str, str]]:
    with (root / "metadata" / "image_manifest.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        return list(csv.DictReader(handle))


def write_manifest(root: Path, rows: list[dict[str, str]]) -> None:
    path = root / "metadata" / "image_manifest.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8-sig",
        newline="",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in MANIFEST_FIELDS})
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _manifest_project_path(root: Path, image_path: Path) -> str:
    return image_path.relative_to(root).as_posix()


def image_id_from_project_path(project_path: str) -> str:
    """Build the globally unique ID for an image below raw/."""
    project_path = PurePosixPath(project_path.replace("\\", "/"))
    parts = project_path.parts
    if parts and parts[0].casefold() == "raw":
        parts = parts[1:]
    if not parts:
        raise ValueError("Manifest project_path must point to an image below raw/")

    components = (*parts[:-1], PurePosixPath(parts[-1]).stem)

    # ``__`` separates path components. Escape the same sequence within a
    # component so distinct relative paths remain distinct artifact names.
    def encode(component: str) -> str:
        return component.replace("%", "%25").replace("__", "_%5F")

    return "__".join(encode(component) for component in components)


def artifact_stem(manifest: dict[str, str]) -> str:
    """Build a generated-file stem that identifies the raw source path."""
    return image_id_from_project_path(manifest["project_path"])


def _artifact_rename_plan(
    root: Path,
    stem_pairs: list[tuple[str, str]],
) -> list[tuple[Path, Path]]:
    pairs = sorted(stem_pairs, key=lambda item: len(item[0]), reverse=True)
    artifact_roots = (root / "layouts" / "masters", root / "exports")
    planned: list[tuple[Path, Path]] = []
    for artifact_root in artifact_roots:
        if not artifact_root.is_dir():
            continue
        for source in artifact_root.rglob("*"):
            if not source.is_file():
                continue
            for old_stem, new_stem in pairs:
                if not source.name.startswith(old_stem):
                    continue
                suffix = source.name[len(old_stem) :]
                if not suffix or suffix[0] not in ("_", "."):
                    continue
                planned.append(
                    (source, source.with_name(f"{new_stem}{suffix}"))
                )
                break
    return planned


def _check_artifact_rename_conflicts(
    root: Path,
    planned: list[tuple[Path, Path]],
) -> None:
    conflicts = [
        f"{source.relative_to(root)} -> {target.relative_to(root)}"
        for source, target in planned
        if target.exists() and target != source
    ]
    if conflicts:
        raise ValueError(
            "Artifact rename target already exists: " + "; ".join(conflicts)
        )


def migrate_artifact_names(root: Path) -> ArtifactMigrationResult:
    """Rename existing generated artifacts to source-qualified file names."""
    rows = load_manifest(root)
    prefixes = [
        (row["image_id"], artifact_stem(row))
        for row in rows
        if row["image_id"] != artifact_stem(row)
    ]
    planned = _artifact_rename_plan(root, prefixes)

    conflicts: list[str] = []
    duplicates: list[tuple[Path, Path]] = []
    renames: list[tuple[Path, Path]] = []
    for source, target in planned:
        if not target.exists():
            renames.append((source, target))
        elif (
            source.stat().st_size == target.stat().st_size
            and sha256_file(source) == sha256_file(target)
        ):
            duplicates.append((source, target))
        else:
            conflicts.append(
                f"{source.relative_to(root)} -> {target.relative_to(root)}"
            )

    if conflicts:
        raise ValueError(
            "Artifact name migration found different files at target paths: "
            + "; ".join(conflicts)
        )

    for source, target in renames:
        source.rename(target)
    for source, _target in duplicates:
        source.unlink()

    return ArtifactMigrationResult(
        renamed=tuple(
            (
                source.relative_to(root).as_posix(),
                target.relative_to(root).as_posix(),
            )
            for source, target in renames
        ),
        duplicates_removed=tuple(
            source.relative_to(root).as_posix()
            for source, _target in duplicates
        ),
    )


def _id_file_rename_plan(
    root: Path,
    id_pairs: dict[str, str],
) -> list[tuple[Path, Path, str, str]]:
    """Plan annotation and Fiji file renames for an image-ID migration."""
    planned: list[tuple[Path, Path, str, str]] = []
    for old_id, new_id in id_pairs.items():
        if old_id == new_id:
            continue
        candidates = [
            (
                root / "annotations" / f"{old_id}.json",
                root / "annotations" / f"{new_id}.json",
            ),
            (
                root / "fiji" / "exports" / f"{old_id}.objects.csv",
                root / "fiji" / "exports" / f"{new_id}.objects.csv",
            ),
            (
                root / "fiji" / "measurements" / f"{old_id}.csv",
                root / "fiji" / "measurements" / f"{new_id}.csv",
            ),
            (
                root / "fiji" / "rois" / f"{old_id}.zip",
                root / "fiji" / "rois" / f"{new_id}.zip",
            ),
            (
                root / "fiji" / "recovery" / f"{old_id}.autosave.zip",
                root / "fiji" / "recovery" / f"{new_id}.autosave.zip",
            ),
        ]
        history_root = root / "annotations" / "history"
        if history_root.is_dir():
            candidates.extend(
                (
                    source,
                    source.with_name(f"{new_id}{source.name[len(old_id):]}"),
                )
                for source in history_root.rglob(f"{old_id}.r*.json")
            )
        planned.extend(
            (source, target, old_id, new_id)
            for source, target in candidates
            if source.is_file()
        )
    return planned


def _check_id_file_rename_conflicts(
    root: Path,
    planned: list[tuple[Path, Path, str, str]],
) -> None:
    targets: dict[Path, Path] = {}
    source_paths = {source for source, _target, _old, _new in planned}
    conflicts: list[str] = []
    for source, target, _old_id, _new_id in planned:
        previous = targets.get(target)
        if previous is not None and previous != source:
            conflicts.append(
                f"{previous.relative_to(root)} and {source.relative_to(root)} "
                f"both target {target.relative_to(root)}"
            )
        targets[target] = source
        if target.exists() and target != source and target not in source_paths:
            conflicts.append(
                f"{source.relative_to(root)} -> {target.relative_to(root)}"
            )
    if conflicts:
        raise ValueError(
            "Image ID migration target already exists: " + "; ".join(conflicts)
        )


def _rewrite_fiji_csv_image_id(path: Path, old_id: str, new_id: str) -> None:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"Unsupported Fiji CSV encoding: {path}")

    rows = list(csv.reader(text.splitlines()))
    for row in rows[1:]:
        if row and row[0] == old_id:
            row[0] = new_id
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        csv.writer(handle, lineterminator="\n").writerows(rows)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _rewrite_fiji_zip_entry_names(path: Path, old_id: str, new_id: str) -> None:
    prefix = old_id + "__"
    with tempfile.NamedTemporaryFile(
        "wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(
            temporary, "w"
        ) as target:
            for info in source.infolist():
                if info.filename == old_id or info.filename.startswith(prefix):
                    info.filename = new_id + info.filename[len(old_id) :]
                target.writestr(info, source.read(info))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _migrate_id_files(
    root: Path,
    id_pairs: dict[str, str],
) -> tuple[tuple[str, str], ...]:
    planned = _id_file_rename_plan(root, id_pairs)
    _check_id_file_rename_conflicts(root, planned)
    for source, target, old_id, new_id in planned:
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix.lower() == ".json":
            payload = json.loads(source.read_text(encoding="utf-8"))
            if payload.get("image_id") == old_id:
                payload["image_id"] = new_id
            atomic_write_json(target, payload)
            source.unlink()
        elif source.suffix.lower() == ".csv":
            source.rename(target)
            _rewrite_fiji_csv_image_id(target, old_id, new_id)
        elif source.suffix.lower() == ".zip":
            source.rename(target)
            _rewrite_fiji_zip_entry_names(target, old_id, new_id)
        else:
            source.rename(target)
    return tuple(
        (
            source.relative_to(root).as_posix(),
            target.relative_to(root).as_posix(),
        )
        for source, target, _old_id, _new_id in planned
    )


def migrate_image_ids(root: Path) -> tuple[tuple[str, str], ...]:
    """Upgrade legacy basename IDs to IDs derived from raw relative paths."""
    rows = load_manifest(root)
    id_pairs: dict[str, str] = {}
    new_ids: dict[str, str] = {}
    for row in rows:
        old_id = row.get("image_id", "")
        new_id = image_id_from_project_path(row["project_path"])
        if old_id != new_id:
            id_pairs[old_id] = new_id
        previous = new_ids.get(new_id)
        if previous is not None and previous != old_id:
            raise ValueError(
                f"Image ID migration collision: {previous} and {old_id} "
                f"both become {new_id}"
            )
        new_ids[new_id] = old_id

    if not id_pairs:
        return ()
    if len(id_pairs) != len({new_id for new_id in id_pairs.values()}):
        raise ValueError("Image ID migration produced duplicate target IDs")
    existing_ids = {row.get("image_id", "") for row in rows}
    unmigrated_targets = {
        new_id
        for old_id, new_id in id_pairs.items()
        if new_id in existing_ids and new_id not in id_pairs
    }
    if unmigrated_targets:
        raise ValueError(
            "Image ID migration target already exists: "
            + ", ".join(sorted(unmigrated_targets))
        )

    artifact_plan = _artifact_rename_plan(root, list(id_pairs.items()))
    _check_artifact_rename_conflicts(root, artifact_plan)
    _migrate_id_files(root, id_pairs)
    for source, target in artifact_plan:
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
    for row in rows:
        old_id = row["image_id"]
        if old_id in id_pairs:
            row["image_id"] = id_pairs[old_id]
    write_manifest(root, rows)
    return tuple(id_pairs.items())


def _migrate_moved_images(
    root: Path,
    rows: list[dict[str, str]],
    discovered: dict[str, Path],
) -> tuple[tuple[str, str], ...]:
    """Match an unambiguous moved image by its existing SHA-256."""
    known_ids = {row["image_id"] for row in rows}
    discovered_paths = {
        _manifest_project_path(root, image_path)
        for image_path in discovered.values()
    }
    unmatched = {
        image_id: path
        for image_id, path in discovered.items()
        if image_id not in known_ids
    }
    stale = [
        row
        for row in rows
        if row.get("project_path", "") not in discovered_paths
        and row.get("sha256", "")
    ]
    if not unmatched or not stale:
        return ()

    stale_by_hash: dict[str, list[dict[str, str]]] = {}
    for row in stale:
        stale_by_hash.setdefault(row["sha256"].upper(), []).append(row)
    unmatched_by_hash: dict[str, list[str]] = {}
    for image_id, path in unmatched.items():
        unmatched_by_hash.setdefault(sha256_file(path), []).append(image_id)

    id_pairs: dict[str, str] = {}
    new_paths: dict[str, str] = {}
    for image_hash, new_ids in unmatched_by_hash.items():
        candidates = stale_by_hash.get(image_hash, [])
        if not candidates:
            continue
        if len(candidates) != 1 or len(new_ids) != 1:
            paths = ", ".join(row["project_path"] for row in candidates)
            raise ValueError(
                "Cannot identify moved images with duplicate content; "
                f"candidates: {paths}"
            )
        old_id = candidates[0]["image_id"]
        new_id = new_ids[0]
        id_pairs[old_id] = new_id
        new_paths[old_id] = _manifest_project_path(root, discovered[new_id])

    if not id_pairs:
        return ()
    artifact_plan = _artifact_rename_plan(root, list(id_pairs.items()))
    _check_artifact_rename_conflicts(root, artifact_plan)
    _migrate_id_files(root, id_pairs)
    for source, target in artifact_plan:
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
    for row in rows:
        old_id = row["image_id"]
        if old_id in id_pairs:
            row["image_id"] = id_pairs[old_id]
            row["project_path"] = new_paths[old_id]
            row["file_name"] = Path(new_paths[old_id]).name
            row["source_path"] = str((root / new_paths[old_id]).resolve())
    write_manifest(root, rows)
    return tuple(id_pairs.items())


def rename_raw_subfolder(
    root: Path,
    old_folder: str,
    new_name: str,
) -> RawFolderRenameResult:
    """Rename one raw subfolder and its existing generated artifacts."""
    old_relative = PurePosixPath(old_folder.replace("\\", "/"))
    if (
        old_relative.is_absolute()
        or not old_relative.parts
        or any(part in ("", ".", "..") for part in old_relative.parts)
    ):
        raise ValueError("Old folder must be a relative subfolder below raw/")
    if (
        not new_name.strip()
        or new_name != new_name.strip()
        or new_name in (".", "..")
        or "/" in new_name
        or "\\" in new_name
        or any(character in '<>:"|?*' for character in new_name)
    ):
        raise ValueError("New folder name must be one valid directory name")

    raw_root = root / "raw"
    source_folder = raw_root.joinpath(*old_relative.parts)
    if not source_folder.is_dir():
        raise FileNotFoundError(f"Raw subfolder not found: {source_folder}")
    target_folder = source_folder.with_name(new_name)
    if target_folder.exists():
        raise ValueError(f"Target raw subfolder already exists: {target_folder}")

    rows = load_manifest(root)
    old_prefix = f"raw/{old_relative.as_posix()}/"
    new_relative = old_relative.parent / new_name
    new_prefix = f"raw/{new_relative.as_posix()}/"
    affected: list[tuple[dict[str, str], str, str, str]] = []
    stem_pairs: list[tuple[str, str]] = []
    id_pairs: dict[str, str] = {}
    for row in rows:
        project_path = row.get("project_path", "")
        if not project_path.startswith(old_prefix):
            continue
        old_stem = artifact_stem(row)
        new_project_path = new_prefix + project_path[len(old_prefix) :]
        updated = dict(row)
        updated["project_path"] = new_project_path
        old_id = row["image_id"]
        new_id = image_id_from_project_path(new_project_path)
        new_stem = artifact_stem(updated)
        affected.append((row, new_project_path, new_stem, new_id))
        stem_pairs.append((old_stem, new_stem))
        if old_id != new_id:
            id_pairs[old_id] = new_id

    if not affected:
        raise ValueError(
            f"No manifest images found below raw/{old_relative.as_posix()}"
        )

    planned = _artifact_rename_plan(root, stem_pairs)
    _check_artifact_rename_conflicts(root, planned)
    _check_id_file_rename_conflicts(
        root,
        _id_file_rename_plan(root, id_pairs),
    )
    affected_old_ids = {row["image_id"] for row, _path, _stem, _id in affected}
    existing_ids = {
        row["image_id"] for row in rows if row["image_id"] not in affected_old_ids
    }
    conflicting_ids = set(id_pairs.values()) & existing_ids
    if conflicting_ids:
        raise ValueError(
            "Target image_id already exists: " + ", ".join(sorted(conflicting_ids))
        )

    source_folder.rename(target_folder)
    for row, new_project_path, _new_stem, new_id in affected:
        row["project_path"] = new_project_path
        row["image_id"] = new_id
        row["file_name"] = Path(new_project_path).name
        row["source_path"] = str((root / new_project_path).resolve())
    if id_pairs:
        _migrate_id_files(root, id_pairs)
    write_manifest(root, rows)
    for source, target in planned:
        source.rename(target)

    return RawFolderRenameResult(
        old_folder=old_relative.as_posix(),
        new_folder=new_relative.as_posix(),
        image_ids=tuple(
            new_id for _row, _path, _stem, new_id in affected
        ),
        artifacts_renamed=tuple(
            (
                source.relative_to(root).as_posix(),
                target.relative_to(root).as_posix(),
            )
            for source, target in planned
        ),
    )


def discover_raw_images(root: Path) -> dict[str, Path]:
    raw = root / "raw"
    if not raw.is_dir():
        raise FileNotFoundError(f"Raw image directory not found: {raw}")

    discovered: dict[str, Path] = {}
    identifiers_by_case: dict[str, str] = {}
    for path in sorted(
        (item for item in raw.rglob("*") if item.is_file() and item.suffix.lower() == ".png"),
        key=lambda item: item.relative_to(root).as_posix().casefold(),
    ):
        project_path = _manifest_project_path(root, path)
        image_id = image_id_from_project_path(project_path)
        folded = image_id.casefold()
        if folded in identifiers_by_case:
            previous_id = identifiers_by_case[folded]
            previous = discovered[previous_id]
            raise ValueError(
                "Duplicate raw image path/ID "
                f"{image_id}: {previous.relative_to(root)} and {path.relative_to(root)}"
            )
        identifiers_by_case[folded] = image_id
        discovered[image_id] = path
    return discovered


def _initial_annotation(
    image_id: str,
    image_sha256: str,
    calibration_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "image_id": image_id,
        "image_sha256": image_sha256,
        "calibration_id": calibration_id,
        "revision": 1,
        "status": "awaiting_annotation",
        "measurements": [],
        "reference_lines": [],
        "refractive_index_groups": [],
        "rois": [],
        "layout": {
            "inset_aspect_ratio": 1.0,
            "main_scale_bar": {"x_um": 200, "y_um": 200},
            "inset_scale_bar": {
                "mode": "fixed",
                "x_um": 200,
                "y_um": 200,
            },
        },
        "history": [
            {
                "revision": 1,
                "date": datetime.now().astimezone().date().isoformat(),
                "reason": "project initialization",
            }
        ],
    }


def sync_raw_images(root: Path) -> RawSyncResult:
    config = load_config(root)
    calibration_id = str(config["calibration"]["id"])
    renamed = migrate_image_ids(root)
    discovered = discover_raw_images(root)
    rows = load_manifest(root)
    moved_by_hash = _migrate_moved_images(root, rows, discovered)
    if moved_by_hash:
        renamed = (*renamed, *moved_by_hash)
        rows = load_manifest(root)
    rows_by_id = {row["image_id"]: row for row in rows}
    added: list[str] = []
    moved: list[str] = []
    initialized: list[str] = []
    manifest_changed = False

    for image_id, image_path in discovered.items():
        project_path = _manifest_project_path(root, image_path)
        header = read_png_header(image_path)
        image_hash = sha256_file(image_path)
        crop_left, crop_top, crop_right, crop_bottom = detect_white_borders(
            image_path
        )
        content_width = header["width"] - crop_left - crop_right
        content_height = header["height"] - crop_top - crop_bottom
        row = rows_by_id.get(image_id)

        if row is None:
            pixel_format = (
                "Format24bppRgb"
                if header["color_type"] == "RGB"
                else str(header["color_type"])
            )
            row = {
                "image_id": image_id,
                "file_name": image_path.name,
                "source_path": str(image_path.resolve()),
                "project_path": project_path,
                "width_px": str(header["width"]),
                "height_px": str(header["height"]),
                "content_width_px": str(content_width),
                "content_height_px": str(content_height),
                "crop_left_px": str(crop_left),
                "crop_top_px": str(crop_top),
                "crop_right_px": str(crop_right),
                "crop_bottom_px": str(crop_bottom),
                "pixel_format": pixel_format,
                "sha256": image_hash,
                "calibration_id": calibration_id,
                "status": "awaiting_annotation",
            }
            rows.append(row)
            rows_by_id[image_id] = row
            added.append(image_id)
            manifest_changed = True
        else:
            expected_hash = row.get("sha256", "").upper()
            if expected_hash and expected_hash != image_hash:
                raise ValueError(
                    f"{image_id}: raw image content changed; "
                    "use a new file name/image_id instead of replacing an existing image"
                )
            if row.get("project_path") != project_path:
                row["project_path"] = project_path
                row["source_path"] = str(image_path.resolve())
                moved.append(image_id)
                manifest_changed = True
            if row.get("file_name") != image_path.name:
                row["file_name"] = image_path.name
                manifest_changed = True
            if row.get("source_path") != str(image_path.resolve()):
                row["source_path"] = str(image_path.resolve())
                manifest_changed = True
            for field, value in (
                ("content_width_px", str(content_width)),
                ("content_height_px", str(content_height)),
                ("crop_left_px", str(crop_left)),
                ("crop_top_px", str(crop_top)),
                ("crop_right_px", str(crop_right)),
                ("crop_bottom_px", str(crop_bottom)),
            ):
                if row.get(field) != value:
                    row[field] = value
                    manifest_changed = True

        path = annotation_path(root, image_id)
        if not path.is_file():
            atomic_write_json(
                path,
                _initial_annotation(image_id, image_hash, row["calibration_id"]),
            )
            initialized.append(image_id)

    if manifest_changed:
        write_manifest(root, rows)

    write_fiji_image_bounds(root)

    return RawSyncResult(
        discovered=len(discovered),
        added=tuple(added),
        moved=tuple(moved),
        initialized=tuple(initialized),
        renamed=renamed,
    )


def write_fiji_image_bounds(root: Path) -> None:
    """Write a dimension-keyed crop table that the Fiji macro can read."""

    bounds_by_dimensions: dict[tuple[int, int], tuple[int, int, int, int]] = {}
    for row in load_manifest(root):
        width = int(row["width_px"])
        height = int(row["height_px"])
        left, top, right, bottom, _content_width, _content_height = content_bounds(
            row
        )
        key = (width, height)
        value = (left, top, right, bottom)
        if key in bounds_by_dimensions and bounds_by_dimensions[key] != value:
            raise ValueError(
                f"Fiji image bounds by dimensions conflict for {width}×{height}; "
                "use distinct dimensions or update the macro to match image_id"
            )
        bounds_by_dimensions[key] = value

    lines = ["width,height,left,top,right,bottom"]
    for (width, height), (left, top, right, bottom) in sorted(
        bounds_by_dimensions.items()
    ):
        lines.append(f"{width},{height},{left},{top},{right},{bottom}")
    path = root / "fiji" / "image_bounds.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def manifest_by_id(root: Path) -> dict[str, dict[str, str]]:
    return {row["image_id"]: row for row in load_manifest(root)}


def annotation_path(root: Path, image_id: str) -> Path:
    return root / "annotations" / f"{image_id}.json"


def load_annotation(root: Path, image_id: str) -> dict[str, Any]:
    return json.loads(annotation_path(root, image_id).read_text(encoding="utf-8"))


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def read_png_header(path: Path) -> dict[str, int | str]:
    with path.open("rb") as handle:
        header = handle.read(33)
    if len(header) < 33 or header[:8] != PNG_SIGNATURE or header[12:16] != b"IHDR":
        raise ValueError(f"Not a valid PNG with an IHDR header: {path}")
    width, height, bit_depth, color_type = struct.unpack(">IIBB", header[16:26])
    return {
        "width": width,
        "height": height,
        "bit_depth": bit_depth,
        "color_type": PNG_COLOR_TYPES.get(color_type, f"unknown-{color_type}"),
    }


def _inside(value: float, low: float, high: float) -> bool:
    return low <= value <= high


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _valid_point(point: Any) -> tuple[float, float] | None:
    if not isinstance(point, list) or len(point) != 2:
        return None
    x = _finite_float(point[0])
    y = _finite_float(point[1])
    if x is None or y is None:
        return None
    return x, y


def validate_annotation(
    annotation: dict[str, Any],
    manifest: dict[str, str],
    config: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    image_id = manifest["image_id"]
    _left, _top, _right, _bottom, width, height = content_bounds(manifest)
    base_calibration = Calibration.from_project_config(config)
    source_width, source_height = source_dimensions(config)
    calibration = calibration_for_content(
        base_calibration,
        source_width,
        source_height,
        width,
        height,
    )

    if annotation.get("image_id") != image_id:
        errors.append(f"{image_id}: annotation image_id does not match manifest")
    if annotation.get("image_sha256") != manifest["sha256"]:
        errors.append(f"{image_id}: annotation image hash does not match manifest")
    if annotation.get("calibration_id") != manifest["calibration_id"]:
        errors.append(f"{image_id}: annotation calibration_id does not match manifest")

    rois_value = annotation.get("rois", [])
    rois = rois_value if isinstance(rois_value, list) else []
    if not isinstance(rois_value, list):
        errors.append(f"{image_id}: rois must be a list")
    if len(rois) > int(config["layout"]["inset_count"]):
        errors.append(f"{image_id}: only one inset ROI is allowed")

    measurements_value = annotation.get("measurements", [])
    measurements = measurements_value if isinstance(measurements_value, list) else []
    if not isinstance(measurements_value, list):
        errors.append(f"{image_id}: measurements must be a list")

    reference_lines_value = annotation.get("reference_lines", [])
    reference_lines = (
        reference_lines_value if isinstance(reference_lines_value, list) else []
    )
    if not isinstance(reference_lines_value, list):
        errors.append(f"{image_id}: reference_lines must be a list")
    if len(reference_lines) > 1:
        errors.append(f"{image_id}: only one reference line is allowed")

    groups_value = annotation.get("refractive_index_groups", [])
    groups = groups_value if isinstance(groups_value, list) else []
    if not isinstance(groups_value, list):
        errors.append(f"{image_id}: refractive_index_groups must be a list")

    seen_ids: set[str] = set()
    for object_kind, objects in (
        ("measurement", measurements),
        ("reference line", reference_lines),
        ("refractive-index group", groups),
        ("ROI", rois),
    ):
        for item in objects:
            object_id = item.get("id") if isinstance(item, dict) else None
            if not isinstance(object_id, str) or not object_id:
                errors.append(f"{image_id}: {object_kind} has an invalid object ID")
                continue
            if object_id in seen_ids:
                errors.append(f"{image_id}: duplicate object ID {object_id}")
            seen_ids.add(object_id)

    for reference_line in reference_lines:
        if not isinstance(reference_line, dict):
            errors.append(f"{image_id}: invalid reference line object")
            continue
        reference_id = reference_line.get("id", "?")
        if reference_id != "L001":
            errors.append(
                f"{image_id}/{reference_id}: reference line ID must be L001"
            )
        points: dict[str, tuple[float, float]] = {}
        for point_name in ("p1_px", "p2_px"):
            point = _valid_point(reference_line.get(point_name))
            if point is None:
                errors.append(
                    f"{image_id}/{reference_id}: invalid {point_name}"
                )
                continue
            points[point_name] = point
            if not _inside(point[0], 0, width) or not _inside(point[1], 0, height):
                errors.append(
                    f"{image_id}/{reference_id}: "
                    f"{point_name} is outside the source image"
                )
        if (
            "p1_px" in points
            and "p2_px" in points
            and points["p1_px"] == points["p2_px"]
        ):
            errors.append(f"{image_id}/{reference_id}: reference line has zero length")

    valid_roi_bounds: tuple[float, float, float, float] | None = None
    for roi in rois:
        if not isinstance(roi, dict):
            errors.append(f"{image_id}: invalid ROI object")
            continue
        roi_id = roi.get("id", "?")
        if roi_id != "R001":
            errors.append(f"{image_id}/{roi_id}: inset ROI ID must be R001")
        x = _finite_float(roi.get("x_px"))
        y = _finite_float(roi.get("y_px"))
        roi_width = _finite_float(roi.get("width_px"))
        roi_height = _finite_float(roi.get("height_px"))
        if None in (x, y, roi_width, roi_height):
            errors.append(f"{image_id}/{roi_id}: ROI coordinates must be finite numbers")
            continue
        assert x is not None
        assert y is not None
        assert roi_width is not None
        assert roi_height is not None
        if roi_width <= 0 or roi_height <= 0:
            errors.append(f"{image_id}/{roi_id}: ROI dimensions must be positive")
        if not math.isclose(roi_width, roi_height, rel_tol=0, abs_tol=1e-6):
            errors.append(
                f"{image_id}/{roi_id}: ROI must be square in source-pixel coordinates"
            )
        if x < 0 or y < 0 or x + roi_width > width or y + roi_height > height:
            errors.append(f"{image_id}/{roi_id}: ROI is outside the source image")
        elif (
            roi_width > 0
            and roi_height > 0
            and roi_id == "R001"
            and math.isclose(roi_width, roi_height, rel_tol=0, abs_tol=1e-6)
        ):
            valid_roi_bounds = (x, y, roi_width, roi_height)

    shared_measurements: dict[str, list[dict[str, Any]]] = {}
    measurements_by_id: dict[str, dict[str, Any]] = {}
    for measurement in measurements:
        if not isinstance(measurement, dict):
            errors.append(f"{image_id}: invalid measurement object")
            continue
        measurement_id = measurement.get("id", "?")
        if not isinstance(measurement_id, str) or not MEASUREMENT_ID_PATTERN.fullmatch(
            measurement_id
        ):
            errors.append(
                f"{image_id}/{measurement_id}: measurement ID must match T followed by digits"
            )
        elif isinstance(measurement_id, str):
            measurements_by_id[measurement_id] = measurement
        measurement_kind = measurement.get("kind", THICKNESS_KIND)
        if measurement_kind not in {THICKNESS_KIND, SHARED_FOOT_KIND}:
            errors.append(
                f"{image_id}/{measurement_id}: unsupported measurement kind "
                f"{measurement_kind}"
            )
        elif measurement_kind == SHARED_FOOT_KIND:
            group_id = measurement.get("group_id")
            group_member = measurement.get("group_member")
            if not isinstance(group_id, str) or not GROUP_ID_PATTERN.fullmatch(
                group_id
            ):
                errors.append(
                    f"{image_id}/{measurement_id}: shared-foot measurement "
                    "requires group_id Gxxx"
                )
            elif group_member not in {"A", "B"}:
                errors.append(
                    f"{image_id}/{measurement_id}: shared-foot measurement "
                    "requires group_member A or B"
                )
            else:
                shared_measurements.setdefault(group_id, []).append(measurement)
        points: dict[str, tuple[float, float]] = {}
        for point_name in ("p1_px", "p2_px"):
            point = _valid_point(measurement.get(point_name))
            if point is None:
                errors.append(
                    f"{image_id}/{measurement_id}: invalid {point_name}"
                )
                continue
            points[point_name] = point
            if not _inside(point[0], 0, width) or not _inside(point[1], 0, height):
                errors.append(
                    f"{image_id}/{measurement_id}: "
                    f"{point_name} is outside the source image"
                )
        if (
            "p1_px" in points
            and "p2_px" in points
            and points["p1_px"] == points["p2_px"]
        ):
            errors.append(f"{image_id}/{measurement_id}: measurement has zero length")

        views = measurement.get("views")
        views_are_valid = (
            isinstance(views, list)
            and bool(views)
            and all(
                isinstance(view, str) and view in {"main", "inset"}
                for view in views
            )
            and len(views) == len(set(views))
            and "main" in views
        )
        if not views_are_valid:
            errors.append(
                f"{image_id}/{measurement_id}: views must contain main and may contain inset once"
            )
        elif "inset" in views:
            if valid_roi_bounds is None or len(rois) != 1:
                errors.append(
                    f"{image_id}/{measurement_id}: inset view requires one valid R001"
                )
            elif "p1_px" in points and "p2_px" in points:
                roi_x, roi_y, roi_width, roi_height = valid_roi_bounds
                for point_name in ("p1_px", "p2_px"):
                    point_x, point_y = points[point_name]
                    if not (
                        roi_x <= point_x <= roi_x + roi_width
                        and roi_y <= point_y <= roi_y + roi_height
                    ):
                        errors.append(
                            f"{image_id}/{measurement_id}: {point_name} is outside "
                            "R001 but views includes inset"
                        )

    group_ids_in_data: set[str] = set()
    for group in groups:
        if not isinstance(group, dict):
            errors.append(f"{image_id}: invalid refractive-index group")
            continue
        group_id = group.get("id", "?")
        if not isinstance(group_id, str) or not GROUP_ID_PATTERN.fullmatch(group_id):
            errors.append(
                f"{image_id}/{group_id}: refractive-index group ID must match Gxxx"
            )
            continue
        group_ids_in_data.add(group_id)
        if group.get("reference_id") != "L001":
            errors.append(f"{image_id}/{group_id}: reference_id must be L001")
        measurement_ids = group.get("measurement_ids")
        if (
            not isinstance(measurement_ids, list)
            or len(measurement_ids) != 2
            or not all(isinstance(value, str) for value in measurement_ids)
            or len(set(measurement_ids)) != 2
        ):
            errors.append(
                f"{image_id}/{group_id}: measurement_ids must contain two unique IDs"
            )
            continue
        members = shared_measurements.get(group_id, [])
        member_ids = {str(item.get("id")) for item in members}
        if member_ids != set(measurement_ids):
            errors.append(
                f"{image_id}/{group_id}: group membership does not match measurements"
            )
            continue
        by_member = {str(item.get("group_member")): item for item in members}
        if set(by_member) != {"A", "B"}:
            errors.append(
                f"{image_id}/{group_id}: group must contain members A and B exactly once"
            )
            continue
        if len(reference_lines) != 1:
            errors.append(
                f"{image_id}/{group_id}: group requires exactly one L001"
            )
            continue
        try:
            geometry_errors = shared_foot_geometry_errors(
                calibration,
                by_member["A"],
                by_member["B"],
                reference_lines[0],
            )
        except (KeyError, TypeError, ValueError):
            geometry_errors = ["group geometry is invalid"]
        for geometry_error in geometry_errors:
            errors.append(f"{image_id}/{group_id}: {geometry_error}")

        calculated = group.get("calculated")
        if not isinstance(calculated, dict):
            errors.append(f"{image_id}/{group_id}: calculated data is missing")
            continue
        try:
            expected = refractive_index_calculation(
                calibration.distance_um(
                    tuple(by_member["A"]["p1_px"]),
                    tuple(by_member["A"]["p2_px"]),
                ),
                calibration.distance_um(
                    tuple(by_member["B"]["p1_px"]),
                    tuple(by_member["B"]["p2_px"]),
                ),
            )
            actual_n = float(calculated["refractive_index"])
            if not math.isclose(
                actual_n,
                float(expected["refractive_index"]),
                rel_tol=0,
                abs_tol=1e-9,
            ):
                errors.append(
                    f"{image_id}/{group_id}: refractive index is out of date"
                )
        except (KeyError, TypeError, ValueError):
            errors.append(
                f"{image_id}/{group_id}: calculated refractive index is invalid"
            )

        foot = _valid_point(group.get("foot_px"))
        expected_foot = _valid_point(by_member["A"].get("p2_px"))
        if foot is None or expected_foot is None or calibration.distance_um(
            foot, expected_foot
        ) > 1e-4:
            errors.append(
                f"{image_id}/{group_id}: stored common foot does not match measurements"
            )

    if set(shared_measurements) != group_ids_in_data:
        missing = sorted(set(shared_measurements) - group_ids_in_data)
        for group_id in missing:
            errors.append(
                f"{image_id}/{group_id}: shared-foot measurements lack group data"
            )
    return errors


def complete_annotation(root: Path, image_id: str) -> tuple[bool, int]:
    sync_raw_images(root)
    manifest = manifest_by_id(root)
    if image_id not in manifest:
        raise ValueError(f"Unknown image_id: {image_id}")

    annotation = load_annotation(root, image_id)
    revision = int(annotation["revision"])
    if annotation.get("status") == "approved":
        return False, revision

    errors = validate_annotation(annotation, manifest[image_id], load_config(root))
    if errors:
        raise ValueError(
            f"{image_id}: annotation cannot be completed: {'; '.join(errors)}"
        )
    history_dir = root / "annotations" / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    backup = history_dir / f"{image_id}.r{revision}.json"
    if not backup.exists():
        atomic_write_json(backup, annotation)

    annotation["revision"] = revision + 1
    annotation["status"] = "approved"
    annotation.setdefault("history", []).append(
        {
            "revision": annotation["revision"],
            "date": datetime.now().astimezone().isoformat(timespec="seconds"),
            "reason": "annotation completed and locked",
        }
    )
    atomic_write_json(annotation_path(root, image_id), annotation)
    return True, int(annotation["revision"])


def unlock_annotation(root: Path, image_id: str) -> tuple[bool, int]:
    sync_raw_images(root)
    manifest = manifest_by_id(root)
    if image_id not in manifest:
        raise ValueError(f"Unknown image_id: {image_id}")

    annotation = load_annotation(root, image_id)
    revision = int(annotation["revision"])
    if annotation.get("status") != "approved":
        return False, revision

    history_dir = root / "annotations" / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    backup = history_dir / f"{image_id}.r{revision}.json"
    if not backup.exists():
        atomic_write_json(backup, annotation)

    annotation["revision"] = revision + 1
    annotation["status"] = "in_progress"
    annotation.setdefault("history", []).append(
        {
            "revision": annotation["revision"],
            "date": datetime.now().astimezone().isoformat(timespec="seconds"),
            "reason": "annotation unlocked for editing",
        }
    )
    atomic_write_json(annotation_path(root, image_id), annotation)
    return True, int(annotation["revision"])


def validate_project(root: Path, verify_hashes: bool = True) -> list[str]:
    sync_raw_images(root)
    errors: list[str] = []
    config = load_config(root)
    calibration = Calibration.from_project_config(config)
    provided = config["calibration"]["provided_reference"]

    if not math.isclose(
        calibration.x_um_per_px,
        calibration.display_x_um_per_px * calibration.display_scale,
        rel_tol=0,
        abs_tol=1e-9,
    ):
        errors.append("x calibration does not map consistently from display to source")
    if not math.isclose(
        calibration.y_um_per_px,
        calibration.display_y_um_per_px * calibration.display_scale,
        rel_tol=0,
        abs_tol=1e-9,
    ):
        errors.append("y calibration does not map consistently from display to source")
    if not math.isclose(
        float(provided["x_px_per_um"]) * float(provided["x_um_per_px"]),
        1.0,
        rel_tol=0,
        abs_tol=2e-7,
    ):
        errors.append("provided x px/µm and µm/px are not reciprocal")
    if not math.isclose(
        float(provided["y_px_per_um"]) * float(provided["y_um_per_px"]),
        1.0,
        rel_tol=0,
        abs_tol=2e-7,
    ):
        errors.append("provided y px/µm and µm/px are not reciprocal")

    for row in load_manifest(root):
        image_id = row["image_id"]
        image_path = root / Path(row["project_path"])
        if not image_path.is_file():
            errors.append(f"{image_id}: missing image {image_path}")
            continue
        try:
            header = read_png_header(image_path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if int(row["width_px"]) != header["width"] or int(row["height_px"]) != header[
            "height"
        ]:
            errors.append(f"{image_id}: manifest dimensions do not match PNG")
        try:
            crop_left, crop_top, crop_right, crop_bottom, content_width, content_height = (
                content_bounds(row)
            )
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if (
            crop_left < 0
            or crop_top < 0
            or crop_right < 0
            or crop_bottom < 0
            or crop_left + crop_right >= header["width"]
            or crop_top + crop_bottom >= header["height"]
        ):
            errors.append(f"{image_id}: crop bounds are invalid")
        if row.get("content_width_px", "") != str(content_width) or row.get(
            "content_height_px", ""
        ) != str(content_height):
            errors.append(f"{image_id}: manifest content dimensions do not match crop")
        if verify_hashes and sha256_file(image_path) != row["sha256"]:
            errors.append(f"{image_id}: SHA-256 does not match manifest")

        path = annotation_path(root, image_id)
        if not path.is_file():
            errors.append(f"{image_id}: missing annotation JSON")
            continue
        errors.extend(validate_annotation(load_annotation(root, image_id), row, config))

    return errors
