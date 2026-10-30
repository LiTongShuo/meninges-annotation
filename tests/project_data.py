from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def has_complete_project_data() -> bool:
    """Whether the optional, owner-provided integration dataset is installed."""

    required_files = (
        ROOT / "config" / "project.json",
        ROOT / "metadata" / "image_manifest.csv",
        ROOT / "metadata" / "calibration.csv",
        ROOT / "fiji" / "image_bounds.csv",
        ROOT / "tests" / "fixtures" / "bscan_x-1.objects.csv",
        ROOT / "raw" / "脑膜" / "bscan_x-1.png",
        ROOT / "raw" / "标注图片" / "图片 1.png",
        ROOT / "annotations" / "脑膜__bscan_x-1.json",
    )
    return all(path.is_file() for path in required_files)
