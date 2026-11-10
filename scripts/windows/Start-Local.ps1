param([switch]$Desktop, [switch]$SkipInstall, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location -LiteralPath $Root
$Owned = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()
$LogDir = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Run-Checked([string]$File, [string[]]$Arguments) {
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $File $Arguments (exit $LASTEXITCODE)" }
}
function Health([string]$Url, [switch]$Api) {
    try {
        if ($Api) {
            $r = Invoke-RestMethod -Uri $Url -TimeoutSec 2
            return ($r.ok -eq $true -and $r.app -eq 'ai-ecom-platform')
        }
        return ((Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200)
    } catch { return $false }
}
function Wait-Ready([string]$Url, [System.Diagnostics.Process]$Process, [switch]$Api) {
    $end = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $end) {
        if ($Process.HasExited) { throw "Service exited. See logs: $LogDir" }
        if (Health $Url -Api:$Api) { return }
        Start-Sleep -Milliseconds 300
    }
    throw "Service timeout: $Url. See logs: $LogDir"
}
try {
    if (-not (Get-Command node -ErrorAction SilentlyContinue) -or -not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        throw 'Please install Node.js 22 LTS or newer, then reopen this script.'
    }
    $Python = Join-Path $Root 'apps\api\.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $Python)) {
        if ($SkipInstall) { throw 'Python virtual environment is missing; run without -SkipInstall.' }
        if (Get-Command py -ErrorAction SilentlyContinue) {
            $PythonTag = $null
            foreach ($tag in @('-3.13', '-3.12', '-3.11')) {
                try {
                    & py $tag -c 'import sys' 2>$null
                    if ($LASTEXITCODE -eq 0) { $PythonTag = $tag; break }
                } catch { }
            }
            if ($null -eq $PythonTag) { throw 'Please install 64-bit Python 3.13 (3.11-3.13 supported).' }
            Run-Checked 'py' @($PythonTag, '-m', 'venv', 'apps/api/.venv')
        } elseif (Get-Command python -ErrorAction SilentlyContinue) {
            Run-Checked 'python' @('-m', 'venv', 'apps/api/.venv')
        } else { throw 'Please install 64-bit Python 3.13 with the Python launcher.' }
    }
    Run-Checked $Python @('-c', "import sys; assert (3,11) <= sys.version_info[:2] <= (3,13), 'Python 3.11-3.13 required'")
    if (-not $SkipInstall) {
        Run-Checked $Python @('-m', 'pip', 'install', '-r', 'apps/api/requirements-windows.txt')
        Run-Checked 'npm.cmd' @('ci')
    }
    if ($Desktop) {
        Run-Checked 'npm.cmd' @('run', 'build')
        Run-Checked 'npm.cmd' @('run', 'desktop')
        exit 0
    }
    if (-not (Health 'http://127.0.0.1:8000/health' -Api)) {
        # 只记录并关闭本脚本启动的进程；端口冲突由服务启动失败明确报告。
        $p = Start-Process -FilePath $Python -ArgumentList @('-m','uvicorn','app.main:app','--app-dir','apps/api','--host','127.0.0.1','--port','8000') -WorkingDirectory $Root -PassThru -RedirectStandardOutput (Join-Path $LogDir 'api.out.log') -RedirectStandardError (Join-Path $LogDir 'api.err.log')
        $Owned.Add($p)
        Wait-Ready 'http://127.0.0.1:8000/health' $p -Api
    }
    if (-not (Health 'http://localhost:5173/')) {
        $vite = '"' + (Join-Path $Root 'node_modules\vite\bin\vite.js') + '"'
        $p = Start-Process -FilePath (Get-Command node).Source -ArgumentList @($vite,'--host','localhost','--port','5173','--strictPort') -WorkingDirectory (Join-Path $Root 'apps\web') -PassThru -RedirectStandardOutput (Join-Path $LogDir 'web.out.log') -RedirectStandardError (Join-Path $LogDir 'web.err.log')
        $Owned.Add($p)
        Wait-Ready 'http://localhost:5173/' $p
    }
    Write-Host 'Running: http://localhost:5173/   Press Ctrl+C to stop services started here.'
    if (-not $NoBrowser) { Start-Process 'http://localhost:5173/' }
    while ($true) {
        foreach ($p in $Owned) { if ($p.HasExited) { throw "Service exited. See logs: $LogDir" } }
        Start-Sleep -Seconds 1
    }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
} finally {
    foreach ($p in $Owned) {
        if (-not $p.HasExited) { & taskkill.exe /PID $p.Id /T /F 2>$null | Out-Null }
    }
}
