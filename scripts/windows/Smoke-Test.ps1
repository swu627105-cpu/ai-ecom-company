$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Python = Join-Path $Root 'resources\python\python.exe'
$Entry = Join-Path $Root 'resources\api\desktop_entry.py'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Run this script from the extracted APP folder.' }
# 使用独立临时沙箱，验收不会修改应用现有数据或模型密钥。
$Temp = Join-Path ([IO.Path]::GetTempPath()) ('aiecom-smoke-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Temp | Out-Null
$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$listener.Start()
$Port = $listener.LocalEndpoint.Port
$listener.Stop()
$Url = "http://127.0.0.1:$Port"
$Id = [guid]::NewGuid().ToString('N')
$proc = $null
function Request([string]$Path, [string]$Method = 'GET', [object]$Body = $null) {
    $options = @{ Uri = "$Url$Path"; Method = $Method; TimeoutSec = 10 }
    if ($null -ne $Body) { $options.ContentType = 'application/json'; $options.Body = ($Body | ConvertTo-Json -Depth 20 -Compress) }
    return Invoke-RestMethod @options
}
function Wait-Run([string]$RunId, [string[]]$States) {
    for ($i = 0; $i -lt 100; $i++) {
        $r = Request "/api/demo/runs/$RunId"
        if ($States -contains $r.status) { return $r }
        Start-Sleep -Milliseconds 200
    }
    throw "Run did not reach expected state: $RunId"
}
try {
    & $Python -c "import ssl,uvicorn,pydantic,langgraph; from zoneinfo import ZoneInfo; print(ZoneInfo('Asia/Shanghai'))"
    if ($LASTEXITCODE -ne 0) { throw 'Python runtime dependency check failed.' }
    $QuotedEntry = '"{0}"' -f $Entry
    $QuotedWeb = '"{0}"' -f (Join-Path $Root 'resources\web')
    $QuotedData = '"{0}"' -f (Join-Path $Temp 'data')
    $argsList = @($QuotedEntry, '--port', "$Port", '--web', $QuotedWeb, '--data', $QuotedData, '--instance', $Id)
    $proc = Start-Process -FilePath $Python -ArgumentList $argsList -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $Temp 'stdout.log') -RedirectStandardError (Join-Path $Temp 'stderr.log')
    $ready = $false
    for ($i = 0; $i -lt 150; $i++) {
        if ($proc.HasExited) { throw "Backend exited; see $Temp" }
        try { $h = Request '/health'; if ($h.desktop_instance -eq $Id) { $ready = $true; break } } catch { }
        Start-Sleep -Milliseconds 200
    }
    if (-not $ready) { throw "Backend startup timeout; see $Temp" }
    if ((Invoke-WebRequest "$Url/" -UseBasicParsing).StatusCode -ne 200) { throw 'Frontend unavailable.' }
    $run = Request '/api/demo/runs' 'POST' @{ mode = 'rehearsal' }
    $waiting = Wait-Run $run.id @('awaiting_approval','blocked','failed')
    if ($waiting.status -ne 'awaiting_approval') { throw 'Rule planning failed.' }
    $null = Request "/api/demo/runs/$($run.id)/approve" 'POST'
    $done = Wait-Run $run.id @('completed','verification_failed','failed')
    if ($done.status -ne 'completed' -or $done.current_metrics.coverage_days -ne 8 -or $done.current_metrics.ticket_count -ne 30) { throw 'Execution verification failed.' }
    $rolled = Request "/api/demo/runs/$($run.id)/rollback" 'POST'
    if (-not $rolled.rollback_verified) { throw 'Rollback verification failed.' }
    Write-Host 'PASS: runtime, timezone, frontend, planning, approval, execution, rollback.' -ForegroundColor Green
    Write-Host "Test logs: $Temp"
} finally {
    if ($null -ne $proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
}
