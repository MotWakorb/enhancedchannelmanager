import fs from 'node:fs';
import path from 'node:path';
import net from 'node:net';
import { spawn, execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';

const root = path.resolve(import.meta.dirname, '..');
const evidence = process.env.DESKTOP_EVIDENCE_DIR;
const guard = process.env.DESKTOP_GUARD_DIR || path.join(root, 'scripts/desktop-guard');
const python = process.env.DESKTOP_PYTHON;
if (!evidence || !guard || !python) throw new Error('Set DESKTOP_EVIDENCE_DIR, DESKTOP_GUARD_DIR and DESKTOP_PYTHON');
fs.mkdirSync(evidence, { recursive: true });
const run = fs.mkdtempSync(path.join(evidence, 'run-'));
let source = root;
const port = 19863;
async function prepare() {
if (process.env.DESKTOP_BASELINE === 'true') {
  source = path.join(run, 'source');
  fs.mkdirSync(source);
  const archive = execFileSync('git', ['archive', process.env.DESKTOP_BASE_REV || 'd5334fce3f41ed754e8105d537ddd3ea31ee441b'], { cwd: root, maxBuffer: 100 * 1024 * 1024 });
  execFileSync('tar', ['-x', '-C', source], { input: archive });
  await runCommand('install', 'npm', ['ci', '--ignore-scripts', '--no-audit', '--no-fund', '--cache', path.join(run, 'npm-cache')], { cwd: path.join(source, 'frontend'), stdio: 'inherit' });
}
for (const name of ['data', 'home', 'tmp']) fs.mkdirSync(path.join(run, name));
await new Promise((resolve, reject) => {
  const probe = net.createServer();
  probe.once('error', reject);
  probe.listen(port, '127.0.0.1', () => probe.close(resolve));
});
// Invoke the build tool directly, not an npm shell that could outlive cancellation.
await runCommand('build', process.execPath, [path.join(source, 'frontend/node_modules/vite/bin/vite.js'), 'build'], { cwd: path.join(source, 'frontend'), stdio: 'inherit' });
fs.cpSync(path.join(source, 'frontend/dist'), path.join(source, 'backend/static'), { recursive: true });
const patch = execFileSync('git', ['diff', '--binary', 'HEAD'], { cwd: root });
fs.writeFileSync(path.join(run, 'worktree.patch'), patch);
fs.writeFileSync(path.join(run, 'source.json'), JSON.stringify({ source, baseline: source !== root,
  revision: source !== root ? process.env.DESKTOP_BASE_REV || 'd5334fce3f41ed754e8105d537ddd3ea31ee441b' : 'worktree',
  head: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim(),
  patchSHA256: createHash('sha256').update(patch).digest('hex'),
  testSHA256: createHash('sha256').update(fs.readFileSync(path.join(root, 'e2e/desktop-visual-repairs.spec.ts'))).digest('hex'),
}, null, 2));
const env = {
  PATH: process.env.PATH, HOME: path.join(run, 'home'), TMPDIR: path.join(run, 'tmp'),
  LANG: 'C.UTF-8', CONFIG_DIR: path.join(run, 'data'), MCP_SECRETS_DIR: path.join(run, 'data'),
  PYTHONPATH: guard, PYTHONDONTWRITEBYTECODE: '1', AUDIT_GUARD_LOG: path.join(run, 'backend-egress.jsonl'),
};
execFileSync(python, ['-c', "import os,socket; assert os.environ['AUDIT_GUARD_ACTIVE']=='1'; s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen();\ntry: socket.create_connection(('127.0.0.1',9))\nexcept PermissionError: print('Outbound denied; bind allowed')\nelse: raise AssertionError('Outbound permitted')"], { env, stdio: 'inherit' });
execFileSync(python, ['-c', `import sys
for event in ('socket.connect', 'socket.getaddrinfo', 'socket.sendto', 'subprocess.Popen', 'os.system', 'os.posix_spawn'):
    try: sys.audit(event, 'synthetic guard self-test')
    except PermissionError: pass
    else: raise AssertionError('Guard permitted ' + event)
print('All six egress/subprocess audit events denied')`], { env, stdio: 'inherit' });
return env;
}
let fd;
const children = [];
let cancelled;
let cancel;
const cancellation = new Promise(resolve => { cancel = resolve; });
const onSignal = signal => { cancelled ??= signal; cancel(); };
const onInterrupt = () => onSignal('SIGINT');
const onTerminate = () => onSignal('SIGTERM');
process.on('SIGINT', onInterrupt);
process.on('SIGTERM', onTerminate);
function ownedSpawn(name, command, args, options) {
  if (cancelled) throw new Error(`Cancelled by ${cancelled}`);
  const child = spawn(command, args, options);
  const record = { name, child, settled: false };
  // Observe errors immediately, including ENOENT before an exit can be emitted.
  record.done = new Promise(resolve => {
    child.on('error', error => {
      record.error = error.message;
      if (!child.pid) { record.settled = true; resolve(); }
    });
    child.once('exit', (code, signal) => { record.code = code; record.signal = signal; record.settled = true; resolve(); });
  });
  children.push(record);
  return record;
}
async function runCommand(name, command, args, options) {
  const record = ownedSpawn(name, command, args, options);
  await Promise.race([record.done, cancellation]);
  if (cancelled) throw new Error(`Cancelled by ${cancelled}`);
  if (record.error || record.code !== 0) throw new Error(record.error || `${name} exited with ${record.code ?? record.signal}`);
}
async function boundedWait(record) {
  let timer;
  try {
    await Promise.race([record.done, new Promise(resolve => { timer = setTimeout(resolve, 5000); })]);
  } finally { clearTimeout(timer); }
}
let status = 1;
try {
  const env = await prepare();
  fd = fs.openSync(path.join(run, 'backend.log'), 'w');
  const backend = ownedSpawn('backend', python, ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', String(port), '--loop', 'asyncio', '--no-access-log'], {
    cwd: path.join(source, 'backend'), env, stdio: ['ignore', fd, fd],
  });
  let ready = false;
  for (let attempt = 0; attempt < 60; attempt++) {
    if (cancelled) throw new Error(`Cancelled by ${cancelled}`);
    if (backend.settled) throw new Error(backend.error || 'Backend exited during startup');
    try {
      const response = await fetch(`http://127.0.0.1:${port}/api/auth/setup-required`, { signal: AbortSignal.timeout(1000) });
      if (response.ok) { ready = true; break; }
    } catch { /* Poll only this owned process. */ }
    await Promise.race([cancellation, new Promise(resolve => setTimeout(resolve, 500))]);
  }
  if (cancelled) throw new Error(`Cancelled by ${cancelled}`);
  if (!ready) throw new Error('Backend readiness failed');
  const test = ownedSpawn('playwright', process.execPath, ['node_modules/@playwright/test/cli.js', 'test', 'e2e/desktop-visual-repairs.spec.ts', '--project=chromium', '--workers=1', '--retries=0', '--reporter=list,json', `--output=${path.join(run, 'browser')}`, ...process.argv.slice(2)], {
    cwd: root, env: { ...process.env, PLAYWRIGHT_JSON_OUTPUT_FILE: path.join(run, 'playwright.json'), E2E_BASE_URL: `http://127.0.0.1:${port}`, E2E_START_SERVER: 'false', E2E_EXACT_BUILD: 'false' }, stdio: 'inherit',
  });
  await Promise.race([test.done, cancellation]);
  if (test.error) throw new Error(test.error);
  status = test.code ?? 1;
} catch (error) {
  console.error(error);
} finally {
  // Playwright gets SIGINT so it can close its workers/browsers before escalation.
  for (const record of [...children].reverse()) {
    if (!record.settled) record.child.kill(record.name === 'playwright' ? 'SIGINT' : 'SIGTERM');
    await boundedWait(record);
    if (!record.settled) {
      record.escalated = true;
      record.child.kill('SIGKILL');
      await boundedWait(record);
      if (!record.settled) record.child.unref();
    }
  }
  const stopped = children.every(record => record.settled);
  if (cancelled) status = cancelled === 'SIGINT' ? 130 : 143;
  if (!stopped) status = 1;
  if (fd !== undefined) fs.closeSync(fd);
  const backend = children.find(record => record.name === 'backend');
  fs.writeFileSync(path.join(run, 'result.json'), JSON.stringify({ status, port, cancelled,
    backendPID: backend?.child.pid, backendExitCode: backend?.code, backendSignal: backend?.signal, stopped,
    children: children.map(({ name, child, code, signal, error, settled, escalated }) => ({ name, pid: child.pid, code, signal, error, stopped: settled, escalated: !!escalated })),
  }, null, 2));
  console.log(`Desktop visual result: ${status}; evidence: ${run}; owned children stopped: ${stopped}`);
  process.removeListener('SIGINT', onInterrupt);
  process.removeListener('SIGTERM', onTerminate);
}
process.exitCode = status;
