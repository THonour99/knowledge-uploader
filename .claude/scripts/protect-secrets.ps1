# protect-secrets.ps1
# Claude Code PreToolUse hook
# 阻止 Write/Edit 到 .env / .env.* / 密钥相关文件
# 阻止把 API Key / Password 字符串硬编码到代码文件

$ErrorActionPreference = "Stop"

# 读 stdin JSON
$input_json = [Console]::In.ReadToEnd()
if (-not $input_json) {
    exit 0
}

try {
    $payload = $input_json | ConvertFrom-Json
} catch {
    # 解析失败不阻塞
    exit 0
}

$tool_name = $payload.tool_name
$tool_input = $payload.tool_input

if ($tool_name -notin @("Write", "Edit", "MultiEdit")) {
    exit 0
}

$file_path = $tool_input.file_path
if (-not $file_path) {
    exit 0
}

# Normalize path
$file_path = $file_path -replace '\\', '/'

# 1. 阻止 .env 类文件直接写
$env_patterns = @(
    '\.env$',
    '\.env\.[a-zA-Z0-9._-]+$',
    '/secrets/',
    '/credentials/',
    '_secrets\.[a-z]+$',
    '_credentials\.[a-z]+$'
)

foreach ($pattern in $env_patterns) {
    if ($file_path -match $pattern) {
        $msg = @"
[BLOCKED by protect-secrets.ps1]

不允许通过 Claude Code 直接写 / 编辑以下类型的文件:
  - .env / .env.*
  - secrets/ credentials/ 目录
  - *_secrets.* / *_credentials.*

请改为:
  1. 编辑 .env.example（示例文件，不含真实密钥）
  2. 实际密钥手动在本机 shell 设置: 'POSTGRES_PASSWORD=xxx' >> .env

被拒绝路径: $file_path
"@
        Write-Error $msg
        exit 1
    }
}

# 2. 检查 content / new_string 是否含硬编码密钥
$content_to_check = ""
if ($tool_input.content) {
    $content_to_check = $tool_input.content
} elseif ($tool_input.new_string) {
    $content_to_check = $tool_input.new_string
}

if ($content_to_check) {
    # 高风险关键字模式（仅 backend 代码）
    $is_backend_code = $file_path -match '^backend/.*\.py$'

    if ($is_backend_code) {
        $secret_patterns = @(
            'api[_-]?key\s*=\s*["\x27](?!\s*["\x27])(?![{<\$])(?!os\.environ)(?!getenv)[A-Za-z0-9_\-]{16,}["\x27]',
            'password\s*=\s*["\x27](?!\s*["\x27])(?!.*\$\{)(?!.*os\.)(?!.*getenv)[A-Za-z0-9_!@#$%^&*]{8,}["\x27]',
            'secret\s*=\s*["\x27](?!\s*["\x27])[A-Za-z0-9_\-]{16,}["\x27]',
            'token\s*=\s*["\x27]sk-[A-Za-z0-9_\-]{20,}["\x27]'
        )

        foreach ($pattern in $secret_patterns) {
            if ($content_to_check -match $pattern) {
                $msg = @"
[BLOCKED by protect-secrets.ps1]

代码中疑似硬编码了密钥/密码/Token:
  匹配模式: $pattern
  文件: $file_path

请改为:
  - 从环境变量读: os.environ['XXX'] 或 settings.XXX
  - 从配置服务读: settings.get('XXX')
  - 加密保存到数据库: ai_providers.api_key_encrypted

如这是误报（如示例字符串、测试数据），请明确加上 # noqa: secret 注释。
"@
                Write-Error $msg
                exit 1
            }
        }
    }
}

exit 0
