[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [switch]$NoFrontend
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $projectRoot 'frontend'
$composeFile = Join-Path $projectRoot 'docker-compose.yml'

function Require-Command([string]$name, [string]$installHint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "$name 未找到。$installHint"
    }
}

function Read-RequiredSecret([string]$name, [string]$prompt) {
    $value = [Environment]::GetEnvironmentVariable($name, 'Process')
    if ([string]::IsNullOrWhiteSpace($value)) {
        $secure = Read-Host -Prompt $prompt -AsSecureString
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try {
            $value = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        }
        finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        }
        if ([string]::IsNullOrWhiteSpace($value)) {
            throw "$name 不能为空。"
        }
        Set-Item -Path "Env:${name}" -Value $value
    }
    return $value
}

function Test-DockerEngine {
    try {
        & docker info 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

Set-Location $projectRoot
Require-Command 'docker' '请先启动 Docker Desktop。'
Require-Command 'npm' '请先安装 Node.js LTS。'

if (-not (Test-DockerEngine)) {
    $dockerExecutable = (Get-Command 'docker' -ErrorAction Stop).Source
    $dockerRoot = Split-Path (Split-Path $dockerExecutable -Parent) -Parent
    $dockerDesktopCandidates = @(
        (Join-Path $dockerRoot 'Docker Desktop.exe'),
        (Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'),
        'D:\DockerDesktop\Docker Desktop.exe'
    )
    $dockerDesktop = $null
    foreach ($candidate in $dockerDesktopCandidates) {
        if (Test-Path $candidate) {
            $dockerDesktop = $candidate
            break
        }
    }
    if (Test-Path $dockerDesktop) {
        Write-Host 'Docker Desktop 未运行，正在启动...' -ForegroundColor Yellow
        Start-Process -FilePath $dockerDesktop
        for ($attempt = 1; $attempt -le 60; $attempt++) {
            Start-Sleep -Seconds 2
            if (Test-DockerEngine) {
                break
            }
        }
    }
    if (-not (Test-DockerEngine)) {
    throw 'Docker 引擎不可用，请先启动 Docker Desktop 后重试。'
    }
}

if (-not (Test-Path $composeFile)) {
    throw "未找到 Compose 文件：$composeFile"
}

# Compose 会在后端容器创建时校验这三个变量；只保存在本次 PowerShell 进程中。
$null = Read-RequiredSecret 'DASHSCOPE_API_KEY' '请输入 DASHSCOPE_API_KEY（不会写入文件）'
if ([string]::IsNullOrWhiteSpace($env:APP_ADMIN_USERNAME)) {
    $env:APP_ADMIN_USERNAME = 'xiaow'
}
$null = Read-RequiredSecret 'APP_ADMIN_PASSWORD' '请输入 APP_ADMIN_PASSWORD（不会写入文件）'

Write-Host "`n[1/3] 启动 Docker 服务..." -ForegroundColor Cyan
$composeArgs = @('-p', 'agent_robot', '-f', $composeFile, 'up', '-d')
if (-not $SkipBuild) {
    $composeArgs += '--build'
}
& docker compose @composeArgs
if ($LASTEXITCODE -ne 0) {
    throw 'Docker Compose 启动失败，请查看上方日志。'
}

Write-Host '[2/3] 等待 FastAPI 健康检查...' -ForegroundColor Cyan
$healthUrl = 'http://127.0.0.1:8001/api/v1/health/ready'
$healthy = $false
for ($attempt = 1; $attempt -le 30; $attempt++) {
    try {
        $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 3
        if ($response.StatusCode -eq 200) {
            $healthy = $true
            break
        }
    }
    catch {
        Start-Sleep -Seconds 2
    }
}
if (-not $healthy) {
    docker compose -p agent_robot -f $composeFile ps
    throw "FastAPI 健康检查超时：$healthUrl"
}
Write-Host 'FastAPI、PostgreSQL、Redis 已就绪。' -ForegroundColor Green

if ($NoFrontend) {
    Write-Host "`n后端已启动：$healthUrl" -ForegroundColor Green
    exit 0
}

Write-Host '[3/3] 准备并启动 Vue 前端...' -ForegroundColor Cyan
$frontendUrl = 'http://127.0.0.1:5174'
$frontendAlreadyRunning = $false
try {
    $frontendAlreadyRunning = (Invoke-WebRequest -Uri $frontendUrl -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200
}
catch {
    $frontendAlreadyRunning = $false
}

if ($frontendAlreadyRunning) {
    Write-Host "Vue 前端已在运行：$frontendUrl" -ForegroundColor Green
}
elseif (-not (Test-Path (Join-Path $frontendRoot 'node_modules\.bin\vite.cmd'))) {
    $npmCache = 'D:\codex_store\npm-cache'
    New-Item -ItemType Directory -Force -Path $npmCache | Out-Null
    Push-Location $frontendRoot
    try {
        & npm ci --cache $npmCache
        if ($LASTEXITCODE -ne 0) {
            throw 'npm ci 失败。'
        }
    }
    finally {
        Pop-Location
    }
}

if (-not $frontendAlreadyRunning) {
    $shellExecutableName = if ($PSVersionTable.PSEdition -eq 'Core') { 'pwsh.exe' } else { 'powershell.exe' }
    $shellPath = Join-Path $PSHOME $shellExecutableName
    $frontendLauncher = Join-Path $PSScriptRoot 'start-agent-robot-frontend.ps1'
    if (-not (Test-Path $shellPath)) {
        throw "未找到当前 PowerShell 可执行文件：$shellPath"
    }
    if (-not (Test-Path $frontendLauncher)) {
        throw "未找到前端启动脚本：$frontendLauncher"
    }
    Start-Process -FilePath $shellPath -WorkingDirectory $frontendRoot -ArgumentList @(
        '-NoExit',
        '-ExecutionPolicy', 'Bypass',
        '-File', $frontendLauncher
    )
}

Write-Host "`n启动完成：" -ForegroundColor Green
Write-Host "前端：$frontendUrl"
Write-Host '后端：http://localhost:8001/api/v1'
Write-Host '关闭后端：docker compose -p agent_robot down'
