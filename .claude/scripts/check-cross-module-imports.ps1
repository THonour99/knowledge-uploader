# check-cross-module-imports.ps1
# Claude Code PostToolUse hook
# 检查 backend/app/modules/ 下的文件是否跨模块 import service/repository
# 不阻塞（PostToolUse），但会显示警告

$ErrorActionPreference = "Continue"

$input_json = [Console]::In.ReadToEnd()
if (-not $input_json) { exit 0 }

try {
    $payload = $input_json | ConvertFrom-Json
} catch {
    exit 0
}

$tool_name = $payload.tool_name
if ($tool_name -notin @("Write", "Edit", "MultiEdit")) { exit 0 }

$file_path = $payload.tool_input.file_path
if (-not $file_path) { exit 0 }
$file_path_norm = $file_path -replace '\\', '/'

# 仅在 backend/app/modules/ 下生效
if ($file_path_norm -notmatch '^backend/app/modules/([a-z_]+)/') {
    exit 0
}

$current_module = $matches[1]

# 读文件内容
if (-not (Test-Path $file_path)) { exit 0 }
$content = Get-Content $file_path -Raw -Encoding utf8

# 模块列表（与 03_BACKEND_SPEC §4 对齐）
$all_modules = @(
    "auth", "user", "document", "review", "ragflow",
    "ai", "statistics", "notification", "config", "audit"
)

$violations = @()

foreach ($mod in $all_modules) {
    if ($mod -eq $current_module) { continue }

    # 禁止: from app.modules.X.service / repository
    $banned_patterns = @(
        "from\s+app\.modules\.$mod\.service\s+import",
        "from\s+app\.modules\.$mod\.repository\s+import",
        "from\s+app\.modules\.$mod\s+import\s+service",
        "from\s+app\.modules\.$mod\s+import\s+repository"
    )

    foreach ($pattern in $banned_patterns) {
        $regex_matches = [regex]::Matches($content, $pattern)
        foreach ($m in $regex_matches) {
            $violations += [PSCustomObject]@{
                Module = $mod
                Match = $m.Value
            }
        }
    }
}

if ($violations.Count -gt 0) {
    $msg = "[WARN check-cross-module-imports.ps1] 跨模块 service/repository import 违规 ($($violations.Count))`n"
    $msg += "  文件: $file_path`n"
    $msg += "  当前模块: $current_module`n`n"

    foreach ($v in $violations) {
        $msg += "  ❌ 引用了 modules/$($v.Module): $($v.Match)`n"
    }

    $msg += @"

修复建议:
  - 跨模块通信只走: (1) 事件总线 @event_handler (2) Celery task .delay() (3) 共享 schemas
  - 允许: from app.modules.$($violations[0].Module).schemas import XxxRef

参考: CLAUDE.md §6 / 补充 spec §4.10
"@
    Write-Warning $msg
}

# 不阻塞（PostToolUse hook 不能阻止已发生的编辑）
exit 0
