# Start-SoakSamplerLoop.ps1 (codex-1npu.3.3)
# omniroute1 的 soak 采样循环：每 ~300 秒采样一次 Desktop 健康，攒 24h 覆盖。
# 合规：on-demand 手动循环，不注册计划任务、不改配置、不请求 Guardian allow。
# 停手：在 $StopFile 路径放一个文件即停；或 Ctrl+C。
[CmdletBinding()]
param(
    [int]$IntervalSeconds = 300,
    [string]$StopFile = 'F:\codex\reports\desktop-health-soak\STOP',
    [int]$MaxIterations = 0  # 0 = 不限
)

$ErrorActionPreference = 'Continue'
$sampler = 'F:\codex\tools\agent-system-map\Invoke-DesktopHealthSample.ps1'
$logDir = 'F:\codex\reports\desktop-health-soak'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'sampler-loop.log'

function Write-Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ss') $msg"
    Add-Content -Path $log -Value $line -Encoding UTF8
    Write-Host $line
}

Write-Log "soak sampler loop start (interval=${IntervalSeconds}s, stopFile=$StopFile)"
$iteration = 0
while ($true) {
    if (Test-Path -LiteralPath $StopFile) {
        Write-Log "stop file found, exiting"
        break
    }
    $iteration++
    try {
        & $sampler | Out-Null
        Write-Log "sample #$iteration ok"
    } catch {
        Write-Log "sample #$iteration error: $($_.Exception.Message)"
    }
    if ($MaxIterations -gt 0 -and $iteration -ge $MaxIterations) {
        Write-Log "max iterations reached, exiting"
        break
    }
    Start-Sleep -Seconds $IntervalSeconds
}
