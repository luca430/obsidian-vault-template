// Structure follows obsidianmd/obsidian-sample-plugin; Obsidian and Node stay external.
import { build } from 'esbuild';
import { builtinModules } from 'node:module';
import { mkdir, copyFile, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.dirname(fileURLToPath(import.meta.url));
const dist = path.join(root, 'dist');
const bundled = path.join(root, '../.obsidian/plugins/research-vault');
await mkdir(dist, { recursive: true });
await build({
  absWorkingDir: root,
  entryPoints: ['src/main.ts'],
  bundle: true,
  external: ['obsidian', 'electron', ...builtinModules, ...builtinModules.map(m => `node:${m}`)],
  format: 'cjs',
  target: 'es2021',
  outfile: path.join(dist, 'main.js'),
  banner: { js: '/* Generated from obsidian-plugin/src. Rebuild with npm run build. */' },
  sourcemap: false,
  logLevel: 'info',
});
await mkdir(bundled, { recursive: true });
for (const file of ['manifest.json', 'styles.css']) {
  await copyFile(path.join(root, file), path.join(dist, file));
}
for (const file of ['main.js', 'manifest.json', 'styles.css']) {
  // Write only these distribution files. Never replace a user's data.json.
  await writeFile(path.join(bundled, file), await readFile(path.join(dist, file)));
}
