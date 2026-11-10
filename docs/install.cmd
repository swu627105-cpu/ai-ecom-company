@echo off
chcp 65001 >nul
title AI 电商公司平台 · 一键安装
cd /d "%~dp0"
cls
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; $u = 'https://swu627105-cpu.github.io/ai-ecom-company/install.ps1'; try { $s = (New-Object System.Net.WebClient).DownloadString($u) } catch { Write-Host '  无法下载安装脚本，请检查网络后重试。' -ForegroundColor Red; exit 1 }; Invoke-Expression $s"
echo.
pause
