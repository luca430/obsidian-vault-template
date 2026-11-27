import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { mkdtemp, mkdir, writeFile, chmod, readFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { Element, Modal, Notice, TFile, WorkspaceLeaf } from 'obsidian';
import Plugin, { COMMANDS, ResearchView } from '../src/main';
import { ACTION_ARGS, Runner, capture, prepare, Spawn } from '../src/execution';
import { OutputParser, outcome, progress, redact, Snapshot, statusDetails } from '../src/protocol';
import { defaults, defaultPython, loadSettings } from '../src/settings';

class Child extends EventEmitter {
  stdout = new PassThrough();
  stderr = new PassThrough();
  killed = false;
  kill(): boolean { this.killed = true; return true; }
}
const native = { ...defaults('linux'), mode: 'native' as const };
const noCheck = async () => {};
const neverSpawn: Spawn = () => { throw new Error('unexpected subprocess'); };
const calls: { command: string; args: string[]; options: object }[] = [];
function wslSpawn(translate = '/custom mount/OneDrive - Lab/café α', failing = ''): Spawn {
  return (command, args, options) => {
    calls.push({ command, args, options });
    const child = new Child();
    queueMicrotask(() => {
      const code = args.includes(failing) ? 1 : 0;
      if (args.includes('wslpath')) child.stdout.write(translate + '\r\n');
      if (args.includes('-0')) child.stdout.write('SECRET=not-for-display\0PATH=/usr/local/bin:/usr/bin:/bin\0');
      if (code) child.stderr.write('invalid configuration');
      child.emit('close', code, null);
    });
    return child as unknown as ReturnType<Spawn>;
  };
}

test('settings are local, opt in, bounded and platform dependent', () => {
  assert.equal(defaults('win32').mode, 'wsl');
  assert.equal(defaults('linux').mode, 'native');
  assert.equal(defaults('linux').startupSync, false);
  assert.equal(defaultPython(native, 'linux'), '.venv/bin/python');
  assert.equal(defaultPython(native, 'win32'), '.venv/Scripts/python.exe');
  assert.equal(defaultPython(defaults('win32'), 'win32'), '.venv/bin/python');
  assert.equal(loadSettings({ maxLogLines: 1, startupSync: 'yes', mode: 'shell' }, 'linux').maxLogLines, 20);
  assert.equal(loadSettings({ maxLogLines: NaN }, 'linux').maxLogLines, 200);
  assert.equal(loadSettings({ startupSync: 'yes' }, 'linux').startupSync, false);
  assert.equal(loadSettings({ maxLogLines: 90000 }, 'linux').maxLogLines, 2000);
});

test('native Linux uses an absolute project venv with literal space/Unicode/metacharacter arguments', async () => {
  const root = '/home/test/OneDrive café α $(touch NEVER)';
  const command = await prepare(root, native, 'linux', 'sync', neverSpawn, noCheck);
  assert.equal(command.executable, root + '/.venv/bin/python');
  assert.deepEqual(command.args, ['-u', root + '/scripts/research.py', '--vault', root, 'sync']);
  assert.equal(command.options.cwd, root);
  assert.equal(command.options.shell, false);
  assert.equal(command.options.env?.VIRTUAL_ENV, root + '/.venv');
  assert.ok(command.options.env?.PATH?.startsWith(root + '/.venv/bin:'));
});

test('native Windows selects Scripts/python.exe and never WSL or a shell', async () => {
  const root = 'R:\\OneDrive - University\\café α research';
  const command = await prepare(root, { ...native }, 'win32', 'dry', neverSpawn, noCheck);
  assert.equal(command.executable, root + '\\.venv\\Scripts\\python.exe');
  assert.equal(command.options.env?.VIRTUAL_ENV, root + '\\.venv');
  assert.ok(command.options.env?.PATH?.startsWith(root + '\\.venv\\Scripts;'));
  assert.deepEqual(command.args.slice(-2), ['sync', '--dry-run']);
  assert.equal(command.options.shell, false);
});

test('custom interpreter and script paths remain separate arguments and explicit --vault wins', async () => {
  const command = await prepare('/project', { ...native, python: '/env with space/bin/python3', script: 'tools/my cli.py' }, 'linux', 'status', neverSpawn, noCheck);
  assert.equal(command.executable, '/env with space/bin/python3');
  assert.deepEqual(command.args, ['-u', '/project/tools/my cli.py', '--vault', '/project', 'status']);
  assert.equal(command.options.env?.VIRTUAL_ENV, '/env with space');
});

test('WSL translates Windows paths without assuming drive/mount/distribution/user', async () => {
  calls.length = 0;
  const root = 'R:\\OneDrive - Lab\\café α $(echo hello)';
  const command = await prepare(root, defaults('win32'), 'win32', 'sync', wslSpawn());
  assert.deepEqual(calls[0].args, ['--cd', '~', '--exec', 'wslpath', '-a', '-u', root]);
  assert.equal(command.executable, 'wsl.exe');
  assert.equal(command.options.cwd, root);
  assert.equal(command.options.shell, false);
  assert.deepEqual(command.args.slice(0, 4), ['--cd', '/custom mount/OneDrive - Lab/café α', '--exec', '/usr/bin/env']);
  assert.ok(command.args.includes('PATH=/custom mount/OneDrive - Lab/café α/.venv/bin:/usr/local/bin:/usr/bin:/bin'));
  assert.ok(command.args.includes('VIRTUAL_ENV=/custom mount/OneDrive - Lab/café α/.venv'));
  assert.deepEqual(command.args.slice(-3), ['--vault', '/custom mount/OneDrive - Lab/café α', 'sync']);
  assert.ok(!command.args.some(arg => arg.includes('not-for-display')));
  assert.equal(calls.filter(call => call.args.includes('/usr/bin/test')).length, 5);
});

test('WSL uses the configured distribution consistently and supports custom Linux venvs', async () => {
  calls.length = 0;
  const command = await prepare('D:\\Vault', { ...defaults('win32'), distribution: 'Research Linux α', python: '/opt/my venv/bin/python' }, 'win32', 'doctor', wslSpawn('/mnt/d/Vault'));
  for (const call of [...calls, { args: command.args }]) assert.deepEqual(call.args.slice(0, 2), ['--distribution', 'Research Linux α']);
  assert.ok(command.args.includes('/opt/my venv/bin/python'));
  assert.ok(command.args.includes('VIRTUAL_ENV=/opt/my venv'));
});

test('invalid WSL distribution, failed path conversion and malformed paths fail before Python launches', async () => {
  await assert.rejects(prepare('C:\\Vault', defaults('win32'), 'win32', 'sync', wslSpawn('', 'wslpath')), /WSL validation failed/);
  for (const value of ['relative', '/mnt/c\n/evil', '']) {
    await assert.rejects(prepare('C:\\Vault', defaults('win32'), 'win32', 'sync', wslSpawn(value)), /invalid Linux path/);
  }
  await assert.rejects(prepare('C:\\Vault', { ...defaults('win32'), distribution: 'bad\nvalue' }, 'win32', 'sync'), /control characters/);
});

test('missing WSL Linux virtual environment is clearly reported', async () => {
  await assert.rejects(prepare('C:\\Vault', defaults('win32'), 'win32', 'sync', wslSpawn('/mnt/c/Vault', '-x')), /WSL Python executable/);
  await assert.rejects(prepare('C:\\Vault', { ...defaults('win32'), python: 'C:\\env\\python.exe' }, 'win32', 'sync', wslSpawn('/mnt/c/Vault')), /Linux path/);
});

test('invalid modes, relative vaults, newlines, tilde, script types and cross-platform paths fail', async () => {
  await assert.rejects(prepare('/vault', { ...native, mode: 'wsl' }, 'linux', 'sync'), /requires Windows/);
  await assert.rejects(prepare('relative', native, 'linux', 'sync'), /absolute/);
  for (const settings of [{ python: 'bad\npath' }, { python: '~/env/python' }, { script: 'cmd.exe' }, { python: 'C:\\env\\python.exe' }]) {
    await assert.rejects(prepare('/vault', { ...native, ...settings }, 'linux', 'sync', neverSpawn, noCheck));
  }
});

test('required project files and Python executable are checked without global fallback', async () => {
  const checked: string[] = [];
  await assert.rejects(prepare('/vault', native, 'linux', 'sync', neverSpawn, async file => {
    checked.push(file); throw new Error('ENOENT');
  }), /Python executable.*\.venv/);
  assert.deepEqual(checked, ['/vault/.venv/bin/python']);
  await assert.rejects(prepare('/vault', native, 'linux', 'doctor', neverSpawn, async file => {
    if (file.endsWith('config.yaml')) throw new Error('ENOENT');
  }), /project file.*config.yaml/);
});

test('default venv requires pyvenv.cfg; a custom system Python is not labeled a venv', async () => {
  const check = async (file: string) => { if (file.endsWith('pyvenv.cfg')) throw new Error('ENOENT'); };
  await assert.rejects(prepare('/vault', native, 'linux', 'status', neverSpawn, check), /virtual environment is incomplete/);
  const command = await prepare('/vault', { ...native, python: '/usr/bin/python3' }, 'linux', 'status', neverSpawn, check);
  assert.equal(command.options.env?.VIRTUAL_ENV, undefined);
  await assert.rejects(prepare('C:\\Vault', defaults('win32'), 'win32', 'status', wslSpawn('/mnt/c/Vault', '/mnt/c/Vault/.venv/pyvenv.cfg')), /WSL virtual environment is incomplete/);
});

test('stream parser handles split JSON progress, ordinary logs and pretty final objects', () => {
  const records: unknown[] = [], logs: string[] = [];
  const parser = new OutputParser(value => records.push(value), line => logs.push(line));
  const output = 'log line\r\n{"stage":"papers","processed":1,"remaining":2}\n' +
    JSON.stringify({ complete: true, warnings: ['braces } { and "quotes" café α'], nested: { value: [1, 2] } }, null, 2) + '\nend';
  for (let i = 0; i < output.length; i += 3) parser.feed(output.slice(i, i + 3));
  parser.finish();
  assert.equal(records.length, 2);
  assert.equal((records[0] as { processed: number }).processed, 1);
  assert.equal((records[1] as { complete: boolean }).complete, true);
  assert.deepEqual(logs, ['log line', 'end']);
});

test('stream parser bounds malformed, oversized and unfinished output', () => {
  const logs: string[] = [];
  const parser = new OutputParser(() => assert.fail('unexpected JSON'), line => logs.push(line), 32);
  parser.feed('x'.repeat(5000) + '\n' + '{"large":"' + 'z'.repeat(100) + '\n');
  parser.feed('{bad}\n{"unterminated":'); parser.finish();
  assert.ok(logs.some(line => line.includes('line truncated')));
  assert.ok(logs.some(line => line.includes('display limit')));
  assert.ok(logs.some(line => line.includes('Incomplete JSON')));
  assert.ok(logs.every(line => line.length < 4100));
});

test('progress uses real emitted fields and preserves counters through nested inference/cache stages', () => {
  const snapshot: Snapshot = { status: 'running', stage: 'snapshot', logs: [], diagnostics: [] };
  progress(snapshot, { stage: 'papers', processed: 2, remaining: 3, failed: 1, skipped: 1 });
  progress(snapshot, { stage: 'cache', namespace: 'extractions', reused: 8 });
  assert.equal(snapshot.processed, 2); assert.equal(snapshot.remaining, 3); assert.equal(snapshot.cacheHits, 8);
  statusDetails(snapshot, { pending: { link: 4, removal: 1, revision: 2, claim: 3 }, last_success: 'today' });
  assert.equal(snapshot.pending?.link, 4); assert.equal(snapshot.lastSuccess, 'today');
});

test('scientific review warnings do not become execution failures', () => {
  assert.equal(outcome(0, { complete: true, pending_claim_reviews: 2, warnings: ['quote needs review'] }), 'completed');
  assert.equal(outcome(2, { complete: false }), 'incomplete');
  assert.equal(outcome(0, { complete: false }), 'incomplete');
  for (const code of [1, 130, 9, null]) assert.equal(outcome(code, { complete: true }), 'failed');
  assert.equal(outcome(0, { blocking_errors: ['API refused'] }), 'failed');
});

test('display redaction masks common credentials in logs and JSON', () => {
  const text = redact('OPENAI_API_KEY=secret123 {"access_token":"abc def"} Authorization: Bearer abc.def password=hunter2 sk-abcdefgh https://user:pass@host/path');
  for (const secret of ['secret123', 'abc def', 'abc.def', 'hunter2', 'sk-abcdefgh', 'user:pass']) assert.ok(!text.includes(secret), text);
});

const fixture = await mkdtemp(path.join(os.tmpdir(), 'research-plugin-'));
await mkdir(path.join(fixture, '.venv/bin'), { recursive: true });
await mkdir(path.join(fixture, 'scripts'));
await writeFile(path.join(fixture, '.venv/bin/python'), 'synthetic executable, never run');
await writeFile(path.join(fixture, '.venv/pyvenv.cfg'), 'synthetic = true');
await chmod(path.join(fixture, '.venv/bin/python'), 0o755);
await writeFile(path.join(fixture, 'scripts/research.py'), '# synthetic');
await writeFile(path.join(fixture, 'config.yaml'), 'synthetic: true');
const fixtureSettings = { ...native, python: path.join(fixture, '.venv/bin/python') };

for (const [code, expected] of [[0, 'completed'], [1, 'failed'], [2, 'incomplete'], [null, 'failed']] as const) {
  test(`runner streams UTF-8, captures logs and honors exit ${code}`, async () => {
    const launch: Spawn = () => {
      const child = new Child();
      queueMicrotask(() => {
        child.stderr.write('{"stage":"papers","processed":1,"remaining":0}\n');
        const buffer = Buffer.from(JSON.stringify({ complete: code === 0, warnings: ['café α review required'] }, null, 2));
        for (const byte of buffer) child.stdout.write(Buffer.from([byte]));
        child.emit('close', code, code === null ? 'SIGTERM' : null);
      });
      return child as unknown as ReturnType<Spawn>;
    };
    let renders = 0;
    const runner = new Runner(() => { renders++; }, launch);
    const result = await runner.run(fixture, fixtureSettings, process.platform, 'sync');
    assert.equal(runner.snapshot.status, expected);
    assert.equal(runner.snapshot.processed, 1);
    assert.ok(JSON.stringify(result).includes('café α'));
    assert.ok(renders > 2); assert.equal(runner.busy, false);
  });
}

test('runner prevents duplicate commands including asynchronous preparation and separate instances', async () => {
  let child: Child | undefined;
  const runner = new Runner(() => {}, (() => { child = new Child(); return child; }) as unknown as Spawn);
  const pending = runner.run(fixture, fixtureSettings, process.platform, 'sync');
  await assert.rejects(runner.run(fixture, fixtureSettings, process.platform, 'apply'), /already running/);
  const second = new Runner(() => {}, neverSpawn);
  await assert.rejects(second.run(fixture, fixtureSettings, process.platform, 'doctor'), /already running/);
  while (!child) await new Promise(resolve => setImmediate(resolve));
  child.stdout.write('{}'); child.emit('close', 0, null);
  await pending;
  assert.equal(runner.busy, false);
});

test('launch errors release the command guard and logs remain bounded/redacted', async () => {
  const launch: Spawn = () => {
    const child = new Child();
    queueMicrotask(() => child.emit('error', new Error('ENOENT')));
    return child as unknown as ReturnType<Spawn>;
  };
  const runner = new Runner(() => {}, launch);
  await assert.rejects(runner.run(fixture, fixtureSettings, process.platform, 'sync'), /Cannot launch pipeline/);
  assert.equal(runner.busy, false); assert.equal(runner.snapshot.status, 'failed');
  for (let i = 0; i < 30; i++) runner.log('password=private', 20);
  assert.equal(runner.snapshot.logs.length, 20);
  assert.ok(!runner.snapshot.logs.join('').includes('private'));
});

test('WSL helper captures error exits, missing executable, and bounded output', async () => {
  const command = { executable: 'wsl.exe', args: [], options: { shell: false } };
  await assert.rejects(capture(command, (() => { throw new Error('ENOENT'); }) as Spawn), /ENOENT/);
  const launch = (error: boolean): Spawn => () => {
    const child = new Child();
    queueMicrotask(() => {
      if (error) child.emit('error', new Error('WSL unavailable'));
      else child.stdout.write('x'.repeat(100));
      child.emit('close', 1, null);
    });
    return child as unknown as ReturnType<Spawn>;
  };
  await assert.rejects(capture(command, launch(true)), /WSL unavailable/);
  await assert.rejects(capture(command, launch(false), 32), /too much output/);
});

test('all commands register; no process runs before workspace readiness', async () => {
  const plugin = new Plugin();
  const actions: string[] = [];
  plugin.execute = async action => { actions.push(action); return undefined; };
  await plugin.onload();
  assert.deepEqual(plugin.commands.map(command => command.name), COMMANDS.map(command => command[1]));
  assert.deepEqual(actions, []);
  for (const id of ['sync', 'dry', 'status', 'doctor']) plugin.commands.find(command => command.id === id)!.callback();
  assert.deepEqual(actions, ['sync', 'dry', 'status', 'doctor']);
  plugin.onunload();
});

test('startup disabled performs status only; enabled runs bounded incremental startup once ready', async () => {
  for (const enabled of [false, true]) {
    const plugin = new Plugin(); plugin.stored = { startupSync: enabled };
    const actions: string[] = [];
    plugin.execute = async action => { actions.push(action); return undefined; };
    await plugin.onload(); assert.deepEqual(actions, []);
    await plugin.ready();
    await plugin.ready();
    assert.deepEqual(actions, [enabled ? 'startup' : 'status']);
    assert.deepEqual(ACTION_ARGS.startup, ['sync', '--startup']);
    assert.ok(!ACTION_ARGS.startup.includes('--full' as never));
    plugin.onunload();
  }
});

test('startup is skipped after unload or when a command is active', async () => {
  const plugin = new Plugin(); plugin.stored = { startupSync: true };
  let runs = 0; plugin.execute = async () => { runs++; return undefined; };
  await plugin.onload(); plugin.runner.busy = true; await plugin.ready();
  assert.equal(runs, 0);
  plugin.runner.busy = false; plugin.onunload(); await plugin.ready(); assert.equal(runs, 0);
});

test('full sync requires confirmation and cancel launches nothing', async () => {
  const plugin = new Plugin(); await plugin.onload(); Modal.opened = [];
  const actions: string[] = [];
  plugin.execute = async action => { actions.push(action); return undefined; };
  plugin.request('full');
  assert.equal(Modal.opened.length, 1); assert.deepEqual(actions, []);
  const modal = Modal.opened[0];
  assert.ok(modal.contentEl.children.some(child => child.text.includes('substantial LLM')));
  modal.contentEl.children.find(child => child.text === 'Cancel')!.events.get('click')!();
  assert.deepEqual(actions, []);
  plugin.request('full');
  Modal.opened[1].contentEl.children.find(child => child.text === 'Continue')!.events.get('click')!();
  assert.deepEqual(actions, ['full']); plugin.onunload();
});

test('apply previews exact Python changes before confirmation and never approves or deletes in TypeScript', async () => {
  const plugin = new Plugin(); await plugin.onload(); Modal.opened = [];
  const actions: string[] = [];
  const selections: unknown[] = [];
  plugin.execute = async (action, _quiet, selection) => {
    actions.push(action); plugin.runner.snapshot.status = 'completed';
    selections.push(selection);
    return { can_apply: true, selected_ids: ['a'.repeat(24)], plan_token: 'b'.repeat(64), changes: ['Literature/SYNTHETIC.md'], destructive: true, impact_previews: [{ decision: 'delete', note: 'Literature/SYNTHETIC.md' }] };
  };
  await plugin.previewReviews(['a'.repeat(24)]);
  assert.deepEqual(actions, ['preview']);
  assert.deepEqual(ACTION_ARGS.preview, ['apply', '--dry-run']);
  assert.ok(Modal.opened[0].contentEl.children.some(child => child.text.includes('Literature/SYNTHETIC.md')));
  Modal.opened[0].contentEl.children.find(child => child.text === 'Continue')!.events.get('click')!();
  assert.deepEqual(actions, ['preview', 'apply']);
  assert.deepEqual(selections, [{ proposalIds: ['a'.repeat(24)] }, { proposalIds: ['a'.repeat(24)], planToken: 'b'.repeat(64) }]);
  assert.deepEqual(ACTION_ARGS.apply, ['apply']); plugin.onunload();
});

test('failed apply preview cannot launch actual apply', async () => {
  const plugin = new Plugin(); await plugin.onload(); Modal.opened = [];
  plugin.execute = async () => { plugin.runner.snapshot.status = 'failed'; return undefined; };
  await plugin.previewReviews(['a'.repeat(24)]); assert.equal(Modal.opened.length, 0); plugin.onunload();
});

test('oversized apply preview blocks application rather than hiding affected paths', async () => {
  const plugin = new Plugin(); await plugin.onload(); Modal.opened = [];
  plugin.execute = async () => {
    plugin.runner.snapshot.status = 'completed';
    return { can_apply: true, selected_ids: ['a'.repeat(24)], plan_token: 'b'.repeat(64), changes: ['x'.repeat(33000)] };
  };
  await plugin.previewReviews(['a'.repeat(24)]); assert.equal(Modal.opened.length, 0); plugin.onunload();
});

test('readiness allows only ready selections and previews them before applying', async () => {
  const plugin = new Plugin(); await plugin.onload(); Modal.opened = [];
  const actions: { action: string; selection: unknown }[] = [];
  const ready = 'a'.repeat(24), stale = 'b'.repeat(24);
  plugin.execute = async (action, _quiet, selection) => {
    actions.push({ action, selection }); plugin.runner.snapshot.status = 'completed';
    if (action === 'readiness') return { items: [
      { id: ready, path: 'Reviews/Links/ready.md', state: 'ready', reason: 'Validated.' },
      { id: stale, path: 'Reviews/Links/stale.md', state: 'needs-revalidation', reason: 'Changed definition.', refreshable: true, differences: [{ note: 'Concepts/test.md', diff: '-old\n+new' }] },
    ] };
    return { can_apply: true, selected_ids: [ready], plan_token: 'c'.repeat(64), changes: ['Concepts/test.md'] };
  };
  await plugin.previewReviews();
  assert.deepEqual(actions, [{ action: 'readiness', selection: undefined }]);
  const walk = (el: Element): Element[] => [el, ...el.children.flatMap(walk)];
  const elements = walk(Modal.opened[0].contentEl);
  assert.ok(elements.some(el => el.text.includes('needs-revalidation')));
  assert.ok(elements.some(el => el.text.includes('-old\n+new')));
  assert.equal(elements.filter(el => el.events.has('change')).length, 1);
  elements.find(el => el.text === 'Preview selected')!.events.get('click')!();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(actions[1], { action: 'preview', selection: { proposalIds: [ready] } });
  assert.equal(actions.length, 2);
  Modal.opened[1].contentEl.children.find(el => el.text === 'Continue')!.events.get('click')!();
  assert.deepEqual(actions[2], { action: 'apply', selection: { proposalIds: [ready], planToken: 'c'.repeat(64) } });
  plugin.onunload();
});

test('empty selections and changed selected previews cannot apply', async () => {
  const plugin = new Plugin(); await plugin.onload(); Modal.opened = [];
  const actions: string[] = [];
  plugin.execute = async action => {
    actions.push(action); plugin.runner.snapshot.status = 'completed';
    if (action === 'readiness') return { items: [{ id: 'a'.repeat(24), state: 'ready', path: 'Reviews/Links/a.md' }] };
    return { can_apply: false, selected_ids: [], plan_token: 'c'.repeat(64) };
  };
  await plugin.previewReviews();
  const walk = (el: Element): Element[] => [el, ...el.children.flatMap(walk)];
  const elements = walk(Modal.opened[0].contentEl);
  const toggle = elements.find(el => el.events.has('change'))!;
  toggle.checked = false; toggle.events.get('change')!();
  elements.find(el => el.text === 'Preview selected')!.events.get('click')!();
  assert.deepEqual(actions, ['readiness']);
  Modal.opened = [];
  await plugin.previewReviews(['a'.repeat(24)]);
  assert.equal(Modal.opened.length, 0);
  assert.deepEqual(actions, ['readiness', 'preview']);
  plugin.onunload();
});

test('review refresh opens its new pending file without synchronization or apply', async () => {
  const plugin = new Plugin(); await plugin.onload(); Modal.opened = [];
  const id = 'd'.repeat(24), target = 'Reviews/Links/new.md';
  plugin.app.files = [new TFile(target)];
  const actions: unknown[] = [];
  plugin.execute = async (action, _quiet, selection) => {
    actions.push({ action, selection }); return { path: target, status: 'pending' };
  };
  await plugin.refreshReview(id);
  assert.deepEqual(actions, [{ action: 'refresh', selection: { id } }]);
  assert.deepEqual(plugin.app.opened, [target]);
  plugin.onunload();
});

test('refresh of an existing relationship opens its source and keeps the closed review in history', async () => {
  const plugin = new Plugin(); await plugin.onload(); Modal.opened = []; Notice.messages = [];
  const source = 'Literature/existing.md', review = 'Reviews/Links/closed.md';
  plugin.app.files = [new TFile(source), new TFile(review)];
  plugin.app.metadataCache.getFileCache = file => ({ frontmatter: { status: 'pending', ...(file.path === review ? { resolved: 'already_represented' } : {}) } });
  const actions: string[] = [];
  plugin.execute = async action => {
    if (action === 'history') return { history: [{ id: 'a'.repeat(24), path: '.pipeline/review_history/resolved/closed.md', decision: 'pending', outcome: 'resolved' }] };
    actions.push(action); return { outcome: 'already_represented', path: source, message: 'Existing relationship; review moved to history.' };
  };
  await plugin.refreshReview('a'.repeat(24));
  assert.deepEqual(actions, ['refresh']);
  assert.deepEqual(plugin.app.opened, [source]);
  assert.ok(Notice.messages.some(message => message.includes('moved to history')));
  type Picker = { getSuggestions(query: string): TFile[]; getSuggestionText(file: TFile): string };
  plugin.openReviews();
  assert.deepEqual((Modal.opened[0] as unknown as Picker).getSuggestions(''), []);
  await plugin.openReviews(true);
  const history = Modal.opened[1] as unknown as Picker;
  assert.deepEqual(history.getSuggestions('').map(file => file.path), ['.pipeline/review_history/resolved/closed.md']);
  plugin.onunload();
});

test('refresh retains alternative definitions and opens readiness without starting inference', async () => {
  const plugin = new Plugin(); await plugin.onload(); Notice.messages = [];
  const actions: string[] = [];
  plugin.execute = async action => {
    actions.push(action); return { outcome: 'concept_comparison_needed', message: 'Compare the definition text.' };
  };
  let opened = 0;
  plugin.previewReviews = async () => { opened++; };
  await plugin.refreshReview('b'.repeat(24));
  assert.deepEqual(actions, ['refresh']);
  assert.equal(opened, 1);
  assert.ok(Notice.messages.includes('Compare the definition text.'));
  plugin.onunload();
});

test('selected IDs, preview tokens and concept paths are literal validated arguments', async () => {
  const id = 'e'.repeat(24), token = 'f'.repeat(64);
  const command = await prepare('/synthetic-vault', native, 'linux', 'apply', neverSpawn, noCheck, { proposalIds: [id], planToken: token });
  assert.deepEqual(command.args.slice(-4), ['--proposal', id, '--plan-token', token]);
  const paths = ['Concepts/A $variable; name.md', 'Concepts/B.md'];
  const comparison = await prepare('/synthetic-vault', native, 'linux', 'consolidate', neverSpawn, noCheck, { memberPaths: paths });
  assert.deepEqual(comparison.args.slice(-4), ['--member', paths[0], '--member', paths[1]]);
  assert.equal(comparison.options.shell, false);
  await assert.rejects(prepare('/synthetic-vault', native, 'linux', 'apply', neverSpawn, noCheck, { proposalIds: ['--full'] }), /valid approved/);
  await assert.rejects(prepare('/synthetic-vault', native, 'linux', 'refresh', neverSpawn, noCheck, { id: '../private' }), /valid review/);
  await assert.rejects(prepare('/synthetic-vault', native, 'linux', 'consolidate', neverSpawn, noCheck, { memberPaths: ['Concepts/../private.md', paths[1]] }), /distinct concept/);
});

test('unrelated review metadata does not make a small selected preview too large', async () => {
  const plugin = new Plugin(); await plugin.onload(); Modal.opened = [];
  const id = 'a'.repeat(24);
  plugin.execute = async () => {
    plugin.runner.snapshot.status = 'completed';
    return { can_apply: true, selected_ids: [id], plan_token: 'b'.repeat(64), changes: ['Concepts/selected.md'],
      diffs: { 'Concepts/selected.md': '+A selected definition.' },
      readiness: { items: [{ id, path: 'Reviews/Links/selected.md' }, { id: 'c'.repeat(24), path: 'Reviews/Links/other.md', reason: 'x'.repeat(40000) }] } };
  };
  await plugin.previewReviews([id]);
  assert.equal(Modal.opened.length, 1);
  const report = Modal.opened[0].contentEl.children.find(el => el.cls === 'research-vault-diagnostic')!.text;
  assert.ok(report.includes('+A selected definition.'));
  assert.ok(!report.includes('Reviews/Links/other.md'));
  assert.ok(!report.includes('plan_token'));
  plugin.onunload();
});

test('pipeline errors notify even when completion notifications are disabled', async () => {
  const plugin = new Plugin(); await plugin.onload(); Notice.messages = [];
  plugin.settings.notifications = false;
  plugin.runner.run = async () => { plugin.runner.snapshot.status = 'failed'; return { blocking_errors: ['Synthetic missing agent'] }; };
  await plugin.execute('doctor', true);
  assert.ok(Notice.messages.some(message => message.includes('failed')));
  plugin.onunload();
});

test('a failed status command is not masked by an older successful report', async () => {
  const plugin = new Plugin(); await plugin.onload();
  plugin.runner.run = async () => {
    plugin.runner.snapshot.status = 'failed';
    return { last_report: { complete: true } };
  };
  await plugin.execute('status'); assert.equal(plugin.runner.snapshot.status, 'failed'); plugin.onunload();
});

test('Python lock errors propagate without retrying expensive work', async () => {
  let launches = 0;
  const launch: Spawn = () => {
    launches++;
    const child = new Child();
    queueMicrotask(() => {
      child.stderr.write('research-vault: Another pipeline run holds the vault lock\n');
      child.emit('close', 1, null);
    });
    return child as unknown as ReturnType<Spawn>;
  };
  const runner = new Runner(() => {}, launch);
  await runner.run(fixture, fixtureSettings, process.platform, 'sync');
  assert.equal(launches, 1); assert.equal(runner.snapshot.status, 'failed');
  assert.ok(runner.snapshot.diagnostics.some(line => line.includes('vault lock')));
});

test('post-sync status refresh preserves incomplete status while updating Python review counts', async () => {
  const plugin = new Plugin(); await plugin.onload();
  const actions: string[] = [];
  plugin.runner.run = async (_vault, _settings, _platform, action) => {
    actions.push(action);
    plugin.runner.snapshot.status = action === 'sync' ? 'incomplete' : 'completed';
    if (action === 'status') return { pending: { link: 2 }, last_success: 'earlier', progress: { stage: 'incomplete', processed: 2 } };
    return { complete: false };
  };
  await plugin.execute('sync');
  assert.deepEqual(actions, ['sync', 'status']); assert.equal(plugin.runner.snapshot.status, 'incomplete');
  assert.equal(plugin.runner.snapshot.pending?.link, 2); assert.equal(plugin.runner.snapshot.lastSuccess, 'earlier');
  plugin.onunload();
});

test('dashboard and review navigation open existing files without modifying notes', async () => {
  const plugin = new Plugin(); await plugin.onload();
  plugin.app.files = [new TFile('Home.md'), new TFile('Reviews/Links/synthetic.md'), new TFile('My Notes/private.md')];
  await plugin.openDashboard(); assert.deepEqual(plugin.app.opened, ['Home.md']);
  Modal.opened = []; plugin.openReviews();
  const picker = Modal.opened[0] as unknown as { getSuggestions(query: string): TFile[]; onChooseSuggestion(file: TFile): void };
  assert.deepEqual(picker.getSuggestions('').map(file => file.path), ['Reviews/Links/synthetic.md']);
  picker.onChooseSuggestion(plugin.app.files[1]);
  assert.equal(plugin.app.opened[1], 'Reviews/Links/synthetic.md'); plugin.onunload();
});

test('active queue retains unapplied approvals and history is read through Python', async () => {
  const plugin = new Plugin(); await plugin.onload(); Modal.opened = [];
  const statuses: Record<string, string> = {
    'Reviews/Links/pending.md': 'pending', 'Reviews/Links/blocked.md': 'approved',
    'Reviews/Revisions/rejected.md': 'rejected', 'My Notes/personal.md': 'pending',
  };
  plugin.app.files = Object.keys(statuses).map(name => new TFile(name));
  plugin.app.metadataCache.getFileCache = file => ({ frontmatter: { status: statuses[file.path] } });
  await plugin.openReviews();
  type Picker = { getSuggestions(query: string): { path: string; id: string }[]; onChooseSuggestion(record: unknown): void };
  const queue = Modal.opened[0] as unknown as Picker;
  assert.deepEqual(queue.getSuggestions('').map(file => file.path), ['Reviews/Links/blocked.md', 'Reviews/Links/pending.md']);
  const id = 'a'.repeat(24), archive = '.pipeline/review_history/rejected/' + id + '.md';
  const calls: unknown[] = [];
  plugin.execute = async (action, _quiet, selection) => {
    calls.push({ action, selection });
    return selection?.id ? { markdown: 'Original rejected scientific proposal', record: { id, outcome: 'rejected' } } : { history: [{ id, path: archive, decision: 'rejected', outcome: 'rejected' }] };
  };
  await plugin.openReviews(true);
  const history = Modal.opened[1] as unknown as Picker;
  assert.deepEqual(history.getSuggestions('').map(record => record.path), [archive]);
  await plugin.openArchivedReview(id);
  assert.deepEqual(calls, [{ action: 'history', selection: undefined }, { action: 'history', selection: { id } }]);
  assert.ok(Modal.opened[2].contentEl.children.some(child => child.text.includes('Original rejected scientific proposal')));
  assert.deepEqual(plugin.app.opened, []);
  plugin.onunload();
});

test('sidebar renders status, counters, review counts, diagnostics and disabled controls', async () => {
  const plugin = new Plugin(); await plugin.onload();
  plugin.runner.busy = true;
  plugin.runner.snapshot = {
    status: 'incomplete', stage: 'reasoning', processed: 3, remaining: 2, failed: 1, skipped: 1,
    cacheHits: 8, pending: { link: 2, removal: 1, revision: 4 }, lastSuccess: 'earlier',
    logs: ['Synthetic warning'], diagnostics: ['Source incomplete'],
  };
  const view = new ResearchView(new WorkspaceLeaf(), plugin); view.render();
  const walk = (el: Element): Element[] => [el, ...el.children.flatMap(walk)];
  const elements = walk(view.contentEl);
  assert.ok(elements.some(el => el.text === 'Status: incomplete'));
  assert.ok(elements.some(el => el.text === 'Pending removals'));
  assert.ok(elements.some(el => el.text === 'Source incomplete'));
  assert.ok(elements.find(el => el.text === 'Sync')?.disabled);
  plugin.onunload();
});

test('bundled distribution contains no settings and matches build inputs', async () => {
  const source = JSON.parse(await readFile('manifest.json', 'utf8'));
  const bundled = JSON.parse(await readFile('../.obsidian/plugins/research-vault/manifest.json', 'utf8'));
  assert.deepEqual(source, bundled); assert.equal(bundled.isDesktopOnly, true);
  const generated = await readFile('../.obsidian/plugins/research-vault/main.js', 'utf8');
  assert.ok(generated.includes('Generated from obsidian-plugin/src'));
  assert.ok(!generated.includes('shell: true'));
  assert.ok(generated.includes('".pipeline", "cancel"')); // Only per-run cancellation markers are removed.
  assert.ok(!generated.includes('.vault.delete('));
});

test('interrupt uses a per-run marker, preserves the child for cleanup, and ends failed', async () => {
  let child: Child | undefined;
  let launchedArgs: string[] = [];
  const launch: Spawn = (_command, args) => {
    launchedArgs = args; child = new Child();
    return child as unknown as ReturnType<Spawn>;
  };
  const runner = new Runner(() => {}, launch);
  const pending = runner.run(fixture, fixtureSettings, process.platform, 'sync');
  while (!child) await new Promise(resolve => setImmediate(resolve));
  const index = launchedArgs.indexOf('--control-id');
  assert.ok(index > 0);
  const id = launchedArgs[index + 1];
  assert.match(id, /^[a-f0-9]{32}$/);
  assert.equal(runner.canInterrupt, true);
  await runner.interrupt();
  assert.equal(await readFile(path.join(fixture, '.pipeline/cancel', id), 'utf8'), 'interrupt\n');
  assert.equal(child.killed, false);
  assert.equal(runner.canInterrupt, false);
  child.stdout.write(JSON.stringify({ status: 'failed', complete: false, error: 'Synchronization interrupted' }));
  child.emit('close', 130, null);
  await pending;
  assert.equal(runner.snapshot.status, 'failed');
  assert.equal(runner.busy, false);
  await assert.rejects(readFile(path.join(fixture, '.pipeline/cancel', id)), /ENOENT/);
});

test('dry-run sidebar control and interrupt command are available; only sync can be interrupted', async () => {
  const plugin = new Plugin(); await plugin.onload();
  const view = new ResearchView(new WorkspaceLeaf(), plugin); view.render();
  const controls = (view.contentEl as unknown as Element).children.find(child => child.cls === 'research-vault-controls')!;
  assert.ok(controls.children.some(child => child.text === 'Dry Run Sync'));
  assert.ok(controls.children.find(child => child.text === 'Interrupt Sync')?.disabled);
  assert.ok(plugin.commands.some(command => command.id === 'interrupt'));
  let child: Child | undefined;
  const runner = new Runner(() => {}, (() => { child = new Child(); return child; }) as unknown as Spawn);
  const pending = runner.run(fixture, fixtureSettings, process.platform, 'apply');
  while (!child) await new Promise(resolve => setImmediate(resolve));
  assert.equal(runner.canInterrupt, false);
  await runner.interrupt();
  child.stdout.write('{}'); child.emit('close', 0, null); await pending;
  plugin.onunload();
});

test('technical completion, source limitations and interruption have distinct presentation', () => {
  assert.equal(outcome(2, { status: 'completed_with_limitations', technical_complete: true, complete: false }), 'completed');
  assert.equal(outcome(0, { status: 'failed', complete: true }), 'failed');
  const snapshot: Snapshot = { status: 'completed', stage: '', logs: [], diagnostics: [] };
  statusDetails(snapshot, { status: 'completed_reviews_pending', pending: { link: 1 } });
  assert.equal(snapshot.syncStatus, 'completed_reviews_pending');
});
