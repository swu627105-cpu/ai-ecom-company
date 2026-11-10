# AI 电商公司平台 · AI-Ecom-Company

多智能体经营的电商公司：AI 员工开会、审批、执行、复盘，把一家电商公司的经营流程真正跑起来。

- 桌面端：Electron + React 19 + Vite
- 本地服务：FastAPI（内置 Python 运行时，开箱即用，无需自己装环境）

## 一键启动（Windows）

打开启动台，点绿色按钮即可拉起本机应用：

**https://swu627105-cpu.github.io/ai-ecom-company/**

首次在任何一台电脑上使用，需要先运行一次页面上的 `install.cmd`（约 10 秒，自动注册启动器）。

## 下载

最新版离线包见 [Releases](https://github.com/swu627105-cpu/ai-ecom-company/releases)。

解压后直接运行 `AI-Ecom-Company.exe`，目录内已包含 Python 运行时，不需要额外依赖。

## 目录结构

```
apps/web        React 前端
apps/desktop    Electron 主进程与本地服务守护
apps/api        FastAPI 后端（会议 / 审批 / 场景 / 评测）
packages/ui     设计令牌
scripts         构建与打包脚本
docs            文档 + GitHub Pages 启动台
```

## 本地开发

```bash
npm install
npm run dev        # 前端开发服务器
npm run desktop    # 桌面端（会自动拉起本地 API）
```

## 文档

- [平台使用说明](docs/平台使用说明.md)
- [零基础代码导读](docs/平台代码导读_零基础版.md)
- [数据字典](docs/数据字典.md)
- [比赛演示与答辩](docs/比赛演示与答辩.md)

## 在线原型

- [交互原型](https://swu627105-cpu.github.io/ai-ecom-company/prototypes/prototype.html)
- [功能清单与开发方案](https://swu627105-cpu.github.io/ai-ecom-company/prototypes/feature-plan.html)
