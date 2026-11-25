import { build } from 'esbuild';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
await build({
  absWorkingDir: root,
  entryPoints: ['tests/plugin.test.ts'],
  bundle: true,
  platform: 'node',
  format: 'esm',
  target: 'node18',
  alias: { obsidian: path.join(root, 'tests/obsidian.ts') },
  outfile: path.join(root, '.test-build/plugin.test.mjs'),
});
// node:test also runs directly, emitting TAP and a failing exit code. This avoids
// nested --test process isolation hiding test cases in some host environments.
const child = spawn(process.execPath, [path.join(root, '.test-build/plugin.test.mjs')], { stdio: 'inherit', shell: false });
child.on('error', error => { console.error(error); process.exitCode = 1; });
child.on('exit', code => { process.exitCode = code ?? 1; });
