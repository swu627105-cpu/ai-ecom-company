const { app, BrowserWindow, ipcMain, Notification, shell, nativeTheme, dialog } = require('electron');
const path = require('node:path');
const { startBackend } = require('./backend.cjs');

app.setName('AI电商公司');
if (process.platform === 'win32') app.setAppUserModelId('cn.aiecom.company');
app.setPath('userData', path.join(app.getPath('appData'), 'AI-Ecom-Company'));
const DEV_URL = !app.isPackaged ? process.env.ELECTRON_START_URL || '' : '';
let win = null;
let backend = null;
let quitting = false;
let starting = false;

function openExternal(url) {
  if (/^https?:\/\//i.test(url)) void shell.openExternal(url);
}

async function createWindow() {
  if (starting || quitting || win) return;
  starting = true;
  try {
    if (!DEV_URL && !backend) {
      const root = path.resolve(__dirname, '../..');
      backend = await startBackend({
        python: app.isPackaged ? path.join(process.resourcesPath, 'python', 'python.exe') : process.env.AIECOM_PYTHON || path.join(root, 'apps/api/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'),
        apiDir: app.isPackaged ? path.join(process.resourcesPath, 'api') : path.join(root, 'apps/api'),
        webDir: app.isPackaged ? path.join(process.resourcesPath, 'web') : path.join(root, 'apps/web/dist'),
        dataDir: path.join(app.getPath('userData'), 'data'),
        logDir: path.join(app.getPath('userData'), 'logs')
      });
      backend.child.once('exit', () => {
        if (quitting) return;
        backend = null;
        dialog.showErrorBox('本地服务已退出', '请重新启动应用。日志位于用户目录 AI-Ecom-Company/logs/backend.log；已保存的运行可从检查点继续。');
        app.quit();
      });
    }
    if (quitting) { backend?.stop(); return; }
    const origin = DEV_URL || backend.url;
    win = new BrowserWindow({
      width: 1440, height: 900, minWidth: 1080, minHeight: 720, title: 'AI 电商公司',
      backgroundColor: nativeTheme.shouldUseDarkColors ? '#0f141b' : '#f2f4f7',
      ...(process.platform === 'darwin' ? { titleBarStyle: 'hiddenInset', trafficLightPosition: { x: 14, y: 14 } } : {}),
      webPreferences: { preload: path.join(__dirname, 'preload.cjs'), additionalArguments: [`--aiecom-api-base=${DEV_URL ? 'http://localhost:8000' : origin}`], contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true }
    });
    win.removeMenu();
    win.webContents.setWindowOpenHandler(({ url }) => { openExternal(url); return { action: 'deny' }; });
    win.webContents.on('will-navigate', (event, url) => {
      if (new URL(url).origin !== new URL(origin).origin) { event.preventDefault(); openExternal(url); }
    });
    win.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    win.on('closed', () => { win = null; });
    await win.loadURL(origin);
  } catch (error) {
    dialog.showErrorBox('AI 电商公司启动失败', `${error.message}\n\n源码版请先运行本地启动脚本安装依赖并构建前端。便携版请完整解压后再启动。`);
    app.quit();
  } finally { starting = false; }
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { if (win) { if (win.isMinimized()) win.restore(); win.show(); win.focus(); } });
  app.whenReady().then(createWindow);
  app.on('activate', () => { if (!win) void createWindow(); });
}
app.on('before-quit', () => { quitting = true; backend?.stop(); });
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });

ipcMain.handle('app:version', () => app.getVersion());
ipcMain.handle('app:notify', (event, payload) => {
  if (event.sender !== win?.webContents) return false;
  const { title, body } = payload || {};
  if (!Notification.isSupported() || !title) return false;
  new Notification({ title: String(title).slice(0, 200), body: String(body || '').slice(0, 1000), silent: false }).show();
  return true;
});
