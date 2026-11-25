/* Generated from obsidian-plugin/src. Rebuild with npm run build. */
"use strict";
var __create = Object.create;
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __getProtoOf = Object.getPrototypeOf;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toESM = (mod, isNodeMode, target) => (target = mod != null ? __create(__getProtoOf(mod)) : {}, __copyProps(
  // If the importer is in node compatibility mode or this is not an ESM
  // file that has been converted to a CommonJS file using a Babel-
  // compatible transform (i.e. "__esModule" has not been set), then set
  // "default" to the CommonJS "module.exports" for node compatibility.
  isNodeMode || !mod || !mod.__esModule ? __defProp(target, "default", { value: mod, enumerable: true }) : target,
  mod
));
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// src/main.ts
var main_exports = {};
__export(main_exports, {
  COMMANDS: () => COMMANDS,
  ResearchView: () => ResearchView,
  VIEW_TYPE: () => VIEW_TYPE,
  default: () => ResearchVaultPlugin
});
module.exports = __toCommonJS(main_exports);
var import_obsidian = require("obsidian");

// src/execution.ts
var import_node_child_process = require("node:child_process");
var import_node_fs = require("node:fs");
var import_node_path = __toESM(require("node:path"));
var import_node_string_decoder = require("node:string_decoder");

// src/settings.ts
function defaults(platform) {
  return {
    mode: platform === "win32" ? "wsl" : "native",
    distribution: "",
    python: "",
    script: "",
    startupSync: false,
    openSidebar: false,
    notifications: true,
    ribbon: true,
    maxLogLines: 200
  };
}
function loadSettings(data, platform) {
  const result = defaults(platform);
  if (!data || typeof data !== "object") return result;
  const value = data;
  if (value.mode === "wsl" || value.mode === "native") result.mode = value.mode;
  for (const key of ["distribution", "python", "script"]) {
    if (typeof value[key] === "string") result[key] = value[key];
  }
  for (const key of ["startupSync", "openSidebar", "notifications", "ribbon"]) {
    if (typeof value[key] === "boolean") result[key] = value[key];
  }
  if (typeof value.maxLogLines === "number" && Number.isFinite(value.maxLogLines)) {
    result.maxLogLines = Math.max(20, Math.min(2e3, Math.floor(value.maxLogLines)));
  }
  return result;
}
function defaultPython(settings, platform) {
  return settings.mode === "native" && platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python";
}

// src/protocol.ts
var isRecord = (v) => !!v && typeof v === "object" && !Array.isArray(v);
function redact(text) {
  return text.replace(/\b(Bearer\s+)[^\s"',}]+/gi, "$1[REDACTED]").replace(/\bsk-[a-zA-Z0-9_-]+/g, "[REDACTED]").replace(/\b([\w-]*(?:api[_-]?key|token|password|secret|authorization)["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,}]+)/gi, "$1[REDACTED]").replace(/(https?:\/\/)[^/\s:@]+:[^/\s@]+@/gi, "$1[REDACTED]@").replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "");
}
var OutputParser = class {
  constructor(object, log, limit = 8 * 1024 * 1024) {
    this.object = object;
    this.log = log;
    this.limit = limit;
    this.line = "";
    this.frame = "";
    this.depth = 0;
    this.quoted = false;
    this.escaped = false;
    this.dropping = false;
  }
  feed(text) {
    for (const char of text) {
      if (this.dropping) {
        if (char === "\n") this.dropping = false;
        continue;
      }
      if (this.frame) {
        this.frame += char;
        if (this.frame.length > this.limit) {
          this.log("Output JSON exceeded the display limit; consult .pipeline/logs locally.");
          this.frame = "";
          this.depth = 0;
          this.dropping = true;
          continue;
        }
        if (this.quoted) {
          if (this.escaped) this.escaped = false;
          else if (char === "\\") this.escaped = true;
          else if (char === '"') this.quoted = false;
        } else if (char === '"') this.quoted = true;
        else if (char === "{" || char === "[") this.depth++;
        else if (char === "}" || char === "]") this.depth--;
        if (this.depth === 0) {
          try {
            const parsed = JSON.parse(this.frame);
            if (isRecord(parsed)) this.object(parsed);
          } catch {
            this.log(this.frame.slice(0, 4e3));
          }
          this.frame = "";
        }
      } else if (char === "{" && !this.line.trim()) {
        this.line = "";
        this.frame = "{";
        this.depth = 1;
        this.quoted = false;
        this.escaped = false;
      } else if (char === "\n") {
        if (this.line.trim()) this.log(this.line.trimEnd());
        this.line = "";
      } else {
        this.line += char;
        if (this.line.length >= 4e3) {
          this.log(this.line + " \u2026 [line truncated]");
          this.line = "";
          this.dropping = true;
        }
      }
    }
  }
  finish() {
    if (this.line.trim()) this.log(this.line.trimEnd());
    if (this.frame) this.log("Incomplete JSON output: " + this.frame.slice(0, 4e3));
    this.line = "";
    this.frame = "";
  }
};
function outcome(code, result) {
  if (code === 2) return "incomplete";
  if (code !== 0) return "failed";
  if (Array.isArray(result?.blocking_errors) && result.blocking_errors.length) return "failed";
  if (result?.complete === false) return "incomplete";
  return "completed";
}
function progress(snapshot, value) {
  if (typeof value.stage !== "string") return;
  snapshot.stage = value.stage;
  for (const key of ["processed", "remaining", "skipped", "failed"]) {
    if (typeof value[key] === "number") snapshot[key] = value[key];
  }
  if (typeof value.cache_hits === "number") snapshot.cacheHits = value.cache_hits;
  if (value.stage === "cache" && typeof value.reused === "number") snapshot.cacheHits = value.reused;
}
function statusDetails(snapshot, value) {
  if (isRecord(value.pending)) snapshot.pending = value.pending;
  if ("last_success" in value) snapshot.lastSuccess = typeof value.last_success === "string" ? value.last_success : void 0;
  if (isRecord(value.progress)) progress(snapshot, value.progress);
  if (isRecord(value.last_report) && typeof value.last_report.cache_hits === "number") {
    snapshot.cacheHits = value.last_report.cache_hits;
  }
}

// src/execution.ts
var ACTION_ARGS = {
  sync: ["sync"],
  full: ["sync", "--full"],
  dry: ["sync", "--dry-run"],
  startup: ["sync", "--startup"],
  apply: ["apply"],
  preview: ["apply", "--dry-run"],
  status: ["status"],
  doctor: ["doctor"]
};
var nodeSpawn = (command, args, options) => (0, import_node_child_process.spawn)(command, args, options);
function validText(value, label) {
  if (/[\u0000-\u001f]/.test(value)) throw new Error(`${label} contains control characters.`);
}
function resolvePath(root, value, windows) {
  validText(value, "Path");
  if (!value || value.startsWith("~")) throw new Error("Use a project-relative or absolute path; ~ is not expanded.");
  const api = windows ? import_node_path.default.win32 : import_node_path.default.posix;
  if (!windows && /^[a-z]:|\\/i.test(value)) throw new Error("Use a Linux path in this execution environment.");
  return api.resolve(root, value);
}
async function capture(command, launch, limit = 128 * 1024) {
  return new Promise((resolve, reject) => {
    const child = launch(command.executable, command.args, command.options);
    let stdout = "", stderr = "", settled = false;
    const out = new import_node_string_decoder.StringDecoder("utf8"), err = new import_node_string_decoder.StringDecoder("utf8");
    const timer = setTimeout(() => {
      child.kill();
      done(new Error("WSL validation timed out. Check the distribution and wsl.exe."));
    }, 15e3);
    const done = (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error);
      else resolve(stdout + out.end());
    };
    const append = (data, isError) => {
      if (settled) return;
      if (isError) stderr += err.write(data);
      else stdout += out.write(data);
      if (stdout.length + stderr.length > limit) {
        child.kill();
        done(new Error("WSL validation returned too much output."));
      }
    };
    child.stdout?.on("data", (data) => append(data, false));
    child.stderr?.on("data", (data) => append(data, true));
    child.once("error", (error) => done(new Error(`Cannot launch ${command.executable}: ${error.message}`)));
    child.once("close", (code) => done(code === 0 ? void 0 : new Error(`WSL validation failed (${code}): ${redact(stderr + err.end()).slice(0, 4e3)}`)));
  });
}
async function prepare(vault, settings, platform, action, launch = nodeSpawn, checkFile = async (file, executable) => {
  if (!(await import_node_fs.promises.stat(file)).isFile()) throw new Error("not a regular file");
  await import_node_fs.promises.access(file, executable && platform !== "win32" ? import_node_fs.constants.X_OK : import_node_fs.constants.R_OK);
}) {
  validText(vault, "Vault");
  validText(settings.distribution, "Distribution");
  if (settings.mode !== "native" && settings.mode !== "wsl") throw new Error("Invalid execution mode.");
  if (!Object.prototype.hasOwnProperty.call(ACTION_ARGS, action)) throw new Error("Unsupported research operation.");
  const windows = platform === "win32";
  if (!(windows ? import_node_path.default.win32 : import_node_path.default.posix).isAbsolute(vault)) throw new Error("Vault must have an absolute filesystem path.");
  const options = { cwd: vault, shell: false, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] };
  let root = vault;
  const wsl = (args2) => ({
    executable: "wsl.exe",
    args: [...settings.distribution ? ["--distribution", settings.distribution] : [], ...args2],
    options
  });
  if (settings.mode === "wsl") {
    if (!windows) throw new Error("WSL mode requires Windows Obsidian. Select Native for Linux Obsidian.");
    if (settings.distribution.startsWith("-")) throw new Error("WSL distribution must be a name, not an option.");
    const translated = await capture(wsl(["--cd", "~", "--exec", "wslpath", "-a", "-u", vault]), launch);
    root = translated.replace(/[\r\n]+$/, "");
    if (!root.startsWith("/") || /[\u0000-\u001f]/.test(root)) throw new Error("wslpath returned an invalid Linux path.");
  }
  const python = resolvePath(root, settings.python || defaultPython(settings, platform), windows && settings.mode === "native");
  const script = resolvePath(root, settings.script || "scripts/research.py", windows && settings.mode === "native");
  if (!script.toLowerCase().endsWith(".py")) throw new Error("CLI script must be a Python .py file.");
  if (settings.mode === "wsl" && /\.(exe|cmd|bat)$/i.test(python)) throw new Error("WSL requires a Linux Python virtual environment.");
  const api = windows && settings.mode === "native" ? import_node_path.default.win32 : import_node_path.default.posix;
  const bin = api.dirname(python);
  const venvLayout = /^(bin|scripts)$/i.test(api.basename(bin));
  const venvConfig = api.join(api.dirname(bin), "pyvenv.cfg");
  let isVenv = false;
  const args = ["-u", script, "--vault", root, ...ACTION_ARGS[action]];
  const required = [[python, true], [script, false], [api.join(root, "config.yaml"), false]];
  if (settings.mode === "native") {
    for (const [file, executable] of required) {
      try {
        await checkFile(file, executable);
      } catch {
        throw new Error(`Missing or inaccessible ${executable ? "Python executable" : "project file"}: ${file}. Configure the project virtual environment and install Python dependencies.`);
      }
    }
    if (venvLayout) {
      try {
        await checkFile(venvConfig, false);
        isVenv = true;
      } catch {
        if (!settings.python) throw new Error(`Project virtual environment is incomplete: missing ${venvConfig}. Recreate it with this environment's Python.`);
      }
    }
    return { executable: python, args, options: {
      ...options,
      env: {
        ...process.env,
        PYTHONUNBUFFERED: "1",
        PYTHONIOENCODING: "utf-8",
        PATH: bin + api.delimiter + (process.env.PATH || ""),
        VIRTUAL_ENV: isVenv ? api.dirname(bin) : void 0
      }
    } };
  }
  for (const [file, executable] of required) {
    try {
      await capture(wsl(["--cd", root, "--exec", "/usr/bin/test", "-f", file]), launch);
      if (executable) await capture(wsl(["--cd", root, "--exec", "/usr/bin/test", "-x", file]), launch);
    } catch (error) {
      throw new Error(`Missing or inaccessible WSL ${executable ? "Python executable" : "project file"}: ${file}. ${String(error)}`);
    }
  }
  if (venvLayout) {
    try {
      await capture(wsl(["--cd", root, "--exec", "/usr/bin/test", "-f", venvConfig]), launch);
      isVenv = true;
    } catch {
      if (!settings.python) throw new Error(`WSL virtual environment is incomplete: missing ${venvConfig}. Create a Linux venv inside this distribution.`);
    }
  }
  const environment = await capture(wsl(["--cd", root, "--exec", "/usr/bin/env", "-0"]), launch);
  const linuxPath = environment.split("\0").find((line) => line.startsWith("PATH="))?.slice(5);
  if (!linuxPath) throw new Error("WSL did not supply PATH; repair the distribution environment.");
  return wsl([
    "--cd",
    root,
    "--exec",
    "/usr/bin/env",
    "-u",
    "VIRTUAL_ENV",
    `PATH=${bin}:${linuxPath}`,
    "PYTHONUNBUFFERED=1",
    "PYTHONIOENCODING=utf-8",
    ...isVenv ? [`VIRTUAL_ENV=${import_node_path.default.posix.dirname(bin)}`] : [],
    python,
    ...args
  ]);
}
var activeVaults = /* @__PURE__ */ new Set();
var Runner = class {
  constructor(changed, launch = nodeSpawn) {
    this.changed = changed;
    this.launch = launch;
    this.snapshot = { status: "idle", stage: "Ready", logs: [], diagnostics: [] };
    this.busy = false;
    this.disposed = false;
  }
  log(line, max) {
    this.snapshot.logs.push(redact(line).slice(0, 4e3));
    if (this.snapshot.logs.length > max) this.snapshot.logs.splice(0, this.snapshot.logs.length - max);
  }
  async run(vault, settings, platform, action) {
    settings = { ...settings };
    const key = platform === "win32" ? import_node_path.default.win32.normalize(vault).toLowerCase() : import_node_path.default.posix.normalize(vault);
    if (this.disposed) throw new Error("Research Vault is unloaded.");
    if (this.busy || activeVaults.has(key)) throw new Error("A Research Vault command is already running in this vault.");
    activeVaults.add(key);
    this.busy = true;
    this.snapshot = {
      ...this.snapshot,
      status: "running",
      stage: `Starting ${action}`,
      processed: void 0,
      remaining: void 0,
      skipped: void 0,
      failed: void 0,
      cacheHits: void 0,
      diagnostics: []
    };
    this.log(`Starting ${action}`, settings.maxLogLines);
    this.changed();
    let result;
    try {
      const command = await prepare(vault, settings, platform, action, this.launch);
      if (this.disposed) throw new Error("Plugin unloaded before the command started.");
      const code = await new Promise((resolve, reject) => {
        const child = this.launch(command.executable, command.args, command.options);
        const consume = (value, stdout) => {
          if (typeof value.stage === "string") progress(this.snapshot, value);
          else if (stdout) result = value;
          else this.log(JSON.stringify(value), settings.maxLogLines);
          this.changed();
        };
        const log = (line) => {
          this.log(line, settings.maxLogLines);
          this.changed();
        };
        const out = new OutputParser((v) => consume(v, true), log);
        const err = new OutputParser((v) => consume(v, false), (line) => {
          log(line);
          this.snapshot.diagnostics.push(redact(line).slice(0, 4e3));
          if (this.snapshot.diagnostics.length > 20) this.snapshot.diagnostics.shift();
        });
        const outDecoder = new import_node_string_decoder.StringDecoder("utf8"), errDecoder = new import_node_string_decoder.StringDecoder("utf8");
        child.stdout?.on("data", (data) => out.feed(outDecoder.write(data)));
        child.stderr?.on("data", (data) => err.feed(errDecoder.write(data)));
        child.once("error", (error) => reject(new Error(`Cannot launch pipeline: ${error.message}. Check Python or WSL settings.`)));
        child.once("close", (exitCode, signal) => {
          out.feed(outDecoder.end());
          err.feed(errDecoder.end());
          out.finish();
          err.finish();
          log(`Process exited: ${exitCode ?? `signal ${signal ?? "unknown"}`}`);
          resolve(exitCode);
        });
      });
      this.snapshot.status = outcome(code, result);
      this.snapshot.stage = `${action}: ${this.snapshot.status}`;
      if (!result) this.log("No final JSON report was available; inspect logs and run Research: Status.", settings.maxLogLines);
      if (result) {
        for (const field of ["warnings", "blocking_errors", "document_errors", "graph_error", "reasoning_error", "failed", "skipped", "partial"]) {
          const detail = result[field];
          if (detail && (!Array.isArray(detail) || detail.length) && (!isRecord(detail) || Object.keys(detail).length)) {
            this.snapshot.diagnostics.push(redact(`${field}: ${JSON.stringify(detail)}`).slice(0, 16e3));
          }
        }
        const report = redact(JSON.stringify(result, null, 2));
        for (const line of report.slice(0, 16e3).split("\n")) this.log(line, settings.maxLogLines);
        if (report.length > 16e3) this.log("Final report display truncated; use the CLI for the full report.", settings.maxLogLines);
      }
      return result;
    } catch (error) {
      this.snapshot.status = "failed";
      this.snapshot.stage = "Launch or execution failed";
      this.log(String(error), settings.maxLogLines);
      this.snapshot.diagnostics.push(redact(String(error)).slice(0, 4e3));
      throw error;
    } finally {
      this.busy = false;
      activeVaults.delete(key);
      this.changed();
    }
  }
};

// src/main.ts
var VIEW_TYPE = "research-vault-sidebar";
var COMMANDS = [
  ["sync", "Research: Sync"],
  ["full", "Research: Full Sync"],
  ["dry", "Research: Dry Run"],
  ["apply", "Research: Apply Reviews"],
  ["status", "Research: Status"],
  ["doctor", "Research: Doctor"],
  ["dashboard", "Research: Open Dashboard"],
  ["reviews", "Research: Open Review Queue"]
];
var ConfirmModal = class extends import_obsidian.Modal {
  constructor(app, title, message, proceed, details) {
    super(app);
    this.title = title;
    this.message = message;
    this.proceed = proceed;
    this.details = details;
  }
  onOpen() {
    this.contentEl.createEl("h2", { text: this.title });
    this.contentEl.createEl("p", { text: this.message });
    if (this.details) this.contentEl.createEl("pre", { text: this.details, cls: "research-vault-diagnostic" });
    new import_obsidian.Setting(this.contentEl).addButton((button) => button.setButtonText("Cancel").onClick(() => this.close())).addButton((button) => button.setButtonText("Continue").setWarning().onClick(() => {
      this.close();
      this.proceed();
    }));
  }
  onClose() {
    this.contentEl.empty();
  }
};
var ReviewPicker = class extends import_obsidian.SuggestModal {
  constructor(app) {
    super(app);
    this.setPlaceholder("Find links, claim drafts, removals or revisions");
  }
  getSuggestions(query) {
    return this.app.vault.getMarkdownFiles().filter((file) => /^Reviews\/(Links|Removals|Revisions)\//.test(file.path) && file.path.toLowerCase().includes(query.toLowerCase())).sort((a, b) => a.path.localeCompare(b.path));
  }
  getSuggestionText(file) {
    const status = this.app.metadataCache.getFileCache(file)?.frontmatter?.status;
    return `${file.path}${typeof status === "string" ? ` (${status})` : ""}`;
  }
  renderSuggestion(file, el) {
    el.setText(this.getSuggestionText(file));
  }
  onChooseSuggestion(file) {
    void this.app.workspace.getLeaf(false).openFile(file);
  }
};
var ResearchView = class extends import_obsidian.ItemView {
  constructor(leaf, plugin) {
    super(leaf);
    this.plugin = plugin;
  }
  getViewType() {
    return VIEW_TYPE;
  }
  getDisplayText() {
    return "Research Vault";
  }
  getIcon() {
    return "microscope";
  }
  async onOpen() {
    this.render();
  }
  render() {
    const el = this.contentEl;
    const previous = el.querySelector(".research-vault-logs");
    const scroll = previous?.scrollTop ?? 0;
    const follow = !previous || previous.scrollHeight - previous.scrollTop - previous.clientHeight < 30;
    el.empty();
    el.addClass("research-vault-view");
    el.createEl("h2", { text: "Research Vault" });
    const state = this.plugin.runner.snapshot;
    el.createEl("p", { text: `Status: ${state.status}`, cls: `research-vault-status status-${state.status}` });
    el.createEl("p", { text: `Stage: ${redact(state.stage)}` });
    const stats = el.createEl("dl", { cls: "research-vault-stats" });
    for (const [label, value] of [
      ["Documents processed", state.processed],
      ["Documents remaining", state.remaining],
      ["Failed documents", state.failed],
      ["Skipped documents", state.skipped],
      ["Cache hits", state.cacheHits],
      ["Pending conceptual links", state.pending?.link],
      ["Pending claim drafts", state.pending?.claim],
      ["Pending removals", state.pending?.removal],
      ["Pending revisions", state.pending?.revision],
      ["Last successful sync", state.lastSuccess]
    ]) {
      stats.createEl("dt", { text: String(label) });
      stats.createEl("dd", { text: typeof value === "number" || typeof value === "string" ? redact(String(value)) : "Not reported" });
    }
    const controls = el.createDiv({ cls: "research-vault-controls" });
    for (const [action, label] of [["sync", "Sync"], ["apply", "Apply Reviews"], ["doctor", "Doctor"], ["status", "Refresh Status"]]) {
      const button = controls.createEl("button", { text: label });
      button.disabled = this.plugin.runner.busy;
      button.addEventListener("click", () => this.plugin.request(action));
    }
    controls.createEl("button", { text: "Open Review Queue" }).addEventListener("click", () => this.plugin.openReviews());
    controls.createEl("button", { text: "Open Dashboard" }).addEventListener("click", () => {
      void this.plugin.openDashboard();
    });
    el.createEl("h3", { text: "Warnings and errors" });
    if (!state.diagnostics.length) el.createEl("p", { text: "No diagnostics reported for this command." });
    for (const diagnostic of state.diagnostics) el.createEl("pre", { text: diagnostic, cls: "research-vault-diagnostic" });
    el.createEl("h3", { text: "Recent output" });
    const logs = el.createEl("pre", { text: state.logs.join("\n"), cls: "research-vault-logs" });
    logs.scrollTop = follow ? logs.scrollHeight : scroll;
    el.createEl("p", { text: "Full reports: .pipeline/logs. Review decisions are edited in review notes and validated by Python.", cls: "setting-item-description" });
  }
};
var ResearchVaultPlugin = class extends import_obsidian.Plugin {
  constructor() {
    super(...arguments);
    this.runner = new Runner(() => this.scheduleRender());
    this.layoutHandled = false;
  }
  async onload() {
    this.settings = loadSettings(await this.loadData(), process.platform);
    this.registerView(VIEW_TYPE, (leaf) => new ResearchView(leaf, this));
    this.addSettingTab(new ResearchSettingTab(this.app, this));
    for (const [id, name] of COMMANDS) {
      this.addCommand({ id, name, callback: () => {
        if (id === "dashboard") void this.openDashboard();
        else if (id === "reviews") this.openReviews();
        else this.request(id);
      } });
    }
    if (this.settings.ribbon) this.addRibbonIcon("microscope", "Open Research Vault", () => {
      void this.openSidebar();
    });
    this.app.workspace.onLayoutReady(() => {
      void this.ready().catch((error) => {
        new import_obsidian.Notice(`Research Vault startup: ${redact(String(error)).slice(0, 500)}`);
      });
    });
  }
  async ready() {
    if (this.runner.disposed || this.layoutHandled) return;
    this.layoutHandled = true;
    if (this.settings.openSidebar) await this.openSidebar();
    if (this.runner.disposed || this.runner.busy) return;
    await this.execute(this.settings.startupSync ? "startup" : "status", true);
  }
  onunload() {
    this.runner.disposed = true;
    if (this.renderTimer) clearTimeout(this.renderTimer);
  }
  scheduleRender() {
    if (this.runner.disposed || this.renderTimer) return;
    this.renderTimer = setTimeout(() => {
      this.renderTimer = void 0;
      for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE)) {
        if (leaf.view instanceof ResearchView) leaf.view.render();
      }
    }, 100);
  }
  request(action) {
    if (this.runner.busy) {
      new import_obsidian.Notice("A Research Vault command is already running.");
      return;
    }
    if (action === "apply") {
      void this.previewReviews();
      return;
    }
    if (action === "full") {
      new ConfirmModal(
        this.app,
        "Full synchronization",
        "This reprocesses the collection and can involve substantial LLM processing and usage costs. Continue?",
        () => {
          void this.execute(action);
        }
      ).open();
    } else void this.execute(action);
  }
  async previewReviews() {
    const preview = await this.execute("preview", true);
    if (!preview || this.runner.snapshot.status !== "completed" || this.runner.disposed) return;
    const details = redact(JSON.stringify(preview, null, 2));
    if (details.length > 32e3) {
      new import_obsidian.Notice("The approval preview is too large for this dialog. Review and apply using the terminal CLI.");
      return;
    }
    new ConfirmModal(
      this.app,
      "Apply reviewed decisions",
      "Python preview of explicitly approved decisions, including any approved/delete removals. Continue only after reviewing these exact paths and impacts. Python revalidates approvals and requires a clean Git tree and a verified backup for deletion.",
      () => {
        void this.execute("apply");
      },
      details
    ).open();
  }
  async execute(action, quiet = false) {
    try {
      if (!(this.app.vault.adapter instanceof import_obsidian.FileSystemAdapter)) throw new Error("Research Vault requires a local desktop filesystem vault.");
      const vault = this.app.vault.adapter.getBasePath();
      const settings = { ...this.settings };
      const result = await this.runner.run(vault, settings, process.platform, action);
      if (action === "status" && result && this.runner.snapshot.status !== "failed") {
        statusDetails(this.runner.snapshot, result);
        if (isRecord(result.last_report)) this.runner.snapshot.status = outcome(0, result.last_report);
        else this.runner.snapshot.status = "idle";
      } else if (["sync", "full", "startup", "apply"].includes(action) && !this.runner.disposed) {
        const finished = { ...this.runner.snapshot };
        try {
          const status = await this.runner.run(vault, settings, process.platform, "status");
          if (status && this.runner.snapshot.status !== "failed") {
            const stage = finished.stage;
            statusDetails(finished, status);
            if (finished.status === "failed") finished.stage = stage;
          } else this.runner.log("Status refresh failed; see recent output.", settings.maxLogLines);
          this.runner.snapshot = { ...finished, logs: this.runner.snapshot.logs };
        } catch (error) {
          this.runner.snapshot = { ...finished, logs: this.runner.snapshot.logs };
          this.runner.log(`Could not refresh status: ${String(error)}`, this.settings.maxLogLines);
        }
      }
      this.scheduleRender();
      if (!this.runner.disposed && (this.runner.snapshot.status === "failed" || this.settings.notifications && (!quiet || action === "startup"))) {
        new import_obsidian.Notice(`Research Vault ${action}: ${this.runner.snapshot.status}. See the sidebar for details.`);
      }
      return result;
    } catch (error) {
      if (!this.runner.disposed) {
        if (!this.runner.busy) this.runner.snapshot.diagnostics = [redact(String(error))];
        this.scheduleRender();
        new import_obsidian.Notice(`Research Vault: ${redact(String(error)).slice(0, 500)}`);
      }
      return void 0;
    }
  }
  async openSidebar() {
    let leaf = this.app.workspace.getLeavesOfType(VIEW_TYPE)[0];
    if (!leaf) {
      const right = this.app.workspace.getRightLeaf(false);
      if (!right) {
        new import_obsidian.Notice("Cannot open Research Vault sidebar.");
        return;
      }
      leaf = right;
      await leaf.setViewState({ type: VIEW_TYPE, active: true });
    }
    await this.app.workspace.revealLeaf(leaf);
  }
  async openDashboard() {
    const file = this.app.vault.getAbstractFileByPath("Home.md");
    if (file instanceof import_obsidian.TFile) await this.app.workspace.getLeaf(false).openFile(file);
    else new import_obsidian.Notice("Home.md is missing from this vault.");
  }
  openReviews() {
    new ReviewPicker(this.app).open();
  }
  async saveSettings() {
    await this.saveData(this.settings);
  }
};
var ResearchSettingTab = class extends import_obsidian.PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }
  display() {
    const el = this.containerEl;
    el.empty();
    el.createEl("h2", { text: "Research Vault" });
    el.createEl("p", { text: "Runs trusted local Python code. Zotero and LLM settings stay in config.yaml. Save settings before starting a command." });
    const settings = this.plugin.settings;
    new import_obsidian.Setting(el).setName("Execution environment").setDesc("Windows defaults to WSL; Linux defaults to Native. The virtual environment must match the selected environment.").addDropdown((dropdown) => dropdown.addOption("native", "Native").addOption("wsl", "WSL").setValue(settings.mode).onChange(async (value) => {
      settings.mode = value === "wsl" ? "wsl" : "native";
      await this.plugin.saveSettings();
      this.display();
    }));
    for (const [key, name, description, placeholder] of [
      ["distribution", "WSL distribution", "Blank uses your default distribution. Used only in WSL mode.", "Default distribution"],
      ["python", "Python executable path", "Blank uses the project virtual environment. A path only, without arguments; in WSL use a Linux path.", defaultPython(settings, process.platform)],
      ["script", "Custom CLI script path", "Blank uses scripts/research.py. Custom scripts must support the same CLI, including --vault.", "scripts/research.py"]
    ]) {
      new import_obsidian.Setting(el).setName(name).setDesc(description).addText((text) => text.setPlaceholder(placeholder).setValue(settings[key]).onChange(async (value) => {
        settings[key] = value;
        await this.plugin.saveSettings();
      }));
    }
    for (const [key, name, description] of [
      ["startupSync", "Synchronize at startup", "Opt in to a bounded incremental sync once the workspace is ready. Never runs a full sync."],
      ["openSidebar", "Open sidebar automatically", "Open the Research Vault sidebar after the workspace is ready."],
      ["notifications", "Show completion notifications", "Errors are always reported."],
      ["ribbon", "Show ribbon icon", "Reload this plugin to apply this setting."]
    ]) {
      new import_obsidian.Setting(el).setName(name).setDesc(description).addToggle((toggle) => toggle.setValue(settings[key]).onChange(async (value) => {
        settings[key] = value;
        await this.plugin.saveSettings();
      }));
    }
    new import_obsidian.Setting(el).setName("Maximum displayed log lines").setDesc("20\u20132000 lines. Long lines and final reports are also bounded; full pipeline reports remain on disk.").addText((text) => text.setValue(String(settings.maxLogLines)).onChange(async (value) => {
      const max = Number(value);
      if (Number.isInteger(max) && max >= 20 && max <= 2e3) {
        settings.maxLogLines = max;
        await this.plugin.saveSettings();
      }
    }));
    new import_obsidian.Setting(el).setName("Research Vault sidebar").addButton((button) => button.setButtonText("Open sidebar").onClick(() => {
      void this.plugin.openSidebar();
    }));
  }
};
