const { spawnSync } = require('node:child_process');
const path = require('node:path');
const command = process.platform === 'win32' ? 'py' : 'python3';
const args = [...(process.platform === 'win32' ? ['-3'] : []), path.join(__dirname, 'build-windows.py'), ...process.argv.slice(2)];
const result = spawnSync(command, args, { stdio: 'inherit' });
if (result.error) console.error('需要 Python 3.11+，Windows 请安装 Python Launcher。', result.error.message);
process.exit(result.status ?? 1);
