const { spawn } = require('node:child_process');
const electron = require('electron');
const child = spawn(electron, ['.'], { stdio: 'inherit', env: { ...process.env, ELECTRON_START_URL: 'http://localhost:5173' } });
child.once('error', error => { console.error(error.message); process.exit(1); });
child.once('exit', code => process.exit(code ?? 1));
