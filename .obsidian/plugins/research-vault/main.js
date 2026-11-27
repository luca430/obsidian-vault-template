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
  ResearchSettingTab: () => ResearchSettingTab,
  ResearchView: () => ResearchView,
  VIEW_TYPE: () => VIEW_TYPE,
  default: () => ResearchVaultPlugin
});
module.exports = __toCommonJS(main_exports);
var import_obsidian2 = require("obsidian");
var import_node_crypto2 = require("node:crypto");
var import_node_fs2 = require("node:fs");
var import_node_path2 = __toESM(require("node:path"));

// src/agent-settings.ts
var import_obsidian = require("obsidian");

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
  if (result?.status === "failed") return "failed";
  if ((code === 0 || code === 2) && result?.technical_complete === true && result?.status === "completed_with_limitations") return "completed";
  if (code === 2) return "incomplete";
  if (code !== 0) return "failed";
  if (Array.isArray(result?.blocking_errors) && result.blocking_errors.length) return "failed";
  if (result?.complete === false) return "incomplete";
  return "completed";
}
function progress(snapshot, value) {
  if (typeof value.stage !== "string") return;
  snapshot.stage = value.stage + (typeof value.reason === "string" ? `: ${value.reason}` : "");
  for (const key of ["processed", "remaining", "skipped", "failed"]) {
    if (typeof value[key] === "number") snapshot[key] = value[key];
  }
  if (typeof value.cache_hits === "number") snapshot.cacheHits = value.cache_hits;
  if (value.stage === "cache" && typeof value.reused === "number") snapshot.cacheHits = value.reused;
}
function statusDetails(snapshot, value) {
  if (isRecord(value.usage_by_agent)) snapshot.agentUsage = value.usage_by_agent;
  if (isRecord(value.task_status)) snapshot.taskCounts = value.task_status;
  snapshot.resumeInstructions = typeof value.resume_instructions === "string" ? value.resume_instructions : void 0;
  if (typeof value.status === "string") snapshot.syncStatus = value.status;
  if (isRecord(value.pending)) snapshot.pending = value.pending;
  if ("last_success" in value) snapshot.lastSuccess = typeof value.last_success === "string" ? value.last_success : void 0;
  if (isRecord(value.progress)) progress(snapshot, value.progress);
  if (isRecord(value.last_report) && typeof value.last_report.cache_hits === "number") {
    snapshot.cacheHits = value.last_report.cache_hits;
  }
}

// src/agent-settings.ts
var roles = ["literature", "manuscript", "concepts", "linking", "synthesis", "maintenance", "consolidation"];
var labels = { codex: "Codex", claude: "Claude Code" };
var providerDefault = "__provider_default__";
var AgentSettingsEditor = class {
  constructor(el, report, save, refresh, saved) {
    this.el = el;
    this.report = report;
    this.save = save;
    this.refresh = refresh;
    this.saved = saved;
    this.load(report);
    this.render();
  }
  load(report) {
    this.report = report;
    this.request = JSON.parse(JSON.stringify({ plan_token: report.plan_token, defaults: report.defaults ?? {}, agents: report.agents ?? {}, default_provider: report.default_provider ?? "codex" }));
  }
  providers() {
    return isRecord(this.report.providers) ? this.report.providers : {};
  }
  authenticated(provider) {
    const status = this.providers()[provider];
    return isRecord(status) && status.authenticated === true;
  }
  profile(role) {
    const value = this.request.agents[role];
    return isRecord(value) ? value : typeof value === "string" ? { provider: value } : {};
  }
  store(role, profile) {
    const agents = this.request.agents;
    if (role === "consolidation" && !(role in agents) && Object.keys(profile).length) {
      const inherited = this.resolved("linking");
      const merged = { ...inherited, ...profile, retry_policy: { ...isRecord(inherited.retry_policy) ? inherited.retry_policy : {}, ...isRecord(profile.retry_policy) ? profile.retry_policy : {} } };
      Object.assign(profile, JSON.parse(JSON.stringify(merged)));
    }
    if (Object.keys(profile).length) agents[role] = profile;
    else delete agents[role];
  }
  resolved(role) {
    const profile = role ? this.profile(role) : {};
    if (role === "consolidation" && !(role in this.request.agents)) return this.resolved("linking");
    const provider = String(profile.provider ?? this.request.default_provider);
    const defaults2 = isRecord(this.report.provider_defaults) && isRecord(this.report.provider_defaults[provider]) ? this.report.provider_defaults[provider] : {};
    return { ...defaults2, ...this.request.defaults, ...profile, provider };
  }
  models(provider) {
    const status = this.providers()[provider];
    return isRecord(status) && Array.isArray(status.models) ? status.models.filter(isRecord) : [];
  }
  render() {
    const el = this.el;
    el.empty();
    el.createEl("h3", { text: "Agent-specific models and reasoning" });
    for (const [provider, label] of Object.entries(labels)) {
      const status = this.providers()[provider];
      el.createEl("p", { text: `${label}: ${isRecord(status) ? String(status.message) : "Login has not been checked."}${this.authenticated(provider) ? "" : ` Sign in with ${provider === "codex" ? "codex login" : "claude auth login"}, then refresh.`}` });
    }
    if (this.refresh) new import_obsidian.Setting(el).setName("Provider login status and model lists").setDesc("Refresh preserves staged choices and sends no inference requests.").addButton((button) => button.setButtonText("Refresh login and models").onClick(async () => {
      button.setDisabled(true);
      try {
        const report = await this.refresh();
        if (report) {
          this.report = { ...this.report, providers: report.providers };
          if (report.plan_token !== this.request.plan_token) new import_obsidian.Notice("Configuration changed externally. Reopen settings before saving; staged choices are retained here.");
          this.render();
        }
      } finally {
        button.setDisabled(false);
      }
    }));
    new import_obsidian.Setting(el).setName("Default provider").setDesc("Changing provider resets affected model and intensity selections to provider defaults. Choose replacements before saving.").addDropdown((control) => {
      this.providerOptions(control);
      control.setValue(String(this.request.default_provider)).onChange((value) => {
        if (!this.authenticated(value)) return;
        this.request.default_provider = value;
        const defaults2 = this.request.defaults;
        defaults2.model = null;
        defaults2.reasoning_effort = null;
        if (value === "codex") delete defaults2.max_output_tokens;
        for (const entry of Object.values(this.request.agents)) {
          if (isRecord(entry) && !entry.provider) {
            entry.model = null;
            entry.reasoning_effort = null;
            if (value === "codex") delete entry.max_output_tokens;
          }
        }
        this.render();
      });
    });
    const defaultsSection = el.createEl("details");
    defaultsSection.createEl("summary", { text: "Global model and advanced defaults" });
    this.modelFields(defaultsSection, this.request.defaults, this.resolved(), () => this.render());
    this.advanced(defaultsSection, this.request.defaults, this.resolved());
    for (const role of roles) {
      const section = el.createDiv({ cls: "research-vault-agent-settings" });
      section.createEl("h4", { text: role });
      const profile = this.profile(role);
      const resolved = this.resolved(role);
      new import_obsidian.Setting(section).setName("Provider").setDesc(`${role === "consolidation" ? "Inherits linking unless this agent has overrides." : "Inherits the default provider unless overridden."} Changing provider resets its model and intensity to provider defaults.`).addDropdown((control) => {
        control.addOption("", "Inherit");
        this.providerOptions(control);
        control.setValue(String(profile.provider ?? "")).onChange((value) => {
          if (value && !this.authenticated(value)) return;
          if (value) profile.provider = value;
          else delete profile.provider;
          profile.model = null;
          profile.reasoning_effort = null;
          if (String(profile.provider ?? this.request.default_provider) === "codex") delete profile.max_output_tokens;
          this.store(role, profile);
          this.render();
        });
      });
      this.modelFields(section, profile, resolved, () => {
        this.store(role, profile);
        this.render();
      });
      const advanced = section.createEl("details");
      advanced.createEl("summary", { text: "Limits, timeout and retries" });
      this.advanced(advanced, profile, resolved, () => this.store(role, profile));
    }
    el.createEl("p", { text: "Login is checked in the selected execution environment. Model access is verified on actual requests. Codex choices use local model metadata; Claude aliases do not certify account access. Configured model catalogs take precedence. Changes apply after Save to future tasks; completed scientific notes are retained." });
    new import_obsidian.Setting(el).addButton((button) => button.setButtonText("Save agent settings").setCta().onClick(async () => {
      button.setDisabled(true);
      try {
        const result = await this.save(this.request);
        if (result) {
          if (isRecord(result)) {
            this.load(result);
            this.render();
          }
          new import_obsidian.Notice("Agent settings saved.");
          this.saved?.();
        }
      } catch (error) {
        new import_obsidian.Notice(String(error));
      } finally {
        button.setDisabled(false);
      }
    }));
  }
  providerOptions(control) {
    for (const [provider, label] of Object.entries(labels)) {
      control.addOption(provider, this.authenticated(provider) ? label : `${label} (login required)`);
      const option = Array.from(control.selectEl.options).find((item) => item.value === provider);
      if (option) option.disabled = !this.authenticated(provider);
    }
  }
  modelFields(el, profile, resolved, changed) {
    const provider = String(resolved.provider);
    const enabled = this.authenticated(provider);
    const models = this.models(provider);
    const model = models.find((row) => row.id === resolved.model);
    const selected = profile.model === null ? providerDefault : String(profile.model ?? "");
    new import_obsidian.Setting(el).setName("Model").setDesc(`Models for ${labels[provider] ?? provider}. Inherited: ${resolved.model ?? "provider default"}.`).addDropdown((control) => {
      control.addOption("", "Inherit").addOption(providerDefault, "Provider default");
      for (const row of models) control.addOption(String(row.id), `${row.label ?? row.id} (${row.id})`);
      if (selected && selected !== providerDefault && !models.some((row) => row.id === selected)) control.addOption(selected, `${selected} (saved; not in current list)`);
      control.setValue(selected).setDisabled(!enabled).onChange((value) => {
        if (!enabled) return;
        if (value === providerDefault) profile.model = null;
        else if (value) profile.model = value;
        else delete profile.model;
        changed();
      });
    });
    const supported = model && Array.isArray(model.reasoning_efforts) ? model.reasoning_efforts.filter((v) => typeof v === "string") : [];
    const intensity = profile.reasoning_effort === null ? providerDefault : String(profile.reasoning_effort ?? "");
    new import_obsidian.Setting(el).setName("Reasoning intensity").setDesc(model ? `Options for this model from ${model.source}.` : "Select a model to choose its supported intensity. Inherit/provider default leave the choice to configuration or the CLI.").addDropdown((control) => {
      control.addOption("", "Inherit").addOption(providerDefault, "Provider default");
      for (const effort of supported) control.addOption(effort, effort);
      if (intensity && intensity !== providerDefault && !supported.includes(intensity)) {
        control.addOption(intensity, `${intensity} (saved; support unverified)`);
        const option = Array.from(control.selectEl.options).find((item) => item.value === intensity);
        if (option) option.disabled = true;
      }
      control.setValue(intensity).setDisabled(!enabled).onChange((value) => {
        if (!enabled) return;
        if (value === providerDefault) profile.reasoning_effort = null;
        else if (value && supported.includes(value)) profile.reasoning_effort = value;
        else if (!value) delete profile.reasoning_effort;
        changed();
      });
    });
  }
  advanced(el, profile, inherited, changed = () => {
  }) {
    for (const [key, label] of [["max_input_chars", "Input character limit"], ["output_reserve_chars", "Output character reserve"], ["max_output_tokens", "Native output-token cap (Claude only)"], ["timeout_seconds", "Timeout in seconds"]]) {
      if (key === "max_output_tokens" && inherited.provider !== "claude") continue;
      new import_obsidian.Setting(el).setName(label).setDesc("Blank inherits.").addText((control) => control.setValue(String(profile[key] ?? "")).setPlaceholder(String(inherited[key] ?? "Inherit")).onChange((value) => {
        if (value.trim()) profile[key] = Number(value);
        else delete profile[key];
        changed();
      }));
    }
    const policy = isRecord(profile.retry_policy) ? profile.retry_policy : {};
    for (const [key, label] of [["max_attempts", "Maximum attempts"], ["retry_after_seconds", "Rate-limit retry delay (seconds)"]]) {
      new import_obsidian.Setting(el).setName(label).setDesc("Retries occur on the next eligible sync.").addText((control) => control.setValue(String(policy[key] ?? "")).onChange((value) => {
        if (value.trim()) policy[key] = Number(value);
        else delete policy[key];
        if (Object.keys(policy).length) profile.retry_policy = policy;
        else delete profile.retry_policy;
        changed();
      }));
    }
  }
};
var AgentSettingsModal = class extends import_obsidian.Modal {
  constructor(app, report, save, refresh) {
    super(app);
    this.report = report;
    this.save = save;
    this.refresh = refresh;
  }
  onOpen() {
    new AgentSettingsEditor(this.contentEl, this.report, this.save, this.refresh, () => this.close());
  }
  onClose() {
    this.contentEl.empty();
  }
};

// src/execution.ts
var import_node_child_process = require("node:child_process");
var import_node_fs = require("node:fs");
var import_node_path = __toESM(require("node:path"));
var import_node_string_decoder = require("node:string_decoder");
var import_node_crypto = require("node:crypto");

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

// src/execution.ts
var ACTION_ARGS = {
  sync: ["sync"],
  full: ["sync", "--full"],
  dry: ["sync", "--dry-run"],
  startup: ["sync", "--startup"],
  apply: ["apply"],
  preview: ["apply", "--dry-run"],
  status: ["status"],
  doctor: ["doctor"],
  readiness: ["review", "status"],
  refresh: ["review", "refresh"],
  consolidate: ["concepts", "suggest"],
  history: ["review", "history"],
  agentSettings: ["settings", "show"],
  saveAgentSettings: ["settings", "save"]
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
}, selection) {
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
  if (action === "saveAgentSettings") {
    if (!selection?.settingsRequestId || !/^[a-f0-9]{32}$/.test(selection.settingsRequestId)) throw new Error("Invalid agent settings request.");
    args.push("--request-id", selection.settingsRequestId);
  }
  if (selection?.controlId) {
    if (!["sync", "full", "startup", "dry"].includes(action) || !/^[a-f0-9]{32}$/.test(selection.controlId)) throw new Error("Invalid sync control identifier.");
    args.push("--control-id", selection.controlId);
  }
  if (action === "consolidate" && selection?.memberPaths) {
    if (selection.id || selection.memberPaths.length < 2 || selection.memberPaths.some((p) => !/^Concepts\/[^\\\u0000-\u001f]+\.md$/.test(p) || p.split("/").includes(".."))) throw new Error("Select distinct concept paths.");
    for (const member of selection.memberPaths) args.push("--member", member);
  } else if (action === "refresh" || action === "consolidate" || action === "history" && selection?.id) {
    if (!selection?.id || !/^[a-f0-9]{24}$/.test(selection.id)) throw new Error("Select a valid review or candidate ID.");
    args.push(selection.id);
  }
  if (selection?.proposalIds) {
    if (!["apply", "preview"].includes(action) || !selection.proposalIds.length || selection.proposalIds.some((id) => !/^[a-f0-9]{24}$/.test(id))) throw new Error("Select valid approved proposal IDs.");
    for (const id of selection.proposalIds) args.push("--proposal", id);
  }
  if (selection?.planToken) {
    if (action !== "apply" || !/^[a-f0-9]{64}$/.test(selection.planToken)) throw new Error("Invalid review preview token.");
    args.push("--plan-token", selection.planToken);
  }
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
  get canInterrupt() {
    return this.busy && !!this.control && !this.control.interrupted;
  }
  async interrupt() {
    const control = this.control;
    if (!this.canInterrupt || !control) return;
    control.interrupted = true;
    try {
      this.interrupting = (async () => {
        for (const directory of [import_node_path.default.join(control.vault, ".pipeline"), import_node_path.default.join(control.vault, ".pipeline", "cancel")]) {
          await import_node_fs.promises.mkdir(directory).catch((error) => {
            if (error.code !== "EEXIST") throw error;
          });
          const info = await import_node_fs.promises.lstat(directory);
          if (info.isSymbolicLink() || !info.isDirectory()) throw new Error("Cancellation directory must be a local directory, without symlinks.");
        }
        await import_node_fs.promises.writeFile(import_node_path.default.join(control.vault, ".pipeline", "cancel", control.id), "interrupt\n", { flag: "wx" });
        this.snapshot.stage = "Stopping synchronization at a safe boundary";
        this.changed();
      })();
      await this.interrupting;
    } catch (error) {
      control.interrupted = false;
      throw error;
    }
  }
  log(line, max) {
    this.snapshot.logs.push(redact(line).slice(0, 4e3));
    if (this.snapshot.logs.length > max) this.snapshot.logs.splice(0, this.snapshot.logs.length - max);
  }
  async run(vault, settings, platform, action, selection) {
    settings = { ...settings };
    const key = platform === "win32" ? import_node_path.default.win32.normalize(vault).toLowerCase() : import_node_path.default.posix.normalize(vault);
    if (this.disposed) throw new Error("Research Vault is unloaded.");
    if (this.busy || activeVaults.has(key)) throw new Error("A Research Vault command is already running in this vault.");
    activeVaults.add(key);
    this.busy = true;
    if (["sync", "full", "startup", "dry"].includes(action)) {
      this.control = { vault, id: (0, import_node_crypto.randomBytes)(16).toString("hex"), interrupted: false };
      selection = { ...selection, controlId: this.control.id };
    }
    this.snapshot = {
      ...this.snapshot,
      status: "running",
      stage: `Starting ${action}`,
      processed: void 0,
      remaining: void 0,
      skipped: void 0,
      failed: void 0,
      cacheHits: void 0,
      diagnostics: [],
      syncStatus: void 0
    };
    this.log(`Starting ${action}`, settings.maxLogLines);
    this.changed();
    let result;
    try {
      const command = await prepare(vault, settings, platform, action, this.launch, void 0, selection);
      if (this.disposed) throw new Error("Plugin unloaded before the command started.");
      if (this.control?.interrupted) throw new Error("Synchronization interrupted before launch.");
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
      this.snapshot.status = this.control?.interrupted ? "failed" : outcome(code, result);
      if (typeof result?.status === "string") this.snapshot.syncStatus = result.status;
      if (result) {
        if (isRecord(result.usage_by_agent)) this.snapshot.agentUsage = result.usage_by_agent;
        if (isRecord(result.task_status)) this.snapshot.taskCounts = result.task_status;
        this.snapshot.resumeInstructions = typeof result.resume_instructions === "string" ? result.resume_instructions : void 0;
      }
      this.snapshot.stage = `${action}: ${this.snapshot.status}`;
      if (!result) this.log("No final JSON report was available; inspect logs and run Research: Status.", settings.maxLogLines);
      if (result) {
        for (const field of ["warnings", "blocking_errors", "document_errors", "graph_error", "reasoning_error", "provider_interruptions", "resume_instructions", "usage_by_agent", "task_status", "failed", "skipped", "partial"]) {
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
      await this.interrupting?.catch(() => void 0);
      if (this.control) await import_node_fs.promises.unlink(import_node_path.default.join(this.control.vault, ".pipeline", "cancel", this.control.id)).catch(() => void 0);
      this.interrupting = void 0;
      this.control = void 0;
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
  ["interrupt", "Research: Interrupt Sync"],
  ["status", "Research: Status"],
  ["doctor", "Research: Doctor"],
  ["dashboard", "Research: Open Dashboard"],
  ["reviews", "Research: Open Review Queue"],
  ["review-history", "Research: Open Review History"],
  ["review-readiness", "Research: Review Readiness"],
  ["agent-settings", "Research: Agent Settings"]
];
var ConfirmModal = class extends import_obsidian2.Modal {
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
    new import_obsidian2.Setting(this.contentEl).addButton((button) => button.setButtonText("Cancel").onClick(() => this.close())).addButton((button) => button.setButtonText("Continue").setWarning().onClick(() => {
      this.close();
      this.proceed();
    }));
  }
  onClose() {
    this.contentEl.empty();
  }
};
var ReviewReadinessModal = class extends import_obsidian2.Modal {
  constructor(app, plugin, report) {
    super(app);
    this.plugin = plugin;
    this.report = report;
  }
  onOpen() {
    const el = this.contentEl;
    el.createEl("h2", { text: "Review readiness" });
    el.createEl("p", { text: "Select ready approvals to preview. Blocked reviews stay unchanged. Refresh creates a new pending review without running Sync." });
    const selected = /* @__PURE__ */ new Set();
    const priority = (item) => item.state === "ready" ? 0 : item.state === "needs-review" ? 2 : 1;
    const items = Array.isArray(this.report.items) ? this.report.items.filter(isRecord).sort((a, b) => priority(a) - priority(b)) : [];
    for (const item of items) {
      const row = el.createDiv();
      row.createEl("p", { text: `${item.state}: ${item.path}` });
      row.createEl("p", { text: String(item.reason ?? "") });
      const controls = new import_obsidian2.Setting(row);
      if (item.state === "ready" && typeof item.id === "string") {
        selected.add(item.id);
        controls.addToggle((toggle) => toggle.setValue(true).onChange((value) => {
          if (value) selected.add(String(item.id));
          else selected.delete(String(item.id));
        }));
      }
      controls.addButton((button) => button.setButtonText("Open review").onClick(() => {
        const file = this.app.vault.getAbstractFileByPath(String(item.path));
        if (file instanceof import_obsidian2.TFile) void this.app.workspace.getLeaf(false).openFile(file);
      }));
      if (item.state === "needs-revalidation" && item.refreshable === true && typeof item.id === "string") {
        controls.addButton((button) => button.setButtonText("Refresh review").onClick(() => {
          this.close();
          void this.plugin.refreshReview(String(item.id));
        }));
      }
      if (Array.isArray(item.differences)) {
        for (const difference of item.differences.filter(isRecord)) {
          const text = String(difference.diff ?? difference.reason ?? "");
          row.createEl("pre", { text: `${difference.note}
${text.slice(0, 16e3)}${text.length > 16e3 ? "\nDisplay truncated; use review status in the CLI for the full diff." : ""}`, cls: "research-vault-diagnostic" });
        }
      }
    }
    const groups = Array.isArray(this.report.concept_candidates) ? this.report.concept_candidates.filter(isRecord) : [];
    for (const group of groups) {
      const row = el.createDiv();
      row.createEl("h3", { text: "Possible concept consolidation" });
      row.createEl("p", { text: String(group.reason ?? "") });
      if (Array.isArray(group.members)) for (const member of group.members.filter(isRecord)) {
        row.createEl("p", { text: String(member.path) });
      }
      new import_obsidian2.Setting(row).addButton((button) => button.setButtonText("Suggest consolidation").onClick(() => {
        new ConfirmModal(
          this.app,
          "Prepare a consolidation suggestion",
          "The configured consolidation agent will compare these candidates and propose merging, keeping them separate, or relating them. This uses inference and creates a pending review for your approval.",
          () => {
            this.close();
            void this.plugin.suggestConsolidation(String(group.id));
          }
        ).open();
      }));
    }
    if (Array.isArray(this.report.concept_catalog)) {
      new import_obsidian2.Setting(el).addButton((button) => button.setButtonText("Compare other concepts").onClick(() => {
        new ConceptComparisonModal(this.app, this.plugin, this.report.concept_catalog.filter(isRecord)).open();
      }));
    }
    new import_obsidian2.Setting(el).addButton((button) => button.setButtonText("Preview selected").onClick(() => {
      if (!selected.size) {
        new import_obsidian2.Notice("Select at least one ready approval.");
        return;
      }
      this.close();
      void this.plugin.previewReviews([...selected]);
    }));
  }
  onClose() {
    this.contentEl.empty();
  }
};
var ConceptComparisonModal = class extends import_obsidian2.Modal {
  constructor(app, plugin, catalog) {
    super(app);
    this.plugin = plugin;
    this.catalog = catalog;
  }
  onOpen() {
    const selected = /* @__PURE__ */ new Set();
    this.contentEl.createEl("h2", { text: "Compare existing or proposed concepts" });
    this.contentEl.createEl("p", { text: "Choose at least two concepts. This also supports differently named concepts that the name-based candidate search did not find." });
    for (const member of this.catalog) {
      const row = this.contentEl.createDiv();
      row.createEl("p", { text: String(member.path) });
      new import_obsidian2.Setting(row).addToggle((toggle) => toggle.setValue(false).onChange((value) => {
        if (value) selected.add(String(member.path));
        else selected.delete(String(member.path));
      }));
    }
    new import_obsidian2.Setting(this.contentEl).addButton((button) => button.setButtonText("Suggest consolidation").onClick(() => {
      if (selected.size < 2) {
        new import_obsidian2.Notice("Select at least two concepts.");
        return;
      }
      new ConfirmModal(
        this.app,
        "Prepare a consolidation suggestion",
        "The configured consolidation agent will compare the selected definitions and source notes. This uses inference and creates a pending review; it does not change the graph.",
        () => {
          this.close();
          void this.plugin.suggestConsolidation(void 0, [...selected]);
        }
      ).open();
    }));
  }
  onClose() {
    this.contentEl.empty();
  }
};
var ReviewPicker = class extends import_obsidian2.SuggestModal {
  constructor(app, history = false) {
    super(app);
    this.history = history;
    this.setPlaceholder(history ? "Find decided, superseded or resolved reviews" : "Find pending links, claim drafts, removals or revisions");
  }
  getSuggestions(query) {
    return this.app.vault.getMarkdownFiles().filter((file) => /^Reviews\/(Links|Removals|Revisions|Concepts)\//.test(file.path) && file.path.toLowerCase().includes(query.toLowerCase())).filter((file) => {
      const status = this.app.metadataCache.getFileCache(file)?.frontmatter?.status;
      const superseded = this.app.metadataCache.getFileCache(file)?.frontmatter?.superseded_by;
      const resolved = this.app.metadataCache.getFileCache(file)?.frontmatter?.resolved === "already_represented";
      return this.history ? status === "approved" || status === "rejected" || typeof superseded === "string" || resolved : (status === "pending" || status === "approved") && !superseded && !resolved;
    }).sort((a, b) => a.path.localeCompare(b.path));
  }
  getSuggestionText(file) {
    const status = this.app.metadataCache.getFileCache(file)?.frontmatter?.status;
    if (this.app.metadataCache.getFileCache(file)?.frontmatter?.resolved === "already_represented") return `${file.path} (already represented)`;
    if (this.app.metadataCache.getFileCache(file)?.frontmatter?.superseded_by) return `${file.path} (superseded)`;
    return `${file.path}${typeof status === "string" ? ` (${status})` : ""}`;
  }
  renderSuggestion(file, el) {
    el.setText(this.getSuggestionText(file));
  }
  onChooseSuggestion(file) {
    void this.app.workspace.getLeaf(false).openFile(file);
  }
};
var HistoryPicker = class extends import_obsidian2.SuggestModal {
  constructor(app, plugin, records) {
    super(app);
    this.plugin = plugin;
    this.records = records;
    this.setPlaceholder("Search archived review decisions");
  }
  getSuggestions(query) {
    return this.records.filter((record) => `${record.path} ${record.decision} ${record.kind}`.toLowerCase().includes(query.toLowerCase()));
  }
  getSuggestionText(record) {
    return `${record.path} (${record.decision}; ${record.outcome})`;
  }
  renderSuggestion(record, el) {
    el.setText(this.getSuggestionText(record));
  }
  onChooseSuggestion(record) {
    void this.plugin.openArchivedReview(String(record.id));
  }
};
var ResearchView = class extends import_obsidian2.ItemView {
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
    const labels2 = { up_to_date: "Up to date", completed_reviews_pending: "Completed, reviews pending", completed_with_limitations: "Completed with limitations", incomplete_resumable: "Incomplete, resumable", failed: "Failed" };
    el.createEl("p", { text: `Status: ${state.status === "failed" ? "Failed" : labels2[state.syncStatus ?? ""] ?? state.status}`, cls: `research-vault-status status-${state.status}` });
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
      ["Last successful sync", state.lastSuccess],
      ["Pending concept decisions", state.pending?.consolidation]
    ]) {
      stats.createEl("dt", { text: String(label) });
      stats.createEl("dd", { text: typeof value === "number" || typeof value === "string" ? redact(String(value)) : "Not reported" });
    }
    const controls = el.createDiv({ cls: "research-vault-controls" });
    if (state.resumeInstructions) el.createEl("p", { text: state.resumeInstructions });
    if (state.taskCounts) el.createEl("p", { text: `Tasks: ${Object.entries(state.taskCounts).map(([name, count]) => `${name}: ${count}`).join(" \xB7 ")}` });
    if (state.agentUsage && Object.keys(state.agentUsage).length) {
      el.createEl("h3", { text: "Inference usage by agent" });
      for (const [agent, usage] of Object.entries(state.agentUsage)) {
        if (isRecord(usage)) el.createEl("p", { text: `${agent}: ${usage.requests ?? 0} requests, ${usage.cache_hits ?? 0} cache hits, ${usage.failures ?? 0} failures, ${usage.deferred ?? 0} deferred. Tokens: ${JSON.stringify(usage.tokens ?? {})}` });
      }
    }
    for (const [action, label] of [["sync", "Sync"], ["dry", "Dry Run Sync"], ["apply", "Apply Reviews"], ["doctor", "Doctor"], ["status", "Refresh Status"]]) {
      const button = controls.createEl("button", { text: label });
      button.disabled = this.plugin.runner.busy;
      button.addEventListener("click", () => this.plugin.request(action));
    }
    const stop = controls.createEl("button", { text: "Interrupt Sync" });
    stop.disabled = !this.plugin.runner.canInterrupt;
    stop.addEventListener("click", () => {
      void this.interrupt();
    });
    controls.createEl("button", { text: "Open Review Queue" }).addEventListener("click", () => this.plugin.openReviews());
    controls.createEl("button", { text: "Review History" }).addEventListener("click", () => this.plugin.openReviews(true));
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
  async interrupt() {
    try {
      await this.plugin.runner.interrupt();
    } catch (error) {
      new import_obsidian2.Notice(`Cannot interrupt synchronization: ${redact(String(error))}`);
    }
  }
};
var ResearchVaultPlugin = class extends import_obsidian2.Plugin {
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
        else if (id === "review-history") this.openReviews(true);
        else if (id === "agent-settings") void this.openAgentSettings();
        else if (id === "review-readiness") void this.previewReviews();
        else if (id === "interrupt") void this.runner.interrupt().catch((error) => new import_obsidian2.Notice(`Cannot interrupt synchronization: ${redact(String(error))}`));
        else this.request(id);
      } });
    }
    if (this.settings.ribbon) this.addRibbonIcon("microscope", "Open Research Vault", () => {
      void this.openSidebar();
    });
    this.app.workspace.onLayoutReady(() => {
      void this.ready().catch((error) => {
        new import_obsidian2.Notice(`Research Vault startup: ${redact(String(error)).slice(0, 500)}`);
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
      new import_obsidian2.Notice("A Research Vault command is already running.");
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
  async previewReviews(proposalIds) {
    if (!proposalIds) {
      const report = await this.execute("readiness", true);
      if (report && this.runner.snapshot.status === "completed" && !this.runner.disposed) new ReviewReadinessModal(this.app, this, report).open();
      return;
    }
    const preview = await this.execute("preview", true, { proposalIds });
    if (!preview || this.runner.snapshot.status !== "completed" || this.runner.disposed) return;
    if (preview.can_apply !== true || typeof preview.plan_token !== "string" || !Array.isArray(preview.selected_ids) || preview.selected_ids.length !== proposalIds.length || preview.selected_ids.some((id) => typeof id !== "string" || !proposalIds.includes(id))) {
      new import_obsidian2.Notice("Selected reviews are no longer ready. Reopen Review Readiness.");
      return;
    }
    const readinessItems = isRecord(preview.readiness) && Array.isArray(preview.readiness.items) ? preview.readiness.items.filter(isRecord) : [];
    const details = redact(JSON.stringify({
      reviews: readinessItems.filter((item) => proposalIds.includes(String(item.id))).map((item) => item.path),
      changes: preview.changes,
      diffs: preview.diffs,
      removal_impacts: preview.impact_previews,
      includes_deletion: preview.destructive,
      deferred_approvals: Array.isArray(preview.skipped) ? preview.skipped.length : 0
    }, null, 2));
    if (details.length > 32e3) {
      new import_obsidian2.Notice("The approval preview is too large for this dialog. Review and apply using the terminal CLI.");
      return;
    }
    new ConfirmModal(
      this.app,
      "Apply reviewed decisions",
      "Review the selected note changes and removal impacts. Continue applies only these selected approvals. If the preview changes, review it again. Deletion requires current impact checks, a clean Git tree and a recoverable backup.",
      () => {
        void this.execute("apply", false, { proposalIds, planToken: String(preview.plan_token) });
      },
      details
    ).open();
  }
  async refreshReview(id) {
    const result = await this.execute("refresh", false, { id });
    if (result?.outcome === "concept_comparison_needed") {
      if (typeof result.message === "string") new import_obsidian2.Notice(result.message);
      await this.previewReviews();
      return;
    }
    if (result && (result.outcome === "already_represented" || result.outcome === "already_closed") && typeof result.message === "string") new import_obsidian2.Notice(result.message);
    const file = result && typeof result.path === "string" ? this.app.vault.getAbstractFileByPath(result.path) : void 0;
    if (file instanceof import_obsidian2.TFile) await this.app.workspace.getLeaf(false).openFile(file);
  }
  async suggestConsolidation(id, memberPaths) {
    const result = await this.execute("consolidate", false, memberPaths ? { memberPaths } : { id });
    const file = result && typeof result.path === "string" ? this.app.vault.getAbstractFileByPath(result.path) : void 0;
    if (file instanceof import_obsidian2.TFile) await this.app.workspace.getLeaf(false).openFile(file);
  }
  async execute(action, quiet = false, selection) {
    try {
      if (!(this.app.vault.adapter instanceof import_obsidian2.FileSystemAdapter)) throw new Error("Research Vault requires a local desktop filesystem vault.");
      const vault = this.app.vault.adapter.getBasePath();
      const settings = { ...this.settings };
      const result = await this.runner.run(vault, settings, process.platform, action, selection);
      if (action === "status" && result && this.runner.snapshot.status !== "failed") {
        statusDetails(this.runner.snapshot, result);
        if (isRecord(result.last_report)) this.runner.snapshot.status = outcome(0, result.last_report);
        else this.runner.snapshot.status = "idle";
      } else if (["sync", "full", "startup", "apply", "refresh", "consolidate"].includes(action) && !this.runner.disposed) {
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
        new import_obsidian2.Notice(`Research Vault ${action}: ${this.runner.snapshot.status}. See the sidebar for details.`);
      }
      return result;
    } catch (error) {
      if (!this.runner.disposed) {
        if (!this.runner.busy) this.runner.snapshot.diagnostics = [redact(String(error))];
        this.scheduleRender();
        new import_obsidian2.Notice(`Research Vault: ${redact(String(error)).slice(0, 500)}`);
      }
      return void 0;
    }
  }
  async openSidebar() {
    let leaf = this.app.workspace.getLeavesOfType(VIEW_TYPE)[0];
    if (!leaf) {
      const right = this.app.workspace.getRightLeaf(false);
      if (!right) {
        new import_obsidian2.Notice("Cannot open Research Vault sidebar.");
        return;
      }
      leaf = right;
      await leaf.setViewState({ type: VIEW_TYPE, active: true });
    }
    await this.app.workspace.revealLeaf(leaf);
  }
  async openDashboard() {
    const file = this.app.vault.getAbstractFileByPath("Home.md");
    if (file instanceof import_obsidian2.TFile) await this.app.workspace.getLeaf(false).openFile(file);
    else new import_obsidian2.Notice("Home.md is missing from this vault.");
  }
  async openReviews(history = false) {
    if (!history) {
      new ReviewPicker(this.app).open();
      return;
    }
    const result = await this.execute("history", true);
    if (result && Array.isArray(result.history) && !this.runner.disposed) new HistoryPicker(this.app, this, result.history.filter(isRecord)).open();
  }
  async openArchivedReview(id) {
    const result = await this.execute("history", true, { id });
    if (!result || typeof result.markdown !== "string" || this.runner.disposed) return;
    const modal = new import_obsidian2.Modal(this.app);
    modal.contentEl.createEl("h2", { text: "Archived review" });
    modal.contentEl.createEl("pre", { text: result.markdown, cls: "research-vault-diagnostic" });
    modal.contentEl.createEl("pre", { text: JSON.stringify(result.record, null, 2), cls: "research-vault-diagnostic" });
    modal.open();
  }
  async openAgentSettings() {
    const report = await this.execute("agentSettings", true);
    if (report && !this.runner.disposed) new AgentSettingsModal(this.app, report, (request) => this.saveAgentConfiguration(request), () => this.execute("agentSettings", true)).open();
  }
  async saveAgentConfiguration(request) {
    if (this.runner.busy) throw new Error("Wait for the current command before saving settings.");
    if (!(this.app.vault.adapter instanceof import_obsidian2.FileSystemAdapter)) throw new Error("A local filesystem vault is required.");
    const root = this.app.vault.adapter.getBasePath();
    const directory = import_node_path2.default.join(root, ".pipeline");
    await import_node_fs2.promises.mkdir(directory).catch((error) => {
      if (error.code !== "EEXIST") throw error;
    });
    const info = await import_node_fs2.promises.lstat(directory);
    if (info.isSymbolicLink() || !info.isDirectory()) throw new Error("Settings requests require a local pipeline directory.");
    const id = (0, import_node_crypto2.randomBytes)(16).toString("hex");
    const file = import_node_path2.default.join(directory, `settings-request-${id}.json`);
    await import_node_fs2.promises.writeFile(file, JSON.stringify(request), { flag: "wx" });
    try {
      const result = await this.execute("saveAgentSettings", false, { settingsRequestId: id });
      return result && this.runner.snapshot.status === "completed" ? result : void 0;
    } finally {
      await import_node_fs2.promises.unlink(file).catch(() => void 0);
    }
  }
  async saveSettings() {
    await this.saveData(this.settings);
  }
};
var ResearchSettingTab = class extends import_obsidian2.PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }
  display() {
    const el = this.containerEl;
    el.empty();
    el.createEl("h2", { text: "Research Vault" });
    const agents = el.createDiv();
    new import_obsidian2.Setting(agents).setName("Agent-specific models and reasoning").setDesc("Check CLI login status, then select provider, model and intensity for each agent here.").addButton((button) => button.setButtonText("Load agent settings").onClick(async () => {
      button.setDisabled(true);
      try {
        const report = await this.plugin.execute("agentSettings", true);
        if (report && !this.plugin.runner.disposed) new AgentSettingsEditor(agents, report, (request) => this.plugin.saveAgentConfiguration(request), () => this.plugin.execute("agentSettings", true));
      } finally {
        button.setDisabled(false);
      }
    }));
    el.createEl("p", { text: "Runs trusted local Python code. Zotero and LLM settings stay in config.yaml. Save settings before starting a command." });
    const settings = this.plugin.settings;
    new import_obsidian2.Setting(el).setName("Execution environment").setDesc("Windows defaults to WSL; Linux defaults to Native. The virtual environment must match the selected environment.").addDropdown((dropdown) => dropdown.addOption("native", "Native").addOption("wsl", "WSL").setValue(settings.mode).onChange(async (value) => {
      settings.mode = value === "wsl" ? "wsl" : "native";
      await this.plugin.saveSettings();
      this.display();
    }));
    for (const [key, name, description, placeholder] of [
      ["distribution", "WSL distribution", "Blank uses your default distribution. Used only in WSL mode.", "Default distribution"],
      ["python", "Python executable path", "Blank uses the project virtual environment. A path only, without arguments; in WSL use a Linux path.", defaultPython(settings, process.platform)],
      ["script", "Custom CLI script path", "Blank uses scripts/research.py. Custom scripts must support the same CLI, including --vault.", "scripts/research.py"]
    ]) {
      new import_obsidian2.Setting(el).setName(name).setDesc(description).addText((text) => text.setPlaceholder(placeholder).setValue(settings[key]).onChange(async (value) => {
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
      new import_obsidian2.Setting(el).setName(name).setDesc(description).addToggle((toggle) => toggle.setValue(settings[key]).onChange(async (value) => {
        settings[key] = value;
        await this.plugin.saveSettings();
      }));
    }
    new import_obsidian2.Setting(el).setName("Maximum displayed log lines").setDesc("20\u20132000 lines. Long lines and final reports are also bounded; full pipeline reports remain on disk.").addText((text) => text.setValue(String(settings.maxLogLines)).onChange(async (value) => {
      const max = Number(value);
      if (Number.isInteger(max) && max >= 20 && max <= 2e3) {
        settings.maxLogLines = max;
        await this.plugin.saveSettings();
      }
    }));
    new import_obsidian2.Setting(el).setName("Research Vault sidebar").addButton((button) => button.setButtonText("Open sidebar").onClick(() => {
      void this.plugin.openSidebar();
    }));
  }
};
