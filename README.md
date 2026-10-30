# Meninges B-scan Annotation

**English** | [简体中文](README.zh-CN.md)

A non-destructive B-scan image annotation workflow using Fiji macros, structured JSON annotations, calibrated measurements, and SVG export.

## Features

- Keep original images separate from annotations and generated figures.
- Import Fiji measurements and regions of interest into structured project data.
- Recalculate measurements using separate horizontal and vertical pixel calibration.
- Generate SVG masters and export figures through external tools.
- Validate image hashes, dimensions, calibration, and annotation consistency.

## Setup

Requires **Python 3.12 or newer**. From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
Copy-Item .\config\project.example.json .\config\project.json
Copy-Item .\metadata\image_manifest.example.csv .\metadata\image_manifest.csv
Copy-Item .\metadata\calibration.example.csv .\metadata\calibration.csv
.\.venv\Scripts\python.exe -m meninges_annotation --help
```

Fill in your local configuration and calibration values, and put your source PNG images in `raw/`. Example calibration fields are placeholders, not usable measurements. Configure Fiji and Inkscape paths in the local project file before running the corresponding wrappers under `scripts/`.

Fiji and Inkscape are installed separately. Original images, real annotations, project-specific calibration, and generated reports are not bundled.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests requiring the optional complete project dataset are skipped when it is absent. See [test data notes](tests/README.md).

## Repository layout

| Path | Purpose |
| --- | --- |
| `src/meninges_annotation/` | Calibration, project validation, import, and export |
| `fiji/macros/` | Fiji annotation macro |
| `scripts/` | Setup and workflow wrappers |
| `config/`, `metadata/` | Example project configuration and tables |
| `schemas/` | Annotation JSON schema |
| `tests/` | Unit and optional integration tests |

## License

[MIT](LICENSE). External applications and dependencies retain their own licenses; see [third-party notices](THIRD_PARTY_NOTICES.md).
