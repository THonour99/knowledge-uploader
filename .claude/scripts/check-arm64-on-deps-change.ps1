# check-arm64-on-deps-change.ps1
# Claude Code PostToolUse hook
# 当 backend/requirements*.txt 被改动时, 自动跑 ARM64 wheel 检查

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

# 仅 backend/requirements*.txt
if ($file_path_norm -notmatch '^backend/requirements[a-z_-]*\.txt$') {
    exit 0
}

Write-Host "[check-arm64-on-deps-change] 检测到 $file_path_norm 改动, 跑 ARM64 wheel 检查..." -ForegroundColor Cyan

$script_path = "scripts/check_arm64_wheels.py"

if (-not (Test-Path $script_path)) {
    Write-Warning "[check-arm64-on-deps-change] $script_path 不存在 (可能是阶段 0 之前)。跳过。"
    exit 0
}

# 直接调用 Python 脚本
try {
    $py_cmd = "python"
    if (Get-Command "py" -ErrorAction SilentlyContinue) {
        $py_cmd = "py -3"
    }

    $output = & cmd /c "$py_cmd $script_path `"$file_path_norm`" 2>&1"
    $exit_code = $LASTEXITCODE

    Write-Host $output

    if ($exit_code -ne 0) {
        $msg = @"

[WARN check-arm64-on-deps-change.ps1]
某些依赖在 ARM64 (DGX Spark) 上不可用。

行动:
  1. 看上方输出哪个包失败
  2. 在补充 spec §2.5.2 找替代方案
  3. 替换后重新跑: invoke check-arm64

注意: 这是 warning 不是 block, 但请在合并前修掉。
"@
        Write-Warning $msg
    } else {
        Write-Host "[check-arm64-on-deps-change] 全部依赖 ARM64 兼容 ✓" -ForegroundColor Green
    }
} catch {
    Write-Warning "[check-arm64-on-deps-change] 检查脚本执行异常: $_"
}

# PostToolUse 不阻塞
exit 0
