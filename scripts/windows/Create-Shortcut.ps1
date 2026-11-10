$ErrorActionPreference = 'Stop'
$exe = Join-Path $PSScriptRoot 'AI-Ecom-Company.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'Extract the complete APP folder first.' }
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'AI-Ecom-Company.lnk'))
$link.TargetPath = $exe
$link.WorkingDirectory = $PSScriptRoot
$link.Description = 'AI E-commerce Company'
$link.Save()
Write-Host 'Desktop shortcut created. Keep the extracted APP folder in this location.'
