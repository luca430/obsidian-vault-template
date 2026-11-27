import { App, Modal, Notice, Setting } from 'obsidian';
import { RecordValue, isRecord } from './protocol';

const roles = ['literature', 'manuscript', 'concepts', 'linking', 'synthesis', 'maintenance', 'consolidation'];
const labels: Record<string, string> = { codex: 'Codex', claude: 'Claude Code' };
const providerDefault = '__provider_default__';
type Save = (request: RecordValue) => Promise<RecordValue | boolean | undefined>;
type Refresh = () => Promise<RecordValue | undefined>;

export class AgentSettingsEditor {
  private request!: RecordValue;
  constructor(private el: HTMLElement, private report: RecordValue, private save: Save, private refresh?: Refresh, private saved?: () => void) {
    this.load(report);
    this.render();
  }
  private load(report: RecordValue): void {
    this.report = report;
    this.request = JSON.parse(JSON.stringify({ plan_token: report.plan_token, defaults: report.defaults ?? {}, agents: report.agents ?? {}, default_provider: report.default_provider ?? 'codex' })) as RecordValue;
  }
  private providers(): RecordValue { return isRecord(this.report.providers) ? this.report.providers : {}; }
  private authenticated(provider: string): boolean { const status = this.providers()[provider]; return isRecord(status) && status.authenticated === true; }
  private profile(role: string): RecordValue {
    const value = (this.request.agents as RecordValue)[role];
    return isRecord(value) ? value : typeof value === 'string' ? { provider: value } : {};
  }
  private store(role: string, profile: RecordValue): void {
    const agents = this.request.agents as RecordValue;
    if (role === 'consolidation' && !(role in agents) && Object.keys(profile).length) {
      const inherited = this.resolved('linking');
      const merged = { ...inherited, ...profile, retry_policy: { ...(isRecord(inherited.retry_policy) ? inherited.retry_policy : {}), ...(isRecord(profile.retry_policy) ? profile.retry_policy : {}) } };
      Object.assign(profile, JSON.parse(JSON.stringify(merged)) as RecordValue);
    }
    if (Object.keys(profile).length) agents[role] = profile; else delete agents[role];
  }
  private resolved(role?: string): RecordValue {
    const profile = role ? this.profile(role) : {};
    if (role === 'consolidation' && !(role in (this.request.agents as RecordValue))) return this.resolved('linking');
    const provider = String(profile.provider ?? this.request.default_provider);
    const defaults = isRecord(this.report.provider_defaults) && isRecord(this.report.provider_defaults[provider]) ? this.report.provider_defaults[provider] : {};
    return { ...defaults, ...(this.request.defaults as RecordValue), ...profile, provider };
  }
  private models(provider: string): RecordValue[] {
    const status = this.providers()[provider];
    return isRecord(status) && Array.isArray(status.models) ? status.models.filter(isRecord) : [];
  }
  render(): void {
    const el = this.el; el.empty();
    el.createEl('h3', { text: 'Agent-specific models and reasoning' });
    for (const [provider, label] of Object.entries(labels)) {
      const status = this.providers()[provider];
      el.createEl('p', { text: `${label}: ${isRecord(status) ? String(status.message) : 'Login has not been checked.'}${this.authenticated(provider) ? '' : ` Sign in with ${provider === 'codex' ? 'codex login' : 'claude auth login'}, then refresh.`}` });
    }
    if (this.refresh) new Setting(el).setName('Provider login status and model lists').setDesc('Refresh preserves staged choices and sends no inference requests.').addButton(button => button.setButtonText('Refresh login and models').onClick(async () => {
      button.setDisabled(true);
      try {
        const report = await this.refresh!();
        if (report) {
          this.report = { ...this.report, providers: report.providers };
          if (report.plan_token !== this.request.plan_token) new Notice('Configuration changed externally. Reopen settings before saving; staged choices are retained here.');
          this.render();
        }
      } finally { button.setDisabled(false); }
    }));
    new Setting(el).setName('Default provider').setDesc('Changing provider resets affected model and intensity selections to provider defaults. Choose replacements before saving.').addDropdown(control => {
      this.providerOptions(control);
      control.setValue(String(this.request.default_provider)).onChange(value => {
        if (!this.authenticated(value)) return;
        this.request.default_provider = value;
        // A provider switch explicitly returns global model/effort to CLI defaults.
        const defaults = this.request.defaults as RecordValue;
        defaults.model = null; defaults.reasoning_effort = null;
        if (value === 'codex') delete defaults.max_output_tokens;
        for (const entry of Object.values(this.request.agents as RecordValue)) {
          if (isRecord(entry) && !entry.provider) {
            entry.model = null; entry.reasoning_effort = null;
            if (value === 'codex') delete entry.max_output_tokens;
          }
        }
        this.render();
      });
    });
    const defaultsSection = el.createEl('details'); defaultsSection.createEl('summary', { text: 'Global model and advanced defaults' });
    this.modelFields(defaultsSection, this.request.defaults as RecordValue, this.resolved(), () => this.render());
    this.advanced(defaultsSection, this.request.defaults as RecordValue, this.resolved());
    for (const role of roles) {
      const section = el.createDiv({ cls: 'research-vault-agent-settings' });
      section.createEl('h4', { text: role });
      const profile = this.profile(role);
      const resolved = this.resolved(role);
      new Setting(section).setName('Provider').setDesc(`${role === 'consolidation' ? 'Inherits linking unless this agent has overrides.' : 'Inherits the default provider unless overridden.'} Changing provider resets its model and intensity to provider defaults.`).addDropdown(control => {
        control.addOption('', 'Inherit'); this.providerOptions(control);
        control.setValue(String(profile.provider ?? '')).onChange(value => {
          if (value && !this.authenticated(value)) return;
          if (value) profile.provider = value; else delete profile.provider;
          // Discard the previous provider's model only after an explicit user switch.
          profile.model = null; profile.reasoning_effort = null;
          if (String(profile.provider ?? this.request.default_provider) === 'codex') delete profile.max_output_tokens;
          this.store(role, profile); this.render();
        });
      });
      this.modelFields(section, profile, resolved, () => { this.store(role, profile); this.render(); });
      const advanced = section.createEl('details'); advanced.createEl('summary', { text: 'Limits, timeout and retries' });
      this.advanced(advanced, profile, resolved, () => this.store(role, profile));
    }
    el.createEl('p', { text: 'Login is checked in the selected execution environment. Model access is verified on actual requests. Codex choices use local model metadata; Claude aliases do not certify account access. Configured model catalogs take precedence. Changes apply after Save to future tasks; completed scientific notes are retained.' });
    new Setting(el).addButton(button => button.setButtonText('Save agent settings').setCta().onClick(async () => {
      button.setDisabled(true);
      try {
        const result = await this.save(this.request);
        if (result) {
          if (isRecord(result)) { this.load(result); this.render(); }
          new Notice('Agent settings saved.'); this.saved?.();
        }
      } catch (error) { new Notice(String(error)); }
      finally { button.setDisabled(false); }
    }));
  }
  private providerOptions(control: { addOption(value: string, label: string): unknown; selectEl: HTMLSelectElement }): void {
    for (const [provider, label] of Object.entries(labels)) {
      control.addOption(provider, this.authenticated(provider) ? label : `${label} (login required)`);
      const option = Array.from(control.selectEl.options).find(item => item.value === provider);
      if (option) option.disabled = !this.authenticated(provider);
    }
    // Existing unavailable selections remain visible; no replacement is chosen.
  }
  private modelFields(el: HTMLElement, profile: RecordValue, resolved: RecordValue, changed: () => void): void {
    const provider = String(resolved.provider);
    const enabled = this.authenticated(provider);
    const models = this.models(provider);
    const model = models.find(row => row.id === resolved.model);
    const selected = profile.model === null ? providerDefault : String(profile.model ?? '');
    new Setting(el).setName('Model').setDesc(`Models for ${labels[provider] ?? provider}. Inherited: ${resolved.model ?? 'provider default'}.`).addDropdown(control => {
      control.addOption('', 'Inherit').addOption(providerDefault, 'Provider default');
      for (const row of models) control.addOption(String(row.id), `${row.label ?? row.id} (${row.id})`);
      if (selected && selected !== providerDefault && !models.some(row => row.id === selected)) control.addOption(selected, `${selected} (saved; not in current list)`);
      control.setValue(selected).setDisabled(!enabled).onChange(value => {
        if (!enabled) return;
        if (value === providerDefault) profile.model = null;
        else if (value) profile.model = value; else delete profile.model;
        changed();
      });
    });
    const supported = model && Array.isArray(model.reasoning_efforts) ? model.reasoning_efforts.filter((v): v is string => typeof v === 'string') : [];
    const intensity = profile.reasoning_effort === null ? providerDefault : String(profile.reasoning_effort ?? '');
    new Setting(el).setName('Reasoning intensity').setDesc(model ? `Options for this model from ${model.source}.` : 'Select a model to choose its supported intensity. Inherit/provider default leave the choice to configuration or the CLI.').addDropdown(control => {
      control.addOption('', 'Inherit').addOption(providerDefault, 'Provider default');
      for (const effort of supported) control.addOption(effort, effort);
      if (intensity && intensity !== providerDefault && !supported.includes(intensity)) {
        control.addOption(intensity, `${intensity} (saved; support unverified)`);
        const option = Array.from(control.selectEl.options).find(item => item.value === intensity);
        if (option) option.disabled = true;
      }
      control.setValue(intensity).setDisabled(!enabled).onChange(value => {
        if (!enabled) return;
        if (value === providerDefault) profile.reasoning_effort = null;
        else if (value && supported.includes(value)) profile.reasoning_effort = value;
        else if (!value) delete profile.reasoning_effort;
        changed();
      });
    });
  }
  private advanced(el: HTMLElement, profile: RecordValue, inherited: RecordValue, changed: () => void = () => {}): void {
    for (const [key, label] of [['max_input_chars', 'Input character limit'], ['output_reserve_chars', 'Output character reserve'], ['max_output_tokens', 'Native output-token cap (Claude only)'], ['timeout_seconds', 'Timeout in seconds']] as const) {
      if (key === 'max_output_tokens' && inherited.provider !== 'claude') continue;
      new Setting(el).setName(label).setDesc('Blank inherits.').addText(control => control.setValue(String(profile[key] ?? '')).setPlaceholder(String(inherited[key] ?? 'Inherit')).onChange(value => { if (value.trim()) profile[key] = Number(value); else delete profile[key]; changed(); }));
    }
    const policy = isRecord(profile.retry_policy) ? profile.retry_policy : {};
    for (const [key, label] of [['max_attempts', 'Maximum attempts'], ['retry_after_seconds', 'Rate-limit retry delay (seconds)']] as const) {
      new Setting(el).setName(label).setDesc('Retries occur on the next eligible sync.').addText(control => control.setValue(String(policy[key] ?? '')).onChange(value => {
        if (value.trim()) policy[key] = Number(value); else delete policy[key];
        if (Object.keys(policy).length) profile.retry_policy = policy; else delete profile.retry_policy;
        changed();
      }));
    }
  }
}

export class AgentSettingsModal extends Modal {
  constructor(app: App, private report: RecordValue, private save: Save, private refresh?: Refresh) { super(app); }
  onOpen(): void { new AgentSettingsEditor(this.contentEl, this.report, this.save, this.refresh, () => this.close()); }
  onClose(): void { this.contentEl.empty(); }
}
