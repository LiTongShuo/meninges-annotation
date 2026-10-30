# 脑膜 B-scan 图像标注

[English](README.md) | **简体中文**

基于 Fiji 宏、结构化 JSON 标注、标定测量和 SVG 导出的非破坏性 B-scan 图像标注工作流。

## 功能

- 原始图像、标注数据和生成图件分别保存。
- 将 Fiji 测量和感兴趣区域导入结构化项目。
- 根据横向、纵向独立像素标定重新计算测量结果。
- 生成 SVG 母版，并通过外部工具导出图件。
- 校验图像哈希、尺寸、标定与标注的一致性。

## 安装配置

需要 **Python 3.12 或更新版本**。在仓库根目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
Copy-Item .\config\project.example.json .\config\project.json
Copy-Item .\metadata\image_manifest.example.csv .\metadata\image_manifest.csv
Copy-Item .\metadata\calibration.example.csv .\metadata\calibration.csv
.\.venv\Scripts\python.exe -m meninges_annotation --help
```

填写本地配置和标定值，将源 PNG 图像放入 `raw/`。示例中的标定字段是占位值，不能直接用于测量。使用 `scripts/` 中的工具前，需在本地项目配置中设置 Fiji 和 Inkscape 路径。

Fiji 和 Inkscape 需另行安装。仓库不含原图、真实标注、项目专属标定及生成报告。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

需要完整项目数据集的测试会在数据缺失时跳过，见[测试数据说明](tests/README.md)。

## 目录结构

| 路径 | 用途 |
| --- | --- |
| `src/meninges_annotation/` | 标定、项目校验、导入与导出 |
| `fiji/macros/` | Fiji 标注宏 |
| `scripts/` | 安装及工作流脚本 |
| `config/`、`metadata/` | 示例项目配置与表格 |
| `schemas/` | 标注 JSON 格式定义 |
| `tests/` | 单元测试及可选集成测试 |

## 许可证

采用 [MIT 许可证](LICENSE)。外部应用和依赖保留各自许可，见[第三方说明](THIRD_PARTY_NOTICES.md)。
