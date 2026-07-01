# adversarial-gate.ps1
# Claude Code Stop hook —— 对抗式完成门硬拦截
# 改了源码但没过 ship-gate，就打回主代理、强制它先跑完成门验收。
#
# 放行/打回逻辑（优先级从上到下）：
#   1. 子代理（payload.agent_id 存在）   → 放行。门只对顶层主代理生效；
#      dev-worker 等子代理是主代理的工具，不负责跑完整 ship-gate。
#   2. 无 pending.json（无未验收改动）   → 放行。
#   3. 人工逃生阀 override 文件存在        → 放行 + 强警告，并清账。
#      绕过门的权力只在【人】手里，主代理不被告知此机制 → 无法自我放行。
#   4. 连续打回超过阈值                    → 强制放行防死循环 + 记录 + 清账，交人工。
#   5. 其余                                → 输出 {"decision":"block"} 打回，提示先跑 /ship-gate。
#
# 阻断方式用 stdout 的 decision:block JSON（reason 会反馈给模型），exit 0。
# 设计依据：CLAUDE.md「完成门 / DoD」+ docs/quality/definition-of-done.md

$ErrorActionPreference = "Continue"

$input_json = [Console]::In.ReadToEnd()
if (-not $input_json) { exit 0 }

try {
    $payload = $input_json | ConvertFrom-Json
} catch {
    exit 0
}

# 1. 子代理结束不拦
if ($payload.agent_id) { exit 0 }

$gate_dir = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\artifacts\gate-state'))
$pending_file  = Join-Path $gate_dir 'pending.json'
$override_file = Join-Path $gate_dir 'override'
$count_file    = Join-Path $gate_dir 'block_count'

# 2. 没有未验收改动 → 放行
if (-not (Test-Path $pending_file)) { exit 0 }

# 3. 人工逃生阀（仅人能创建 override 文件）
if (Test-Path $override_file) {
    Write-Warning "[adversarial-gate] override 生效，跳过完成门。请人工确认质量，并自行删除 override 文件。"
    Remove-Item $pending_file -ErrorAction SilentlyContinue
    Remove-Item $count_file -ErrorAction SilentlyContinue
    exit 0
}

# 4. 防死循环：累加 block 计数
$count = 0
if (Test-Path $count_file) {
    try { $count = [int]((Get-Content $count_file -Raw).Trim()) } catch { $count = 0 }
}
$count += 1
Set-Content -Path $count_file -Value $count -Encoding ascii

$max_blocks = 5
if ($count -gt $max_blocks) {
    $forced = Join-Path $gate_dir 'last-forced-release.txt'
    "$((Get-Date).ToString('o')) 连续打回 $count 次后强制放行（防死循环）" | Set-Content -Path $forced -Encoding utf8
    Write-Warning "[adversarial-gate] 已连续打回 ${max_blocks} 次仍未通过 ship-gate，强制放行防死循环。已清账，请人工检查 last-forced-release.txt 与改动质量。"
    Remove-Item $pending_file -ErrorAction SilentlyContinue
    Remove-Item $count_file -ErrorAction SilentlyContinue
    exit 0
}

# 5. 打回主代理
$files_hint = ""
try {
    $state = Get-Content $pending_file -Raw -Encoding utf8 | ConvertFrom-Json
    if ($state.files) { $files_hint = (@($state.files) -join ", ") }
} catch { }

$reason = "检测到本轮修改了后端/前端源码（$files_hint），但尚未通过 ship-gate 完成门。" +
          "结束前请运行 /ship-gate，跑完事实层(invoke lint + invoke test + invoke check-arm64) + " +
          "quality-reviewer + security-auditor + red-team 四方审查；全部通过后标记会自动清除再放行。" +
          "这是项目硬规则——完成判定权不在执行者手里（见 docs/quality/definition-of-done.md）。"

$output = [PSCustomObject]@{
    decision = "block"
    reason   = $reason
}
$output | ConvertTo-Json -Compress
exit 0
