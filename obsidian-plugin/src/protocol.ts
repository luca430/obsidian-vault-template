export type RecordValue = Record<string, unknown>;
export const isRecord = (v: unknown): v is RecordValue =>
  !!v && typeof v === 'object' && !Array.isArray(v);

export function redact(text: string): string {
  return text
    .replace(/\b(Bearer\s+)[^\s"',}]+/gi, '$1[REDACTED]')
    .replace(/\bsk-[a-zA-Z0-9_-]+/g, '[REDACTED]')
    .replace(/\b([\w-]*(?:api[_-]?key|token|password|secret|authorization)["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,}]+)/gi, '$1[REDACTED]')
    .replace(/(https?:\/\/)[^/\s:@]+:[^/\s@]+@/gi, '$1[REDACTED]@')
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '');
}

// Newline progress records on stderr; pretty final JSON on stdout. Bound both
// unfinished lines and JSON frames, including malicious/unexpected subprocess output.
export class OutputParser {
  private line = '';
  private frame = '';
  private depth = 0;
  private quoted = false;
  private escaped = false;
  private dropping = false;
  constructor(
    private object: (value: RecordValue) => void,
    private log: (line: string) => void,
    private limit = 8 * 1024 * 1024,
  ) {}

  feed(text: string): void {
    for (const char of text) {
      if (this.dropping) {
        if (char === '\n') this.dropping = false;
        continue;
      }
      if (this.frame) {
        this.frame += char;
        if (this.frame.length > this.limit) {
          this.log('Output JSON exceeded the display limit; consult .pipeline/logs locally.');
          this.frame = ''; this.depth = 0; this.dropping = true;
          continue;
        }
        if (this.quoted) {
          if (this.escaped) this.escaped = false;
          else if (char === '\\') this.escaped = true;
          else if (char === '"') this.quoted = false;
        } else if (char === '"') this.quoted = true;
        else if (char === '{' || char === '[') this.depth++;
        else if (char === '}' || char === ']') this.depth--;
        if (this.depth === 0) {
          try {
            const parsed: unknown = JSON.parse(this.frame);
            if (isRecord(parsed)) this.object(parsed);
          } catch { this.log(this.frame.slice(0, 4000)); }
          this.frame = '';
        }
      } else if (char === '{' && !this.line.trim()) {
        this.line = ''; this.frame = '{'; this.depth = 1;
        this.quoted = false; this.escaped = false;
      } else if (char === '\n') {
        if (this.line.trim()) this.log(this.line.trimEnd());
        this.line = '';
      } else {
        this.line += char;
        if (this.line.length >= 4000) {
          this.log(this.line + ' … [line truncated]');
          this.line = ''; this.dropping = true;
        }
      }
    }
  }

  finish(): void {
    if (this.line.trim()) this.log(this.line.trimEnd());
    if (this.frame) this.log('Incomplete JSON output: ' + this.frame.slice(0, 4000));
    this.line = ''; this.frame = '';
  }
}

export type PipelineStatus = 'idle' | 'running' | 'completed' | 'incomplete' | 'failed';
export function outcome(code: number | null, result?: RecordValue): PipelineStatus {
  if (code === 2) return 'incomplete';
  if (code !== 0) return 'failed';
  if (Array.isArray(result?.blocking_errors) && result.blocking_errors.length) return 'failed';
  if (result?.complete === false) return 'incomplete';
  return 'completed';
}

export interface Snapshot {
  status: PipelineStatus;
  stage: string;
  processed?: number;
  remaining?: number;
  skipped?: number;
  failed?: number;
  cacheHits?: number;
  pending?: RecordValue;
  lastSuccess?: string;
  logs: string[];
  diagnostics: string[];
}

export function progress(snapshot: Snapshot, value: RecordValue): void {
  if (typeof value.stage !== 'string') return;
  snapshot.stage = value.stage;
  for (const key of ['processed', 'remaining', 'skipped', 'failed'] as const) {
    if (typeof value[key] === 'number') snapshot[key] = value[key];
  }
  if (typeof value.cache_hits === 'number') snapshot.cacheHits = value.cache_hits;
  if (value.stage === 'cache' && typeof value.reused === 'number') snapshot.cacheHits = value.reused;
}

export function statusDetails(snapshot: Snapshot, value: RecordValue): void {
  if (isRecord(value.pending)) snapshot.pending = value.pending;
  if ('last_success' in value) snapshot.lastSuccess = typeof value.last_success === 'string' ? value.last_success : undefined;
  if (isRecord(value.progress)) progress(snapshot, value.progress);
  if (isRecord(value.last_report) && typeof value.last_report.cache_hits === 'number') {
    snapshot.cacheHits = value.last_report.cache_hits;
  }
}
