from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from .calibration import Calibration
from .fiji_import import import_fiji_objects, recalculate_annotation
from .image_geometry import (
    calibration_for_content,
    content_bounds,
    source_dimensions,
)
from .project import (
    annotation_path,
    artifact_stem,
    atomic_write_json,
    complete_annotation,
    find_project_root,
    load_annotation,
    load_config,
    load_manifest,
    manifest_by_id,
    migrate_artifact_names,
    rename_raw_subfolder,
    sync_raw_images,
    unlock_annotation,
    validate_project,
)
from .svg_export import render_svgs
from .wps_export import export_wps_svg


def _root_from_args(args: argparse.Namespace) -> Path:
    return find_project_root(args.root)


def command_validate(args: argparse.Namespace) -> int:
    root = _root_from_args(args)
    errors = validate_project(root, verify_hashes=not args.skip_hash)
    if errors:
        print("项目校验失败：")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("项目校验通过：图像、哈希、尺寸、标定和标注数据一致。")
    return 0


def command_sync_raw(args: argparse.Namespace) -> int:
    root = _root_from_args(args)
    result = sync_raw_images(root)
    print(
        f"已扫描 raw：{result.discovered} 张 PNG；"
        f"新增 {len(result.added)}，移动 {len(result.moved)}，"
        f"初始化标注 {len(result.initialized)}。"
    )
    if result.added:
        print(f"新增 image_id：{', '.join(result.added)}")
    if result.moved:
        print(f"已更新路径：{', '.join(result.moved)}")
    if result.renamed:
        print(
            "已迁移 image_id："
            + ", ".join(f"{old} -> {new}" for old, new in result.renamed)
        )
    return 0


def command_migrate_names(args: argparse.Namespace) -> int:
    root = _root_from_args(args)
    result = migrate_artifact_names(root)
    print(
        f"命名迁移完成：重命名 {len(result.renamed)} 个文件，"
        f"清理 {len(result.duplicates_removed)} 个相同旧副本。"
    )
    for source, target in result.renamed:
        print(f"  {source} -> {target}")
    return 0


def command_rename_folder(args: argparse.Namespace) -> int:
    root = _root_from_args(args)
    result = rename_raw_subfolder(root, args.old_folder, args.new_name)
    print(
        f"子文件夹已重命名：raw/{result.old_folder} -> "
        f"raw/{result.new_folder}；更新 {len(result.image_ids)} 张图片，"
        f"同步重命名 {len(result.artifacts_renamed)} 个生成文件。"
    )
    return 0


def command_complete(args: argparse.Namespace) -> int:
    root = _root_from_args(args)
    changed, revision = complete_annotation(root, args.image_id)
    if changed:
        print(f"已完成并锁定：{args.image_id}（revision {revision}）。")
    else:
        print(f"已是完成锁定状态：{args.image_id}（revision {revision}）。")
    return 0


def command_unlock(args: argparse.Namespace) -> int:
    root = _root_from_args(args)
    changed, revision = unlock_annotation(root, args.image_id)
    if changed:
        print(f"已解除锁定：{args.image_id}（revision {revision}）。")
    else:
        print(f"标注当前未锁定：{args.image_id}（revision {revision}）。")
    return 0


def command_import_fiji(args: argparse.Namespace) -> int:
    root = _root_from_args(args)
    image_id, measurement_count, roi_count = import_fiji_objects(
        root, Path(args.csv).resolve()
    )
    print(
        f"已导入 {image_id}：{measurement_count} 条测量，{roi_count} 个放大 ROI。"
    )
    return 0


def command_recalculate(args: argparse.Namespace) -> int:
    root = _root_from_args(args)
    sync_raw_images(root)
    config = load_config(root)
    base_calibration = Calibration.from_project_config(config)
    source_width, source_height = source_dimensions(config)
    image_ids = (
        [args.image_id]
        if args.image_id
        else [row["image_id"] for row in load_manifest(root)]
    )
    for image_id in image_ids:
        _left, _top, _right, _bottom, content_width, content_height = content_bounds(
            manifest_by_id(root)[image_id]
        )
        calibration = calibration_for_content(
            base_calibration,
            source_width,
            source_height,
            content_width,
            content_height,
        )
        updated = recalculate_annotation(
            load_annotation(root, image_id), calibration
        )
        atomic_write_json(annotation_path(root, image_id), updated)
        print(f"已重算：{image_id}")
    return 0


def command_render(args: argparse.Namespace) -> int:
    root = _root_from_args(args)
    for view, output in render_svgs(root, args.image_id).items():
        print(f"已生成 {view}：{output}")
    return 0


def command_render_all(args: argparse.Namespace) -> int:
    root = _root_from_args(args)
    sync_raw_images(root)
    for row in load_manifest(root):
        for view, output in render_svgs(root, row["image_id"]).items():
            print(f"已生成 {view}：{output}")
    return 0


def _tool_path(root: Path, configured: str) -> Path:
    path = Path(configured)
    return path if path.is_absolute() else root / path


def _export_one(
    root: Path,
    image_id: str,
    view: str,
    export_type: str,
    svg: Path,
) -> Path:
    config = load_config(root)
    inkscape = _tool_path(root, config["tools"]["inkscape_executable"])
    if not inkscape.is_file():
        raise FileNotFoundError(f"Inkscape executable not found: {inkscape}")
    output_stem = artifact_stem(manifest_by_id(root)[image_id])
    output = (
        root
        / "exports"
        / export_type
        / f"{output_stem}_{view}.{export_type}"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(inkscape),
            str(svg),
            f"--export-type={export_type}",
            f"--export-filename={output}",
        ],
        check=True,
    )
    return output


def command_export(args: argparse.Namespace) -> int:
    root = _root_from_args(args)
    sync_raw_images(root)
    image_ids = (
        [args.image_id]
        if args.image_id
        else [row["image_id"] for row in load_manifest(root)]
    )
    for image_id in image_ids:
        svgs = render_svgs(root, image_id)
        views = tuple(svgs) if args.view == "both" else (args.view,)
        for view in views:
            if view not in svgs:
                raise ValueError(
                    f"{image_id}: crop export requires an R001 inset ROI"
                )
            for export_type in args.format:
                output = _export_one(
                    root,
                    image_id,
                    view,
                    export_type,
                    svgs[view],
                )
                print(f"已导出 {view}：{output}")
    return 0


def command_export_wps(args: argparse.Namespace) -> int:
    root = _root_from_args(args)
    sync_raw_images(root)
    image_ids = (
        [args.image_id]
        if args.image_id
        else [row["image_id"] for row in load_manifest(root)]
    )
    for image_id in image_ids:
        annotation = load_annotation(root, image_id)
        available_views = (
            ("global", "crop")
            if annotation.get("rois", [])
            else ("global",)
        )
        views = available_views if args.view == "both" else (args.view,)
        if "crop" in views and "crop" not in available_views:
            raise ValueError(
                f"{image_id}: crop export requires an R001 inset ROI"
            )
        output_stem = artifact_stem(manifest_by_id(root)[image_id])
        for view in views:
            master_svg = (
                root
                / "layouts"
                / "masters"
                / f"{output_stem}_{view}.svg"
            )
            if not master_svg.is_file():
                raise FileNotFoundError(
                    f"SVG master not found: {master_svg}; "
                    f"run render {image_id} first"
                )
            output = export_wps_svg(
                root,
                image_id,
                view,
                master_svg,
            )
            print(f"已导出 WPS {view}：{output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meninges-annotation",
        description="脑膜 B-scan 非破坏式测量与 SVG 排版工具",
    )
    parser.add_argument(
        "--root", type=Path, help="项目根目录；默认从当前目录向上查找"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="校验项目数据和原图")
    validate.add_argument("--skip-hash", action="store_true")
    validate.set_defaults(func=command_validate)

    sync_raw = subparsers.add_parser(
        "sync-raw",
        help="递归扫描 raw，注册新图片并更新移动后的路径",
    )
    sync_raw.set_defaults(func=command_sync_raw)

    migrate_names = subparsers.add_parser(
        "migrate-names",
        help="把现有生成文件迁移为包含 raw 子目录的名称",
    )
    migrate_names.set_defaults(func=command_migrate_names)

    rename_folder = subparsers.add_parser(
        "rename-folder",
        help="重命名 raw 子文件夹并同步清单和生成文件名",
    )
    rename_folder.add_argument("old_folder", help="相对于 raw 的子文件夹路径")
    rename_folder.add_argument("new_name", help="新的文件夹名称")
    rename_folder.set_defaults(func=command_rename_folder)

    complete = subparsers.add_parser(
        "complete",
        help="将标注设为完成并锁定，禁止再次导入或重算",
    )
    complete.add_argument("image_id")
    complete.set_defaults(func=command_complete)

    unlock = subparsers.add_parser(
        "unlock",
        help="解除已完成标注的锁定，恢复导入和重算",
    )
    unlock.add_argument("image_id")
    unlock.set_defaults(func=command_unlock)

    import_fiji = subparsers.add_parser(
        "import-fiji", help="导入 Fiji 宏导出的对象 CSV"
    )
    import_fiji.add_argument("csv")
    import_fiji.set_defaults(func=command_import_fiji)

    recalculate = subparsers.add_parser(
        "recalculate", help="按当前标定重算测量值"
    )
    recalculate.add_argument("image_id", nargs="?")
    recalculate.set_defaults(func=command_recalculate)

    render = subparsers.add_parser("render", help="生成单张 SVG 母版")
    render.add_argument("image_id")
    render.set_defaults(func=command_render)

    render_all = subparsers.add_parser("render-all", help="生成全部 SVG 母版")
    render_all.set_defaults(func=command_render_all)

    export = subparsers.add_parser("export", help="通过 Inkscape 导出 PNG/PDF")
    export.add_argument("image_id", nargs="?")
    export.add_argument(
        "--format",
        action="append",
        choices=("png", "pdf"),
        default=None,
        help="可重复指定；默认 png",
    )
    export.add_argument(
        "--view",
        choices=("global", "crop", "both"),
        default="both",
        help="导出全局图、裁剪图或二者；默认 both",
    )
    export.set_defaults(func=command_export)

    export_wps = subparsers.add_parser(
        "export-wps",
        help="生成嵌入底图且文字转路径的 WPS/PPT 兼容 SVG",
    )
    export_wps.add_argument("image_id", nargs="?")
    export_wps.add_argument(
        "--view",
        choices=("global", "crop", "both"),
        default="both",
        help="导出全局图、裁剪图或二者；默认 both",
    )
    export_wps.set_defaults(func=command_export_wps)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "format", None) is None:
        args.format = ["png"]
    try:
        return int(args.func(args))
    except (FileNotFoundError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"错误：{exc}\n")
