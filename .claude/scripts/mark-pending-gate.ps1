# mark-pending-gate.ps1
# Claude Code PostToolUse hook
# 当 Edit/Write/MultiEdit 命中 backend/app/ 或 frontend/src/ 源码时，
# 记录"有未验收改动"标记到 .claude/artifacts/gate-state/pending.json。
# 该标记由 ship-gate 全绿后清除，由 adversarial-gate.ps1（Stop hook）读取以决定放行/打回。
# 不阻塞（PostToolUse 无法撤销已发生的编辑），始终 exit 0。
# 设计依据：CLAUDE.md「完成门 / DoD」+ docs/quality/definition-of-done.md

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

# 保留原始路径：用于实测 hook 传入的是绝对还是相对路径（写入 pending.json 可见）
$raw_path = $file_path
$norm = $file_path -replace '\\', '/'

# 命中后端/前端源码才触发完成门（双向匹配：绝对路径或相对路径都覆盖）
$is_source = ($norm -match '(^|/)backend/app/') -or ($norm -match '(^|/)frontend/src/')
if (-not $is_source) { exit 0 }

# 排除门机制自身的产物，避免自触发
if ($norm -match '/artifacts/gate-state/') { exit 0 }

# 确保 gate-state 目录存在（相对脚本所在目录 .claude/scripts/）
$gate_dir = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\artifacts\gate-state'))
if (-not (Test-Path $gate_dir)) {
    New-Item -ItemType Directory -Path $gate_dir -Force | Out-Null
}

$pending_file = Join-Path $gate_dir 'pending.json'
$now = (Get-Date).ToString('o')

# 读现有标记（若有），合并文件列表，保留首次标记时间
$files = @()
$first = $now
if (Test-Path $pending_file) {
    try {
        $existing = Get-Content $pending_file -Raw -Encoding utf8 | ConvertFrom-Json
        if ($existing.files) { $files = @($existing.files) }
        if ($existing.first_marked) { $first = $existing.first_marked }
    } catch { }
}

if ($files -notcontains $raw_path) { $files += $raw_path }

$state = [PSCustomObject]@{
    reason       = "源码已改动，等待 ship-gate 完成门验收"
    files        = @($files)
    first_marked = $first
    last_marked  = $now
}

$state | ConvertTo-Json -Depth 5 | Set-Content -Path $pending_file -Encoding utf8

exit 0
