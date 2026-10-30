[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string[]]$ImageId,

    [switch]$SkipImport
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

function Invoke-MeningesCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$CommandArguments
    )

    & $python -m meninges_annotation --root $projectRoot @CommandArguments
    if ($LASTEXITCODE -ne 0) {
        throw "meninges_annotation command failed: $($CommandArguments -join ' ')"
    }
}

function Get-ArtifactStem {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Row
    )

    $parts = @(([string]$Row.project_path) -split "/")
    if ($parts.Count -gt 0 -and $parts[0] -ieq "raw") {
        $parts = @($parts | Select-Object -Skip 1)
    }
    if ($parts.Count -eq 0) {
        throw "Invalid manifest project_path: $($Row.project_path)"
    }
    $parts[-1] = [System.IO.Path]::GetFileNameWithoutExtension($parts[-1])
    $encoded = @(
        $parts | ForEach-Object {
            ([string]$_).Replace("%", "%25").Replace("__", "_%5F")
        }
    )
    return $encoded -join "__"
}

function Select-ImagesInteractively {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Rows
    )

    Write-Host ""
    Write-Host "可生成 SVG 的图片：" -ForegroundColor Cyan
    $categoryPalette = @(
        "Cyan",
        "Magenta",
        "Blue",
        "DarkYellow",
        "DarkCyan",
        "DarkMagenta"
    )
    $categoryColors = @{}
    $nextCategoryColor = 0

    for ($index = 0; $index -lt $Rows.Count; $index++) {
        $row = $Rows[$index]
        $pathParts = ([string]$row.project_path) -split "/"
        $category = if ($pathParts.Count -gt 2) {
            ($pathParts[1..($pathParts.Count - 2)] -join "/")
        } else {
            "raw 根目录"
        }
        if (-not $categoryColors.ContainsKey($category)) {
            $categoryColors[$category] = $categoryPalette[
                $nextCategoryColor % $categoryPalette.Count
            ]
            $nextCategoryColor++
        }
        $csvPath = Join-Path $projectRoot "fiji\exports\$($row.image_id).objects.csv"
        $hasCsv = Test-Path -LiteralPath $csvPath -PathType Leaf
        $csvState = if ($hasCsv) {
            "有"
        } else {
            "无"
        }
        $annotationPath = Join-Path $projectRoot "annotations\$($row.image_id).json"
        $annotationState = "无"
        $annotation = $null
        if (Test-Path -LiteralPath $annotationPath -PathType Leaf) {
            try {
                $annotation = Get-Content -LiteralPath $annotationPath -Raw -Encoding UTF8 |
                    ConvertFrom-Json
                $statusLabel = switch ([string]$annotation.status) {
                    "approved" { "已完成（锁定）" }
                    "ready_for_review" { "待审核" }
                    "in_progress" { "进行中" }
                    "returned" { "已退回" }
                    "awaiting_annotation" { "待标注" }
                    default { [string]$annotation.status }
                }
                $annotationState = "$statusLabel, r$($annotation.revision)"
            } catch {
                $annotationState = "无法读取"
            }
        }

        $annotationColor = switch ([string]$annotation.status) {
            "approved" { "Green" }
            "ready_for_review" { "Cyan" }
            "in_progress" { "Yellow" }
            "returned" { "Red" }
            "awaiting_annotation" { "DarkGray" }
            default {
                if ($annotationState -eq "无法读取") {
                    "Red"
                } else {
                    "DarkGray"
                }
            }
        }

        Write-Host ("[{0,2}] " -f ($index + 1)) `
            -ForegroundColor DarkGray -NoNewline
        Write-Host ([string]$row.image_id) `
            -ForegroundColor White -NoNewline
        Write-Host " | 分类: " -ForegroundColor DarkGray -NoNewline
        Write-Host $category `
            -ForegroundColor $categoryColors[$category] -NoNewline
        Write-Host " | Fiji CSV: " -ForegroundColor DarkGray -NoNewline
        Write-Host $csvState `
            -ForegroundColor $(if ($hasCsv) { "Green" } else { "Red" }) `
            -NoNewline
        Write-Host " | 标注: " -ForegroundColor DarkGray -NoNewline
        Write-Host $annotationState -ForegroundColor $annotationColor
    }

    while ($true) {
        Write-Host ""
        $answer = Read-Host "输入序号（如 1、1,3、2-5；A=全部；Q=退出）"
        $answer = $answer.Trim()
        if ($answer -match "^(?i:q)$") {
            exit 0
        }
        if ($answer -match "^(?i:a)$") {
            return @($Rows | ForEach-Object { [string]$_.image_id })
        }

        $indexes = @()
        $valid = $true
        foreach ($token in ($answer -split "[,，\s]+")) {
            if (-not $token) {
                continue
            }
            if ($token -match "^(\d+)-(\d+)$") {
                $start = [int]$Matches[1]
                $end = [int]$Matches[2]
                if ($start -gt $end) {
                    $temporary = $start
                    $start = $end
                    $end = $temporary
                }
                if ($start -lt 1 -or $end -gt $Rows.Count) {
                    $valid = $false
                    break
                }
                $indexes += $start..$end
            } elseif ($token -match "^\d+$") {
                $number = [int]$token
                if ($number -lt 1 -or $number -gt $Rows.Count) {
                    $valid = $false
                    break
                }
                $indexes += $number
            } else {
                $valid = $false
                break
            }
        }

        $indexes = @($indexes | Select-Object -Unique)
        if ($valid -and $indexes.Count -gt 0) {
            return @(
                $indexes | ForEach-Object {
                    [string]$Rows[$_ - 1].image_id
                }
            )
        }
        Write-Host "输入无效，请重新选择。" -ForegroundColor Yellow
    }
}

function Wait-ForMainMenu {
    Write-Host ""
    Read-Host "按 Enter 返回图片列表" | Out-Null
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project environment is missing. Run scripts\bootstrap.ps1 first."
}

$interactive = -not $ImageId -or $ImageId.Count -eq 0
$requestedImageIds = @($ImageId)

do {
Invoke-MeningesCommand -CommandArguments @("sync-raw")

$manifestPath = Join-Path $projectRoot "metadata\image_manifest.csv"
$manifestRows = @(
    Import-Csv -LiteralPath $manifestPath |
        Sort-Object project_path, image_id
)
if ($manifestRows.Count -eq 0) {
    throw "No PNG images were found under raw."
}

if ($interactive) {
    $ImageId = @(
        Select-ImagesInteractively -Rows $manifestRows
    )
} else {
    $ImageId = @($requestedImageIds)
}

$manifestById = @{}
foreach ($row in $manifestRows) {
    $manifestById[[string]$row.image_id] = $row
}
foreach ($id in $ImageId) {
    if ([string]::IsNullOrWhiteSpace($id)) {
        throw "image_id cannot be empty."
    }
    if ($id.IndexOfAny([System.IO.Path]::GetInvalidFileNameChars()) -ge 0) {
        throw "Invalid image_id: $id"
    }
    if (-not $manifestById.ContainsKey($id)) {
        throw "Unknown image_id: $id"
    }
}

$lockedImageIds = @(
    foreach ($id in $ImageId) {
        $annotationPath = Join-Path $projectRoot "annotations\$id.json"
        if (Test-Path -LiteralPath $annotationPath -PathType Leaf) {
            $annotation = Get-Content -LiteralPath $annotationPath -Raw -Encoding UTF8 |
                ConvertFrom-Json
            if ([string]$annotation.status -eq "approved") {
                $id
            }
        }
    }
)

$missingCsvImageIds = @(
    foreach ($id in $ImageId) {
        $csvPath = Join-Path $projectRoot "fiji\exports\$id.objects.csv"
        if (-not (Test-Path -LiteralPath $csvPath -PathType Leaf)) {
            $id
        }
    }
)

$skipImportSelected = [bool]$SkipImport
if ($interactive) {
    Write-Host ""
    Write-Host "已选择: $($ImageId -join ', ')" -ForegroundColor Cyan

    Write-Host ""
    Write-Host "[1] 生成 SVG" -ForegroundColor Cyan
    Write-Host "[2] 完成标注并锁定（可多选）" -ForegroundColor Green
    Write-Host "[3] 解除锁定标注（可多选）" -ForegroundColor Yellow
    Write-Host "[4] 生成 WPS 兼容 SVG（可多选，仅已完成）" -ForegroundColor Magenta
    Write-Host "[5] 修改 raw 子文件夹名并同步生成文件名" -ForegroundColor Blue
    $operation = ""
    while ($true) {
        $operation = (Read-Host "选择操作（1/2/3/4/5；Q=退出，默认 1）").Trim()
        if (-not $operation -or $operation -eq "1") {
            $operation = "generate"
            break
        }
        if ($operation -eq "2") {
            $operation = "complete"
            break
        }
        if ($operation -eq "3") {
            $operation = "unlock"
            break
        }
        if ($operation -eq "4") {
            $operation = "wps"
            break
        }
        if ($operation -eq "5") {
            $operation = "rename-folder"
            break
        }
        if ($operation -match "^(?i:q)$") {
            exit 0
        }
        Write-Host "输入无效，请输入 1、2、3、4、5 或 Q。" -ForegroundColor Yellow
    }

    if ($operation -eq "rename-folder") {
        if ($ImageId.Count -ne 1) {
            Write-Host "修改子文件夹名时，请选择该文件夹中的一张图片。" `
                -ForegroundColor Red
            Wait-ForMainMenu
            continue
        }
        $id = [string]$ImageId[0]
        $row = $manifestById[$id]
        $pathParts = @(([string]$row.project_path) -split "/")
        if ($pathParts.Count -le 2) {
            Write-Host "所选图片位于 raw 根目录，没有可修改的子文件夹。" `
                -ForegroundColor Red
            Wait-ForMainMenu
            continue
        }
        $folderParts = @($pathParts[1..($pathParts.Count - 2)])
        $oldFolder = $folderParts -join "/"
        $oldName = $folderParts[-1]
        Write-Host ""
        Write-Host "当前子文件夹：raw/$oldFolder" -ForegroundColor Cyan
        Write-Host "其下所有图片及现有 SVG/PNG/PDF/WPS 文件都会同步改名。" `
            -ForegroundColor DarkGray
        $newName = (Read-Host "输入新的文件夹名称；直接回车取消").Trim()
        if (-not $newName) {
            Write-Host "已取消修改。" -ForegroundColor Yellow
            Wait-ForMainMenu
            continue
        }
        if ($newName -eq $oldName) {
            Write-Host "新旧名称相同，无需修改。" -ForegroundColor Yellow
            Wait-ForMainMenu
            continue
        }
        $confirmation = (
            Read-Host "输入 Y 确认将“$oldName”改为“$newName”"
        ).Trim()
        if ($confirmation -notmatch "^(?i:y|yes)$") {
            Write-Host "已取消修改。" -ForegroundColor Yellow
            Wait-ForMainMenu
            continue
        }
        Invoke-MeningesCommand -CommandArguments @(
            "rename-folder",
            $oldFolder,
            $newName
        )
        Invoke-MeningesCommand -CommandArguments @("validate")
        Write-Host "子文件夹、清单和生成文件名已同步更新。" `
            -ForegroundColor Green
        Wait-ForMainMenu
        continue
    }

    if ($operation -eq "complete") {
        Write-Host ""
        Write-Host "将为以下图片完成标注并锁定：" -ForegroundColor Green
        Write-Host ($ImageId -join ", ") -ForegroundColor Cyan
        Write-Host "警告：完成后将禁止重新导入 Fiji CSV 和重算标注。" `
            -ForegroundColor Yellow
        Write-Host "仍可校验项目和重新生成 SVG。" -ForegroundColor DarkGray
        $confirmation = (
            Read-Host "按 Enter 确认批量锁定；输入 Q 取消"
        ).Trim()
        if ($confirmation -match "^(?i:q)$") {
            Write-Host "已取消锁定。" -ForegroundColor Yellow
            Wait-ForMainMenu
            continue
        }
        $failedIds = @()
        foreach ($id in $ImageId) {
            try {
                Invoke-MeningesCommand -CommandArguments @("complete", $id)
                Write-Host ("已锁定：{0}" -f $id) -ForegroundColor Green
            } catch {
                Write-Host ("锁定失败：{0}" -f $id) -ForegroundColor Red
                Write-Host ("  {0}" -f $_.Exception.Message) -ForegroundColor Red
                $failedIds += $id
            }
        }
        if ($failedIds.Count -gt 0) {
            Write-Host ("锁定未完成：{0}" -f ($failedIds -join ", ")) `
                -ForegroundColor Red
        } else {
            Write-Host "锁定完成。" -ForegroundColor Green
        }
        Wait-ForMainMenu
        continue
    }

    if ($operation -eq "unlock") {
        $unlockableIds = @(
            $ImageId | Where-Object { $lockedImageIds -contains $_ }
        )
        $skippedIds = @(
            $ImageId | Where-Object { $lockedImageIds -notcontains $_ }
        )
        if ($unlockableIds.Count -eq 0) {
            Write-Host "所选图片当前均未锁定，无需解除。" -ForegroundColor Yellow
            Wait-ForMainMenu
            continue
        }
        if ($skippedIds.Count -gt 0) {
            Write-Host (
                "以下图片当前未锁定，跳过：{0}" -f ($skippedIds -join ", ")
            ) -ForegroundColor DarkGray
        }
        Write-Host ""
        Write-Host (
            "将解除以下图片的锁定：{0}" -f ($unlockableIds -join ", ")
        ) -ForegroundColor Yellow
        Write-Host "解除后可重新导入 Fiji CSV 和重算标注。" -ForegroundColor DarkGray
        $confirmation = (
            Read-Host "按 Enter 确认解除锁定；输入 Q 取消"
        ).Trim()
        if ($confirmation -match "^(?i:q)$") {
            Write-Host "已取消解除锁定。" -ForegroundColor Yellow
            Wait-ForMainMenu
            continue
        }
        $failedIds = @()
        foreach ($id in $unlockableIds) {
            try {
                Invoke-MeningesCommand -CommandArguments @("unlock", $id)
                Write-Host ("已解除锁定：{0}" -f $id) -ForegroundColor Green
            } catch {
                Write-Host ("解除锁定失败：{0}" -f $id) -ForegroundColor Red
                Write-Host ("  {0}" -f $_.Exception.Message) -ForegroundColor Red
                $failedIds += $id
            }
        }
        if ($failedIds.Count -gt 0) {
            Write-Host (
                "解除锁定未完成：{0}" -f ($failedIds -join ", ")
            ) -ForegroundColor Red
        } else {
            Write-Host "已解除锁定。" -ForegroundColor Green
        }
        Wait-ForMainMenu
        continue
    }

    if ($operation -eq "wps") {
        $incompleteImageIds = @(
            $ImageId | Where-Object { $lockedImageIds -notcontains $_ }
        )
        if ($incompleteImageIds.Count -gt 0) {
            Write-Host (
                "以下图片尚未完成锁定，不能生成 WPS 兼容 SVG：{0}" -f
                ($incompleteImageIds -join ", ")
            ) -ForegroundColor Red
            Wait-ForMainMenu
            continue
        }

        Write-Host ""
        Write-Host "将为以下已完成图片生成 WPS 兼容 SVG：" `
            -ForegroundColor Magenta
        Write-Host ($ImageId -join ", ") -ForegroundColor Green
        $confirmation = (
            Read-Host "按 Enter 开始，输入 Q 取消"
        ).Trim()
        if ($confirmation -match "^(?i:q)$") {
            Wait-ForMainMenu
            continue
        }

        Invoke-MeningesCommand -CommandArguments @("validate")
        $failedIds = @()
        $succeededIds = @()
        foreach ($id in $ImageId) {
            try {
                Invoke-MeningesCommand -CommandArguments @(
                    "export-wps",
                    $id,
                    "--view",
                    "both"
                )
                $succeededIds += $id
            } catch {
                Write-Host ("WPS 导出失败：{0}" -f $id) -ForegroundColor Red
                Write-Host ("  {0}" -f $_.Exception.Message) -ForegroundColor Red
                $failedIds += $id
            }
        }

        if ($succeededIds.Count -gt 0) {
            Write-Host "WPS 兼容 SVG 生成完成：" -ForegroundColor Green
            foreach ($id in $succeededIds) {
                $artifactStem = Get-ArtifactStem -Row $manifestById[$id]
                Get-Item -LiteralPath (
                    Join-Path $projectRoot (
                        "exports\vector\wps\${artifactStem}_global_wps.svg"
                    )
                )
                $cropWpsPath = Join-Path $projectRoot (
                    "exports\vector\wps\${artifactStem}_crop_wps.svg"
                )
                if (Test-Path -LiteralPath $cropWpsPath -PathType Leaf) {
                    Get-Item -LiteralPath $cropWpsPath
                }
            }
        }
        if ($failedIds.Count -gt 0) {
            Write-Host (
                "WPS 导出未完成：{0}" -f ($failedIds -join ", ")
            ) -ForegroundColor Red
        }
        Wait-ForMainMenu
        continue
    }

    while ($true) {
        $mode = (Read-Host "生成方式（1=重新导入 Fiji CSV；2=仅用现有 JSON；Q=退出，默认 1）").Trim()
        if (-not $mode -or $mode -eq "1") {
            if ($lockedImageIds.Count -gt 0) {
                Write-Host (
                    "已完成锁定的图片不能重新导入：{0}。请选择方式 2。" -f
                    ($lockedImageIds -join ", ")
                ) -ForegroundColor Red
                continue
            }
            if ($missingCsvImageIds.Count -gt 0) {
                Write-Host (
                    "以下图片没有 Fiji 对象 CSV，不能重新导入：{0}" -f
                    ($missingCsvImageIds -join ", ")
                ) -ForegroundColor Red
                Write-Host (
                    "请先在 Fiji 中执行 Export current objects，" +
                    "或选择方式 2 使用现有 JSON。"
                ) -ForegroundColor Yellow
                continue
            }
            $skipImportSelected = $false
            break
        }
        if ($mode -eq "2") {
            $skipImportSelected = $true
            break
        }
        if ($mode -match "^(?i:q)$") {
            exit 0
        }
        Write-Host "输入无效，请输入 1、2 或 Q。" -ForegroundColor Yellow
    }

    $confirmation = (Read-Host "按 Enter 开始生成，输入 Q 取消").Trim()
    if ($confirmation -match "^(?i:q)$") {
        exit 0
    }
}

if (-not $skipImportSelected) {
    $csvPaths = foreach ($id in $ImageId) {
        $csvPath = Join-Path $projectRoot "fiji\exports\$id.objects.csv"
        if (-not (Test-Path -LiteralPath $csvPath -PathType Leaf)) {
            throw "Fiji export is missing: $csvPath"
        }
        $csvPath
    }

    foreach ($csvPath in $csvPaths) {
        Invoke-MeningesCommand -CommandArguments @("import-fiji", $csvPath)
    }
}

Invoke-MeningesCommand -CommandArguments @("validate")

foreach ($id in $ImageId) {
    Invoke-MeningesCommand -CommandArguments @("render", $id)
}

Write-Host "SVG generation completed:"
foreach ($id in $ImageId) {
    $artifactStem = Get-ArtifactStem -Row $manifestById[$id]
    Get-Item -LiteralPath (
        Join-Path $projectRoot "layouts\masters\${artifactStem}_global.svg"
    )
    $cropMasterPath = Join-Path $projectRoot (
        "layouts\masters\${artifactStem}_crop.svg"
    )
    if (Test-Path -LiteralPath $cropMasterPath -PathType Leaf) {
        Get-Item -LiteralPath $cropMasterPath
    }
}

if ($interactive) {
    Wait-ForMainMenu
}
} while ($interactive)
