import { spawn, SpawnOptions, ChildProcess } from 'node:child_process';
import { promises as fs, constants } from 'node:fs';
import path from 'node:path';
import { StringDecoder } from 'node:string_decoder';
import { randomBytes } from 'node:crypto';
import { ResearchSettings, defaultPython } from './settings';
import { OutputParser, RecordValue, Snapshot, isRecord, outcome, progress, redact } from './protocol';

export const ACTION_ARGS = {
  sync: ['sync'], full: ['sync', '--full'], dry: ['sync', '--dry-run'],
  startup: ['sync', '--startup'], apply: ['apply'], preview: ['apply', '--dry-run'],
  status: ['status'], doctor: ['doctor'],
  readiness: ['review', 'status'], refresh: ['review', 'refresh'], consolidate: ['concepts', 'suggest'],
  history: ['review', 'history'],
  agentSettings: ['settings', 'show'], saveAgentSettings: ['settings', 'save'],
} as const;
export type Action = keyof typeof ACTION_ARGS;
export interface ReviewSelection { proposalIds?: string[]; planToken?: string; id?: string; memberPaths?: string[]; controlId?: string; settingsRequestId?: string }
export interface Command { executable: string; args: string[]; options: SpawnOptions }
export type Spawn = (command: string, args: string[], options: SpawnOptions) => ChildProcess;
export const nodeSpawn: Spawn = (command, args, options) => spawn(command, args, options);

function validText(value: string, label: string): void {
  if (/[\u0000-\u001f]/.test(value)) throw new Error(`${label} contains control characters.`);
}

function resolvePath(root: string, value: string, windows: boolean): string {
  validText(value, 'Path');
  if (!value || value.startsWith('~')) throw new Error('Use a project-relative or absolute path; ~ is not expanded.');
  const api = windows ? path.win32 : path.posix;
  if (!windows && /^[a-z]:|\\/i.test(value)) throw new Error('Use a Linux path in this execution environment.');
  // A setting is a path, never an executable plus flags or a PATH lookup.
  return api.resolve(root, value);
}

export async function capture(command: Command, launch: Spawn, limit = 128 * 1024): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = launch(command.executable, command.args, command.options);
    let stdout = '', stderr = '', settled = false;
    const out = new StringDecoder('utf8'), err = new StringDecoder('utf8');
    const timer = setTimeout(() => { child.kill(); done(new Error('WSL validation timed out. Check the distribution and wsl.exe.')); }, 15000);
    const done = (error?: Error) => {
      if (settled) return;
      settled = true; clearTimeout(timer);
      if (error) reject(error); else resolve(stdout + out.end());
    };
    const append = (data: Buffer, isError: boolean) => {
      if (settled) return;
      if (isError) stderr += err.write(data); else stdout += out.write(data);
      if (stdout.length + stderr.length > limit) {
        child.kill(); done(new Error('WSL validation returned too much output.'));
      }
    };
    child.stdout?.on('data', (data: Buffer) => append(data, false));
    child.stderr?.on('data', (data: Buffer) => append(data, true));
    child.once('error', (error) => done(new Error(`Cannot launch ${command.executable}: ${error.message}`)));
    child.once('close', (code) => done(code === 0 ? undefined : new Error(`WSL validation failed (${code}): ${redact(stderr + err.end()).slice(0, 4000)}`)));
  });
}

export async function prepare(
  vault: string, settings: ResearchSettings, platform: string, action: Action,
  launch: Spawn = nodeSpawn,
  checkFile: (file: string, executable: boolean) => Promise<void> = async (file, executable) => {
    if (!(await fs.stat(file)).isFile()) throw new Error('not a regular file');
    await fs.access(file, executable && platform !== 'win32' ? constants.X_OK : constants.R_OK);
  },
  selection?: ReviewSelection,
): Promise<Command> {
  validText(vault, 'Vault'); validText(settings.distribution, 'Distribution');
  if (settings.mode !== 'native' && settings.mode !== 'wsl') throw new Error('Invalid execution mode.');
  if (!Object.prototype.hasOwnProperty.call(ACTION_ARGS, action)) throw new Error('Unsupported research operation.');
  const windows = platform === 'win32';
  if (!(windows ? path.win32 : path.posix).isAbsolute(vault)) throw new Error('Vault must have an absolute filesystem path.');
  const options: SpawnOptions = { cwd: vault, shell: false, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] };
  let root = vault;
  const wsl = (args: string[]): Command => ({
    executable: 'wsl.exe',
    args: [...(settings.distribution ? ['--distribution', settings.distribution] : []), ...args], options,
  });
  if (settings.mode === 'wsl') {
    if (!windows) throw new Error('WSL mode requires Windows Obsidian. Select Native for Linux Obsidian.');
    if (settings.distribution.startsWith('-')) throw new Error('WSL distribution must be a name, not an option.');
    const translated = await capture(wsl(['--cd', '~', '--exec', 'wslpath', '-a', '-u', vault]), launch);
    root = translated.replace(/[\r\n]+$/, '');
    if (!root.startsWith('/') || /[\u0000-\u001f]/.test(root)) throw new Error('wslpath returned an invalid Linux path.');
  }
  const python = resolvePath(root, settings.python || defaultPython(settings, platform), windows && settings.mode === 'native');
  const script = resolvePath(root, settings.script || 'scripts/research.py', windows && settings.mode === 'native');
  if (!script.toLowerCase().endsWith('.py')) throw new Error('CLI script must be a Python .py file.');
  if (settings.mode === 'wsl' && /\.(exe|cmd|bat)$/i.test(python)) throw new Error('WSL requires a Linux Python virtual environment.');
  const api = windows && settings.mode === 'native' ? path.win32 : path.posix;
  const bin = api.dirname(python);
  const venvLayout = /^(bin|scripts)$/i.test(api.basename(bin));
  const venvConfig = api.join(api.dirname(bin), 'pyvenv.cfg');
  let isVenv = false;
  const args = ['-u', script, '--vault', root, ...ACTION_ARGS[action]];
  if (action === 'saveAgentSettings') {
    if (!selection?.settingsRequestId || !/^[a-f0-9]{32}$/.test(selection.settingsRequestId)) throw new Error('Invalid agent settings request.');
    args.push('--request-id', selection.settingsRequestId);
  }
  if (selection?.controlId) {
    if (!['sync', 'full', 'startup', 'dry'].includes(action) || !/^[a-f0-9]{32}$/.test(selection.controlId)) throw new Error('Invalid sync control identifier.');
    args.push('--control-id', selection.controlId);
  }
  if (action === 'consolidate' && selection?.memberPaths) {
    if (selection.id || selection.memberPaths.length < 2 || selection.memberPaths.some(p => !/^Concepts\/[^\\\u0000-\u001f]+\.md$/.test(p) || p.split('/').includes('..'))) throw new Error('Select distinct concept paths.');
    for (const member of selection.memberPaths) args.push('--member', member);
  } else if (action === 'refresh' || action === 'consolidate' || (action === 'history' && selection?.id)) {
    if (!selection?.id || !/^[a-f0-9]{24}$/.test(selection.id)) throw new Error('Select a valid review or candidate ID.');
    args.push(selection.id);
  }
  if (selection?.proposalIds) {
    if (!['apply', 'preview'].includes(action) || !selection.proposalIds.length || selection.proposalIds.some(id => !/^[a-f0-9]{24}$/.test(id))) throw new Error('Select valid approved proposal IDs.');
    for (const id of selection.proposalIds) args.push('--proposal', id);
  }
  if (selection?.planToken) {
    if (action !== 'apply' || !/^[a-f0-9]{64}$/.test(selection.planToken)) throw new Error('Invalid review preview token.');
    args.push('--plan-token', selection.planToken);
  }
  const required = [[python, true], [script, false], [api.join(root, 'config.yaml'), false]] as const;
  if (settings.mode === 'native') {
    for (const [file, executable] of required) {
      try { await checkFile(file, executable); }
      catch { throw new Error(`Missing or inaccessible ${executable ? 'Python executable' : 'project file'}: ${file}. Configure the project virtual environment and install Python dependencies.`); }
    }
    if (venvLayout) {
      try { await checkFile(venvConfig, false); isVenv = true; }
      catch {
        if (!settings.python) throw new Error(`Project virtual environment is incomplete: missing ${venvConfig}. Recreate it with this environment's Python.`);
      }
    }
    return { executable: python, args, options: {
      ...options, env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8',
        PATH: bin + api.delimiter + (process.env.PATH || ''),
        VIRTUAL_ENV: isVenv ? api.dirname(bin) : undefined,
      },
    } };
  }
  for (const [file, executable] of required) {
    try {
      await capture(wsl(['--cd', root, '--exec', '/usr/bin/test', '-f', file]), launch);
      if (executable) await capture(wsl(['--cd', root, '--exec', '/usr/bin/test', '-x', file]), launch);
    } catch (error) { throw new Error(`Missing or inaccessible WSL ${executable ? 'Python executable' : 'project file'}: ${file}. ${String(error)}`); }
  }
  if (venvLayout) {
    try {
      await capture(wsl(['--cd', root, '--exec', '/usr/bin/test', '-f', venvConfig]), launch);
      isVenv = true;
    } catch {
      if (!settings.python) throw new Error(`WSL virtual environment is incomplete: missing ${venvConfig}. Create a Linux venv inside this distribution.`);
    }
  }
  // Probe WSL's environment, never copy Windows PATH into Linux or log credentials.
  const environment = await capture(wsl(['--cd', root, '--exec', '/usr/bin/env', '-0']), launch);
  const linuxPath = environment.split('\0').find(line => line.startsWith('PATH='))?.slice(5);
  if (!linuxPath) throw new Error('WSL did not supply PATH; repair the distribution environment.');
  return wsl(['--cd', root, '--exec', '/usr/bin/env', '-u', 'VIRTUAL_ENV',
    `PATH=${bin}:${linuxPath}`, 'PYTHONUNBUFFERED=1', 'PYTHONIOENCODING=utf-8',
    ...(isVenv ? [`VIRTUAL_ENV=${path.posix.dirname(bin)}`] : []), python, ...args]);
}

// Python remains the cross-process lock authority, including other Obsidian windows
// and terminal runs. This guard additionally covers async preflight within this app.
const activeVaults = new Set<string>();
export class Runner {
  snapshot: Snapshot = { status: 'idle', stage: 'Ready', logs: [], diagnostics: [] };
  busy = false;
  disposed = false;
  private control?: { vault: string; id: string; interrupted: boolean };
  private interrupting?: Promise<void>;
  get canInterrupt(): boolean { return this.busy && !!this.control && !this.control.interrupted; }
  async interrupt(): Promise<void> {
    const control = this.control;
    if (!this.canInterrupt || !control) return;
    control.interrupted = true;
    try {
      this.interrupting = (async () => {
        for (const directory of [path.join(control.vault, '.pipeline'), path.join(control.vault, '.pipeline', 'cancel')]) {
          await fs.mkdir(directory).catch((error: NodeJS.ErrnoException) => { if (error.code !== 'EEXIST') throw error; });
          const info = await fs.lstat(directory);
          if (info.isSymbolicLink() || !info.isDirectory()) throw new Error('Cancellation directory must be a local directory, without symlinks.');
        }
        await fs.writeFile(path.join(control.vault, '.pipeline', 'cancel', control.id), 'interrupt\n', { flag: 'wx' });
        this.snapshot.stage = 'Stopping synchronization at a safe boundary';
        this.changed();
      })();
      await this.interrupting;
    } catch (error) { control.interrupted = false; throw error; }
  }
  constructor(private changed: () => void, private launch: Spawn = nodeSpawn) {}

  log(line: string, max: number): void {
    this.snapshot.logs.push(redact(line).slice(0, 4000));
    if (this.snapshot.logs.length > max) this.snapshot.logs.splice(0, this.snapshot.logs.length - max);
  }

  async run(vault: string, settings: ResearchSettings, platform: string, action: Action, selection?: ReviewSelection): Promise<RecordValue | undefined> {
    settings = { ...settings };
    const key = platform === 'win32' ? path.win32.normalize(vault).toLowerCase() : path.posix.normalize(vault);
    if (this.disposed) throw new Error('Research Vault is unloaded.');
    if (this.busy || activeVaults.has(key)) throw new Error('A Research Vault command is already running in this vault.');
    activeVaults.add(key); this.busy = true;
    if (['sync', 'full', 'startup', 'dry'].includes(action)) {
      this.control = { vault, id: randomBytes(16).toString('hex'), interrupted: false };
      selection = { ...selection, controlId: this.control.id };
    }
    this.snapshot = {
      ...this.snapshot, status: 'running', stage: `Starting ${action}`,
      processed: undefined, remaining: undefined, skipped: undefined, failed: undefined,
      cacheHits: undefined, diagnostics: [],
      syncStatus: undefined,
    };
    this.log(`Starting ${action}`, settings.maxLogLines); this.changed();
    let result: RecordValue | undefined;
    try {
      const command = await prepare(vault, settings, platform, action, this.launch, undefined, selection);
      if (this.disposed) throw new Error('Plugin unloaded before the command started.');
      if (this.control?.interrupted) throw new Error('Synchronization interrupted before launch.');
      const code = await new Promise<number | null>((resolve, reject) => {
        const child = this.launch(command.executable, command.args, command.options);
        const consume = (value: RecordValue, stdout: boolean) => {
          if (typeof value.stage === 'string') progress(this.snapshot, value);
          else if (stdout) result = value;
          else this.log(JSON.stringify(value), settings.maxLogLines);
          this.changed();
        };
        const log = (line: string) => { this.log(line, settings.maxLogLines); this.changed(); };
        const out = new OutputParser(v => consume(v, true), log);
        const err = new OutputParser(v => consume(v, false), line => {
          log(line);
          this.snapshot.diagnostics.push(redact(line).slice(0, 4000));
          if (this.snapshot.diagnostics.length > 20) this.snapshot.diagnostics.shift();
        });
        const outDecoder = new StringDecoder('utf8'), errDecoder = new StringDecoder('utf8');
        child.stdout?.on('data', (data: Buffer) => out.feed(outDecoder.write(data)));
        child.stderr?.on('data', (data: Buffer) => err.feed(errDecoder.write(data)));
        child.once('error', error => reject(new Error(`Cannot launch pipeline: ${error.message}. Check Python or WSL settings.`)));
        child.once('close', (exitCode, signal) => {
          out.feed(outDecoder.end()); err.feed(errDecoder.end()); out.finish(); err.finish();
          log(`Process exited: ${exitCode ?? `signal ${signal ?? 'unknown'}`}`);
          resolve(exitCode);
        });
      });
      this.snapshot.status = this.control?.interrupted ? 'failed' : outcome(code, result);
      if (typeof result?.status === 'string') this.snapshot.syncStatus = result.status;
      if (result) {
        if (isRecord(result.usage_by_agent)) this.snapshot.agentUsage = result.usage_by_agent;
        if (isRecord(result.task_status)) this.snapshot.taskCounts = result.task_status;
        this.snapshot.resumeInstructions = typeof result.resume_instructions === 'string' ? result.resume_instructions : undefined;
      }
      this.snapshot.stage = `${action}: ${this.snapshot.status}`;
      if (!result) this.log('No final JSON report was available; inspect logs and run Research: Status.', settings.maxLogLines);
      if (result) {
        for (const field of ['warnings', 'blocking_errors', 'document_errors', 'graph_error', 'reasoning_error', 'provider_interruptions', 'resume_instructions', 'usage_by_agent', 'task_status', 'failed', 'skipped', 'partial'] as const) {
          const detail = result[field];
          if (detail && (!Array.isArray(detail) || detail.length) && (!isRecord(detail) || Object.keys(detail).length)) {
            this.snapshot.diagnostics.push(redact(`${field}: ${JSON.stringify(detail)}`).slice(0, 16000));
          }
        }
        const report = redact(JSON.stringify(result, null, 2));
        for (const line of report.slice(0, 16000).split('\n')) this.log(line, settings.maxLogLines);
        if (report.length > 16000) this.log('Final report display truncated; use the CLI for the full report.', settings.maxLogLines);
      }
      return result;
    } catch (error) {
      this.snapshot.status = 'failed'; this.snapshot.stage = 'Launch or execution failed';
      this.log(String(error), settings.maxLogLines);
      this.snapshot.diagnostics.push(redact(String(error)).slice(0, 4000));
      throw error;
    } finally {
      await this.interrupting?.catch(() => undefined);
      if (this.control) await fs.unlink(path.join(this.control.vault, '.pipeline', 'cancel', this.control.id)).catch(() => undefined);
      this.interrupting = undefined;
      this.control = undefined;
      this.busy = false; activeVaults.delete(key); this.changed();
    }
  }
}
