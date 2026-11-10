# AI 电商公司 · Windows APP

## 直接运行便携版

适用 Windows 10/11 64 位（x64）。下载 `AI-Ecom-Company-0.1.0-Windows-x64.zip`，右键「全部解压」，将整个目录放在固定位置，再双击 `AI-Ecom-Company.exe` 或 `Start-App.cmd`。不要只拖出 EXE，也不要在 ZIP 预览中启动。

APP 已包含 Electron、Python、前端和后端依赖，运行时无需安装 Node.js、Python 或另开终端。首次启动等待本地服务就绪后显示原有 AI 电商公司运营台。它使用随机本地端口；与浏览器开发版独立运行。重复启动会激活已有窗口，关闭应用会关闭它自己创建的后端。

可双击 `Create-Shortcut.cmd` 创建桌面快捷方式。快捷方式指向当前解压位置，之后不要单独移动或删除该文件夹。

这是便携应用，不是已签名的安装程序。本项目未提供代码签名证书；Windows 可能显示未知发布者提示。分发方应在正式交付前完成签名与目标机器验收。

## 首次配置和演示

打开「模型配置」，填写自己的书生 API 地址、模型与 Key。使用真实书生分析需要联网；没有密钥也可用「规则策略演练」运行本地模拟流程。应用不包含开发机的模型密钥、用户会话或已授权店铺。

公司频道点击「经营救援演示」，可选择库存、广告、退款、联合救援或现金不足案例；在原审批中心批准，再在原任务列表和频道中查看结果，录入反馈进入下一轮。店铺授权与业务动作保持模拟。

包内「演示证据」是只读合成案例记录，不会自动导入当前运行状态。比赛讲述见同目录的 `比赛演示与答辩.md`。

## 数据、升级和故障排查

- 配置和业务记录：`%APPDATA%\AI-Ecom-Company\data\`。
- 后端启动日志：`%APPDATA%\AI-Ecom-Company\logs\backend.log`。
- `settings.json` 含自己的模型密钥，备份时妥善保管，不发送给评委或提交到仓库。
- 升级时先关闭应用，解压新版本到新目录后启动，原用户数据继续保留。删除便携程序目录不会自动删除用户数据。
- 启动失败先检查是否完整解压，再查看日志；不要单独运行 `resources/python/python.exe` 作为应用入口。
- 正在运行的模型请求与未完成任务不会在重启后静默重放，请从原平台查看中断状态并继续或回滚。

## Windows 本机验收

在解压目录打开 PowerShell，执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Smoke-Test.ps1
```

它会使用独立临时目录，不读现有用户配置，验证 Python、Windows 时区、前端页面、规则规划、人工批准、业务执行和回滚。成功显示 `PASS`，日志位置会打印。该脚本不验证书生网络连通或 Electron 窗口交互，请再手动打开 APP 验收。

当前构建主机为 macOS。构建过程检查官方运行包哈希、所有本地扩展的 Windows x64 架构，并在本机构建测试共用代码；未把 macOS 测试当作 Windows 本机运行通过。

## 源码版 Windows 本地运行

安装 Node.js 22 LTS 或更新版本、64 位 Python 3.11+（建议3.13，并安装 Python Launcher），重新打开终端。

双击源码目录 `scripts\windows\Start-Local.cmd`：首次安装项目依赖，自动启动 API 与 Vite 并打开浏览器。终端保持开启；按 Ctrl+C 关闭本脚本启动的服务，不关闭此前已有的服务。

双击 `scripts\windows\Start-Desktop.cmd`：安装依赖、构建前端并启动源码桌面版，后端由桌面应用管理。其用户数据与浏览器开发版分开。

可在 PowerShell 使用参数：

```powershell
# 已安装依赖后跳过重复安装
.\scripts\windows\Start-Local.ps1 -SkipInstall
# 不自动打开浏览器
.\scripts\windows\Start-Local.ps1 -NoBrowser
# 源码桌面版
.\scripts\windows\Start-Local.ps1 -Desktop
```

浏览器版运行于 `http://localhost:5173/`，后端为 `http://127.0.0.1:8000/`；启动日志位于源码 `logs`。首次依赖安装需要联网，脚本不自动修改系统 Python 或 Node.js。

macOS/Linux 源码浏览器版仍可运行 `bash scripts/start-local.sh`。

## 重新构建 Windows APP

先在源码目录运行 `npm ci`，然后双击 `scripts\windows\Build-Windows.cmd`。macOS/Linux 可执行 `python3 scripts/build-windows.py`，也可统一执行 `npm run build:windows`。

构建会下载官方 Windows x64 Electron、CPython 嵌入式包和 PyPI wheel，把依赖作为应用组成部分随包携带，不在用户电脑运行 pip。Python嵌入式方案参照 [Python官方说明](https://docs.python.org/3.13/using/windows.html#the-embeddable-package)。

构建产物位于 `release`，ZIP旁边提供 SHA256 文件，包内 `build-manifest.json` 记录运行时版本与依赖哈希。完整 wheel 版本与哈希锁定在 `scripts/windows/requirements-runtime.lock.txt`。下载缓存位于 `build/windows-cache`，可用于重复构建。重新生成依赖锁时应先清空 wheel 缓存，避免混入多个版本。

该构建不会导入 `apps/api/data`、`.env`、settings.json、state.json 或开发机依赖目录。
