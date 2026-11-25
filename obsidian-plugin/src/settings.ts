export interface ResearchSettings {
  mode: 'native' | 'wsl';
  distribution: string;
  python: string;
  script: string;
  startupSync: boolean;
  openSidebar: boolean;
  notifications: boolean;
  ribbon: boolean;
  maxLogLines: number;
}

export function defaults(platform: string): ResearchSettings {
  return {
    mode: platform === 'win32' ? 'wsl' : 'native',
    distribution: '', python: '', script: '', startupSync: false,
    openSidebar: false, notifications: true, ribbon: true, maxLogLines: 200,
  };
}

export function loadSettings(data: unknown, platform: string): ResearchSettings {
  const result = defaults(platform);
  if (!data || typeof data !== 'object') return result;
  const value = data as Record<string, unknown>;
  if (value.mode === 'wsl' || value.mode === 'native') result.mode = value.mode;
  for (const key of ['distribution', 'python', 'script'] as const) {
    if (typeof value[key] === 'string') result[key] = value[key];
  }
  for (const key of ['startupSync', 'openSidebar', 'notifications', 'ribbon'] as const) {
    if (typeof value[key] === 'boolean') result[key] = value[key];
  }
  if (typeof value.maxLogLines === 'number' && Number.isFinite(value.maxLogLines)) {
    result.maxLogLines = Math.max(20, Math.min(2000, Math.floor(value.maxLogLines)));
  }
  return result;
}

export function defaultPython(settings: ResearchSettings, platform: string): string {
  return settings.mode === 'native' && platform === 'win32'
    ? '.venv/Scripts/python.exe' : '.venv/bin/python';
}
