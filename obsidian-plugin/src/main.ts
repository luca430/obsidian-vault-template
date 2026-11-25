import {
  App, FileSystemAdapter, ItemView, Modal, Notice, Plugin, PluginSettingTab,
  Setting, SuggestModal, TFile, WorkspaceLeaf,
} from 'obsidian';
import { Action, Runner } from './execution';
import { RecordValue, isRecord, outcome, redact, statusDetails } from './protocol';
import { ResearchSettings, defaultPython, loadSettings } from './settings';

export const VIEW_TYPE = 'research-vault-sidebar';
export const COMMANDS = [
  ['sync', 'Research: Sync'], ['full', 'Research: Full Sync'],
  ['dry', 'Research: Dry Run'], ['apply', 'Research: Apply Reviews'],
  ['status', 'Research: Status'], ['doctor', 'Research: Doctor'],
  ['dashboard', 'Research: Open Dashboard'], ['reviews', 'Research: Open Review Queue'],
] as const;

class ConfirmModal extends Modal {
  constructor(app: App, private title: string, private message: string, private proceed: () => void, private details?: string) { super(app); }
  onOpen(): void {
    this.contentEl.createEl('h2', { text: this.title });
    this.contentEl.createEl('p', { text: this.message });
    if (this.details) this.contentEl.createEl('pre', { text: this.details, cls: 'research-vault-diagnostic' });
    new Setting(this.contentEl)
      .addButton(button => button.setButtonText('Cancel').onClick(() => this.close()))
      .addButton(button => button.setButtonText('Continue').setWarning().onClick(() => {
        this.close(); this.proceed();
      }));
  }
  onClose(): void { this.contentEl.empty(); }
}

class ReviewPicker extends SuggestModal<TFile> {
  constructor(app: App) { super(app); this.setPlaceholder('Find links, claim drafts, removals or revisions'); }
  getSuggestions(query: string): TFile[] {
    return this.app.vault.getMarkdownFiles()
      .filter(file => /^Reviews\/(Links|Removals|Revisions)\//.test(file.path) && file.path.toLowerCase().includes(query.toLowerCase()))
      .sort((a, b) => a.path.localeCompare(b.path));
  }
  getSuggestionText(file: TFile): string {
    const status: unknown = this.app.metadataCache.getFileCache(file)?.frontmatter?.status;
    return `${file.path}${typeof status === 'string' ? ` (${status})` : ''}`;
  }
  renderSuggestion(file: TFile, el: HTMLElement): void { el.setText(this.getSuggestionText(file)); }
  onChooseSuggestion(file: TFile): void { void this.app.workspace.getLeaf(false).openFile(file); }
}

export class ResearchView extends ItemView {
  constructor(leaf: WorkspaceLeaf, private plugin: ResearchVaultPlugin) { super(leaf); }
  getViewType(): string { return VIEW_TYPE; }
  getDisplayText(): string { return 'Research Vault'; }
  getIcon(): string { return 'microscope'; }
  async onOpen(): Promise<void> { this.render(); }
  render(): void {
    const el = this.contentEl;
    // Preserve log scroll position during throttled progress redraws.
    const previous = el.querySelector('.research-vault-logs');
    const scroll = previous?.scrollTop ?? 0;
    const follow = !previous || previous.scrollHeight - previous.scrollTop - previous.clientHeight < 30;
    el.empty(); el.addClass('research-vault-view');
    el.createEl('h2', { text: 'Research Vault' });
    const state = this.plugin.runner.snapshot;
    el.createEl('p', { text: `Status: ${state.status}`, cls: `research-vault-status status-${state.status}` });
    el.createEl('p', { text: `Stage: ${redact(state.stage)}` });
    const stats = el.createEl('dl', { cls: 'research-vault-stats' });
    for (const [label, value] of [
      ['Documents processed', state.processed], ['Documents remaining', state.remaining],
      ['Failed documents', state.failed], ['Skipped documents', state.skipped],
      ['Cache hits', state.cacheHits], ['Pending conceptual links', state.pending?.link],
      ['Pending claim drafts', state.pending?.claim], ['Pending removals', state.pending?.removal],
      ['Pending revisions', state.pending?.revision], ['Last successful sync', state.lastSuccess],
    ]) {
      stats.createEl('dt', { text: String(label) });
      stats.createEl('dd', { text: typeof value === 'number' || typeof value === 'string' ? redact(String(value)) : 'Not reported' });
    }
    const controls = el.createDiv({ cls: 'research-vault-controls' });
    for (const [action, label] of [['sync', 'Sync'], ['apply', 'Apply Reviews'], ['doctor', 'Doctor'], ['status', 'Refresh Status']] as const) {
      const button = controls.createEl('button', { text: label });
      button.disabled = this.plugin.runner.busy;
      button.addEventListener('click', () => this.plugin.request(action));
    }
    controls.createEl('button', { text: 'Open Review Queue' }).addEventListener('click', () => this.plugin.openReviews());
    controls.createEl('button', { text: 'Open Dashboard' }).addEventListener('click', () => { void this.plugin.openDashboard(); });
    el.createEl('h3', { text: 'Warnings and errors' });
    if (!state.diagnostics.length) el.createEl('p', { text: 'No diagnostics reported for this command.' });
    for (const diagnostic of state.diagnostics) el.createEl('pre', { text: diagnostic, cls: 'research-vault-diagnostic' });
    el.createEl('h3', { text: 'Recent output' });
    const logs = el.createEl('pre', { text: state.logs.join('\n'), cls: 'research-vault-logs' });
    logs.scrollTop = follow ? logs.scrollHeight : scroll;
    el.createEl('p', { text: 'Full reports: .pipeline/logs. Review decisions are edited in review notes and validated by Python.', cls: 'setting-item-description' });
  }
}

export default class ResearchVaultPlugin extends Plugin {
  settings!: ResearchSettings;
  runner = new Runner(() => this.scheduleRender());
  private renderTimer?: ReturnType<typeof setTimeout>;
  private layoutHandled = false;

  async onload(): Promise<void> {
    this.settings = loadSettings(await this.loadData(), process.platform);
    this.registerView(VIEW_TYPE, leaf => new ResearchView(leaf, this));
    this.addSettingTab(new ResearchSettingTab(this.app, this));
    for (const [id, name] of COMMANDS) {
      this.addCommand({ id, name, callback: () => {
        if (id === 'dashboard') void this.openDashboard();
        else if (id === 'reviews') this.openReviews();
        else this.request(id);
      } });
    }
    if (this.settings.ribbon) this.addRibbonIcon('microscope', 'Open Research Vault', () => { void this.openSidebar(); });
    this.app.workspace.onLayoutReady(() => { void this.ready().catch(error => {
      new Notice(`Research Vault startup: ${redact(String(error)).slice(0, 500)}`);
    }); });
  }

  async ready(): Promise<void> {
    if (this.runner.disposed || this.layoutHandled) return;
    this.layoutHandled = true;
    if (this.settings.openSidebar) await this.openSidebar();
    if (this.runner.disposed || this.runner.busy) return;
    await this.execute(this.settings.startupSync ? 'startup' : 'status', true);
  }

  onunload(): void {
    this.runner.disposed = true;
    if (this.renderTimer) clearTimeout(this.renderTimer);
    // Do not kill Python mid-transaction or leave only a killed WSL launcher.
    // An already launched finite run finishes under Python's lock and checkpoints.
  }

  private scheduleRender(): void {
    if (this.runner.disposed || this.renderTimer) return;
    this.renderTimer = setTimeout(() => {
      this.renderTimer = undefined;
      for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE)) {
        if (leaf.view instanceof ResearchView) leaf.view.render();
      }
    }, 100);
  }

  request(action: Action): void {
    if (this.runner.busy) { new Notice('A Research Vault command is already running.'); return; }
    if (action === 'apply') { void this.previewReviews(); return; }
    if (action === 'full') {
      new ConfirmModal(this.app, 'Full synchronization',
        'This reprocesses the collection and can involve substantial LLM processing and usage costs. Continue?',
        () => { void this.execute(action); }).open();
    } else void this.execute(action);
  }

  async previewReviews(): Promise<void> {
    const preview = await this.execute('preview', true);
    if (!preview || this.runner.snapshot.status !== 'completed' || this.runner.disposed) return;
    const details = redact(JSON.stringify(preview, null, 2));
    if (details.length > 32000) {
      new Notice('The approval preview is too large for this dialog. Review and apply using the terminal CLI.');
      return;
    }
    new ConfirmModal(this.app, 'Apply reviewed decisions',
      'Python preview of explicitly approved decisions, including any approved/delete removals. Continue only after reviewing these exact paths and impacts. Python revalidates approvals and requires a clean Git tree and a verified backup for deletion.',
      () => { void this.execute('apply'); }, details).open();
  }

  async execute(action: Action, quiet = false): Promise<RecordValue | undefined> {
    try {
      if (!(this.app.vault.adapter instanceof FileSystemAdapter)) throw new Error('Research Vault requires a local desktop filesystem vault.');
      const vault = this.app.vault.adapter.getBasePath();
      const settings = { ...this.settings };
      const result = await this.runner.run(vault, settings, process.platform, action);
      if (action === 'status' && result && this.runner.snapshot.status !== 'failed') {
        statusDetails(this.runner.snapshot, result);
        if (isRecord(result.last_report)) this.runner.snapshot.status = outcome(0, result.last_report);
        else this.runner.snapshot.status = 'idle';
      } else if (['sync', 'full', 'startup', 'apply'].includes(action) && !this.runner.disposed) {
        // Ask Python for authoritative pending counts and last_success; never read
        // or interpret state.json/review approval rules in TypeScript.
        const finished = { ...this.runner.snapshot };
        try {
          const status = await this.runner.run(vault, settings, process.platform, 'status');
          if (status && this.runner.snapshot.status !== 'failed') {
            const stage = finished.stage;
            statusDetails(finished, status);
            if (finished.status === 'failed') finished.stage = stage;
          } else this.runner.log('Status refresh failed; see recent output.', settings.maxLogLines);
          // A status query must not mask a failed/incomplete synchronization.
          this.runner.snapshot = { ...finished, logs: this.runner.snapshot.logs };
        } catch (error) {
          this.runner.snapshot = { ...finished, logs: this.runner.snapshot.logs };
          this.runner.log(`Could not refresh status: ${String(error)}`, this.settings.maxLogLines);
        }
      }
      this.scheduleRender();
      if (!this.runner.disposed && (this.runner.snapshot.status === 'failed' || (this.settings.notifications && (!quiet || action === 'startup')))) {
        new Notice(`Research Vault ${action}: ${this.runner.snapshot.status}. See the sidebar for details.`);
      }
      return result;
    } catch (error) {
      if (!this.runner.disposed) {
        if (!this.runner.busy) this.runner.snapshot.diagnostics = [redact(String(error))];
        this.scheduleRender();
        new Notice(`Research Vault: ${redact(String(error)).slice(0, 500)}`);
      }
      return undefined;
    }
  }

  async openSidebar(): Promise<void> {
    let leaf = this.app.workspace.getLeavesOfType(VIEW_TYPE)[0];
    if (!leaf) {
      const right = this.app.workspace.getRightLeaf(false);
      if (!right) { new Notice('Cannot open Research Vault sidebar.'); return; }
      leaf = right;
      await leaf.setViewState({ type: VIEW_TYPE, active: true });
    }
    await this.app.workspace.revealLeaf(leaf);
  }

  async openDashboard(): Promise<void> {
    const file = this.app.vault.getAbstractFileByPath('Home.md');
    if (file instanceof TFile) await this.app.workspace.getLeaf(false).openFile(file);
    else new Notice('Home.md is missing from this vault.');
  }
  openReviews(): void { new ReviewPicker(this.app).open(); }
  async saveSettings(): Promise<void> { await this.saveData(this.settings); }
}

class ResearchSettingTab extends PluginSettingTab {
  constructor(app: App, private plugin: ResearchVaultPlugin) { super(app, plugin); }
  display(): void {
    const el = this.containerEl; el.empty();
    el.createEl('h2', { text: 'Research Vault' });
    el.createEl('p', { text: 'Runs trusted local Python code. Zotero and LLM settings stay in config.yaml. Save settings before starting a command.' });
    const settings = this.plugin.settings;
    new Setting(el).setName('Execution environment').setDesc('Windows defaults to WSL; Linux defaults to Native. The virtual environment must match the selected environment.')
      .addDropdown(dropdown => dropdown.addOption('native', 'Native').addOption('wsl', 'WSL')
        .setValue(settings.mode).onChange(async value => {
          settings.mode = value === 'wsl' ? 'wsl' : 'native';
          await this.plugin.saveSettings(); this.display();
        }));
    for (const [key, name, description, placeholder] of [
      ['distribution', 'WSL distribution', 'Blank uses your default distribution. Used only in WSL mode.', 'Default distribution'],
      ['python', 'Python executable path', 'Blank uses the project virtual environment. A path only, without arguments; in WSL use a Linux path.', defaultPython(settings, process.platform)],
      ['script', 'Custom CLI script path', 'Blank uses scripts/research.py. Custom scripts must support the same CLI, including --vault.', 'scripts/research.py'],
    ] as const) {
      new Setting(el).setName(name).setDesc(description).addText(text => text.setPlaceholder(placeholder).setValue(settings[key]).onChange(async value => {
        settings[key] = value; await this.plugin.saveSettings();
      }));
    }
    for (const [key, name, description] of [
      ['startupSync', 'Synchronize at startup', 'Opt in to a bounded incremental sync once the workspace is ready. Never runs a full sync.'],
      ['openSidebar', 'Open sidebar automatically', 'Open the Research Vault sidebar after the workspace is ready.'],
      ['notifications', 'Show completion notifications', 'Errors are always reported.'],
      ['ribbon', 'Show ribbon icon', 'Reload this plugin to apply this setting.'],
    ] as const) {
      new Setting(el).setName(name).setDesc(description).addToggle(toggle => toggle.setValue(settings[key]).onChange(async value => {
        settings[key] = value; await this.plugin.saveSettings();
      }));
    }
    new Setting(el).setName('Maximum displayed log lines').setDesc('20–2000 lines. Long lines and final reports are also bounded; full pipeline reports remain on disk.')
      .addText(text => text.setValue(String(settings.maxLogLines)).onChange(async value => {
        const max = Number(value);
        if (Number.isInteger(max) && max >= 20 && max <= 2000) {
          settings.maxLogLines = max; await this.plugin.saveSettings();
        }
      }));
    new Setting(el).setName('Research Vault sidebar').addButton(button => button.setButtonText('Open sidebar').onClick(() => { void this.plugin.openSidebar(); }));
  }
}
