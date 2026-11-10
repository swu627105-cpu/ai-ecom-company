const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const http = require('node:http');
const { spawn } = require('node:child_process');
const { randomUUID } = require('node:crypto');

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      server.close(error => error ? reject(error) : resolve(port));
    });
  });
}

function health(url, instance) {
  return new Promise(resolve => {
    const request = http.get(`${url}/health`, response => {
      let body = '';
      response.on('data', chunk => { body += chunk; });
      response.on('end', () => {
        try {
          const data = JSON.parse(body);
          resolve(response.statusCode === 200 && data.app === 'ai-ecom-platform' && data.desktop_instance === instance);
        } catch { resolve(false); }
      });
    });
    request.setTimeout(900, () => request.destroy());
    request.on('error', () => resolve(false));
  });
}

async function startBackend({ python, apiDir, webDir, dataDir, logDir, timeout = 60000 }) {
  for (const file of [python, path.join(apiDir, 'desktop_entry.py'), path.join(webDir, 'index.html')]) {
    if (!fs.existsSync(file)) throw new Error(`缺少运行文件：${file}`);
  }
  fs.mkdirSync(dataDir, { recursive: true });
  fs.mkdirSync(logDir, { recursive: true });
  const port = await freePort();
  const instance = randomUUID();
  const url = `http://127.0.0.1:${port}`;
  const logFile = path.join(logDir, 'backend.log');
  const log = fs.openSync(logFile, 'a');
  let child;
  try {
    child = spawn(python, [path.join(apiDir, 'desktop_entry.py'), '--port', String(port), '--web', webDir, '--data', dataDir, '--instance', instance], {
      cwd: apiDir, windowsHide: true, stdio: ['ignore', log, log],
      env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', PYTHONDONTWRITEBYTECODE: '1' }
    });
  } finally { fs.closeSync(log); }
  let error = null;
  let stopping = false;
  child.once('error', value => { error = value; });
  const stop = () => {
    if (stopping || child.exitCode !== null || child.signalCode) return;
    stopping = true;
    child.kill(); // 仅终止本窗口创建的后端，不影响浏览器版服务。
  };
  try {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      if (error) throw error;
      if (child.exitCode !== null || child.signalCode) throw new Error(`后端提前退出（${child.exitCode ?? child.signalCode}），日志：${logFile}`);
      if (await health(url, instance)) return { url, child, stop, logFile };
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    throw new Error(`后端启动超时，日志：${logFile}`);
  } catch (value) { stop(); throw value; }
}

module.exports = { startBackend, freePort, health };
