const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');
const os = require('node:os');
const { once } = require('node:events');
const { startBackend, health } = require('../backend.cjs');
const root = path.resolve(__dirname, '../../..');

test('desktop backend serves original UI and isolates persistent simulation data', { timeout: 30000 }, async () => {
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'aiecom-desktop-'));
  const runtime = await startBackend({
    python: path.join(root, 'apps/api/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'),
    apiDir: path.join(root, 'apps/api'), webDir: path.join(root, 'apps/web/dist'),
    dataDir: path.join(temp, '用户 data'), logDir: path.join(temp, 'logs')
  });
  try {
    const request = async (route, body) => {
      const response = await fetch(runtime.url + route, body === undefined ? {} : { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
      assert.equal(response.ok, true, await response.clone().text());
      return response.json();
    };
    const response = await fetch(runtime.url);
    assert.equal(response.status, 200);
    assert.match(await response.text(), /<div id="root">/);
    assert.equal(await health(runtime.url, 'incorrect-instance'), false);
    const model = await request('/api/model');
    assert.equal(model.has_key, false, 'desktop test must not inherit developer .env key');
    const run = await request('/api/demo/runs', { mode: 'rehearsal' });
    async function waitRun(states) {
      for (let i = 0; i < 80; i++) {
        const result = await request('/api/demo/runs/' + run.id);
        if (states.includes(result.status)) return result;
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      assert.fail('Run did not reach expected state');
    }
    const pending = await waitRun(['awaiting_approval', 'failed', 'blocked']);
    assert.equal(pending.status, 'awaiting_approval');
    await request('/api/demo/runs/' + run.id + '/approve', {});
    const done = await waitRun(['completed', 'verification_failed', 'failed']);
    assert.equal(done.status, 'completed');
    assert.equal(done.current_metrics.stock, 480);
    assert.equal(done.current_metrics.ticket_count, 30);
    const restored = await request('/api/demo/runs/' + run.id + '/rollback', {});
    assert.equal(restored.rollback_verified, true);
    const persisted = JSON.parse(await fs.readFile(path.join(temp, '用户 data/state.json'), 'utf8'));
    assert.equal(persisted.demo_runs[run.id].status, 'rolled_back');
  } finally {
    const exited = once(runtime.child, 'exit');
    runtime.stop();
    await exited;
    assert.equal(runtime.child.exitCode !== null || Boolean(runtime.child.signalCode), true);
    await fs.rm(temp, { recursive: true, force: true });
  }
});

test('missing runtime fails before starting a process', async () => {
  await assert.rejects(startBackend({ python: '/missing/python', apiDir: '/missing', webDir: '/missing', dataDir: '/missing', logDir: '/missing' }), /缺少运行文件/);
});
