import {
  App, FileSystemAdapter, ItemView, Modal, Notice, Plugin, PluginSettingTab,
  Setting, SuggestModal, TFile, WorkspaceLeaf,
} from 'obsidian';
import { Action, ReviewSelection, Runner } from './execution';
import { RecordValue, isRecord, outcome, redact, statusDetails } from './protocol';
import { ResearchSettings, defaultPython, loadSettings } from './settings';

export const VIEW_TYPE = 'research-vault-sidebar';
export const COMMANDS = [
  ['sync', 'Research: Sync'], ['full', 'Research: Full Sync'],
  ['dry', 'Research: Dry Run'], ['apply', 'Research: Apply Reviews'],
  ['status', 'Research: Status'], ['doctor', 'Research: Doctor'],
  ['dashboard', 'Research: Open Dashboard'], ['reviews', 'Research: Open Review Queue'],
  ['review-history', 'Research: Open Review History'],
  ['review-readiness', 'Research: Review Readiness'],
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

class ReviewReadinessModal extends Modal {
  constructor(app: App, private plugin: ResearchVaultPlugin, private report: RecordValue) { super(app); }
  onOpen(): void {
    const el = this.contentEl;
    el.createEl('h2', { text: 'Review readiness' });
    el.createEl('p', { text: 'Select ready approvals to preview. Blocked reviews stay unchanged. Refresh creates a new pending review without running Sync.' });
    const selected = new Set<string>();
    const priority = (item: RecordValue) => item.state === 'ready' ? 0 : item.state === 'needs-review' ? 2 : 1;
    const items = Array.isArray(this.report.items) ? this.report.items.filter(isRecord).sort((a, b) => priority(a) - priority(b)) : [];
    for (const item of items) {
      const row = el.createDiv();
      row.createEl('p', { text: `${item.state}: ${item.path}` });
      row.createEl('p', { text: String(item.reason ?? '') });
      const controls = new Setting(row);
      if (item.state === 'ready' && typeof item.id === 'string') {
        selected.add(item.id);
        controls.addToggle(toggle => toggle.setValue(true).onChange(value => {
          if (value) selected.add(String(item.id)); else selected.delete(String(item.id));
        }));
      }
      controls.addButton(button => button.setButtonText('Open review').onClick(() => {
        const file = this.app.vault.getAbstractFileByPath(String(item.path));
        if (file instanceof TFile) void this.app.workspace.getLeaf(false).openFile(file);
      }));
      if (item.state === 'needs-revalidation' && item.refreshable === true && typeof item.id === 'string') {
        controls.addButton(button => button.setButtonText('Refresh review').onClick(() => {
          this.close(); void this.plugin.refreshReview(String(item.id));
        }));
      }
      if (Array.isArray(item.differences)) {
        for (const difference of item.differences.filter(isRecord)) {
          const text = String(difference.diff ?? difference.reason ?? '');
          row.createEl('pre', { text: `${difference.note}\n${text.slice(0, 16000)}${text.length > 16000 ? '\nDisplay truncated; use review status in the CLI for the full diff.' : ''}`, cls: 'research-vault-diagnostic' });
        }
      }
    }
    const groups = Array.isArray(this.report.concept_candidates) ? this.report.concept_candidates.filter(isRecord) : [];
    for (const group of groups) {
      const row = el.createDiv();
      row.createEl('h3', { text: 'Possible concept consolidation' });
      row.createEl('p', { text: String(group.reason ?? '') });
      if (Array.isArray(group.members)) for (const member of group.members.filter(isRecord)) {
        row.createEl('p', { text: String(member.path) });
      }
      new Setting(row).addButton(button => button.setButtonText('Suggest consolidation').onClick(() => {
        new ConfirmModal(this.app, 'Prepare a consolidation suggestion',
          'The configured consolidation agent will compare these candidates and propose merging, keeping them separate, or relating them. This uses inference and creates a pending review for your approval.',
          () => { this.close(); void this.plugin.suggestConsolidation(String(group.id)); }).open();
      }));
    }
    if (Array.isArray(this.report.concept_catalog)) {
      new Setting(el).addButton(button => button.setButtonText('Compare other concepts').onClick(() => {
        new ConceptComparisonModal(this.app, this.plugin, (this.report.concept_catalog as unknown[]).filter(isRecord)).open();
      }));
    }
    new Setting(el).addButton(button => button.setButtonText('Preview selected').onClick(() => {
      if (!selected.size) { new Notice('Select at least one ready approval.'); return; }
      this.close(); void this.plugin.previewReviews([...selected]);
    }));
  }
  onClose(): void { this.contentEl.empty(); }
}

class ConceptComparisonModal extends Modal {
  constructor(app: App, private plugin: ResearchVaultPlugin, private catalog: RecordValue[]) { super(app); }
  onOpen(): void {
    const selected = new Set<string>();
    this.contentEl.createEl('h2', { text: 'Compare existing or proposed concepts' });
    this.contentEl.createEl('p', { text: 'Choose at least two concepts. This also supports differently named concepts that the name-based candidate search did not find.' });
    for (const member of this.catalog) {
      const row = this.contentEl.createDiv();
      row.createEl('p', { text: String(member.path) });
      new Setting(row).addToggle(toggle => toggle.setValue(false).onChange(value => {
        if (value) selected.add(String(member.path)); else selected.delete(String(member.path));
      }));
    }
    new Setting(this.contentEl).addButton(button => button.setButtonText('Suggest consolidation').onClick(() => {
      if (selected.size < 2) { new Notice('Select at least two concepts.'); return; }
      new ConfirmModal(this.app, 'Prepare a consolidation suggestion',
        'The configured consolidation agent will compare the selected definitions and source notes. This uses inference and creates a pending review; it does not change the graph.',
        () => { this.close(); void this.plugin.suggestConsolidation(undefined, [...selected]); }).open();
    }));
  }
  onClose(): void { this.contentEl.empty(); }
}

class ReviewPicker extends SuggestModal<TFile> {
  constructor(app: App, private history = false) {
    super(app);
    this.setPlaceholder(history ? 'Find decided, superseded or resolved reviews' : 'Find pending links, claim drafts, removals or revisions');
  }
  getSuggestions(query: string): TFile[] {
    return this.app.vault.getMarkdownFiles()
      .filter(file => /^Reviews\/(Links|Removals|Revisions|Concepts)\//.test(file.path) && file.path.toLowerCase().includes(query.toLowerCase()))
      .filter(file => {
        const status: unknown = this.app.metadataCache.getFileCache(file)?.frontmatter?.status;
        const superseded: unknown = this.app.metadataCache.getFileCache(file)?.frontmatter?.superseded_by;
        const resolved = this.app.metadataCache.getFileCache(file)?.frontmatter?.resolved === 'already_represented';
        return this.history ? status === 'approved' || status === 'rejected' || typeof superseded === 'string' || resolved : status === 'pending' && !superseded && !resolved;
      })
      .sort((a, b) => a.path.localeCompare(b.path));
  }
  getSuggestionText(file: TFile): string {
    const status: unknown = this.app.metadataCache.getFileCache(file)?.frontmatter?.status;
    if (this.app.metadataCache.getFileCache(file)?.frontmatter?.resolved === 'already_represented') return `${file.path} (already represented)`;
    if (this.app.metadataCache.getFileCache(file)?.frontmatter?.superseded_by) return `${file.path} (superseded)`;
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
      ['Pending concept decisions', state.pending?.consolidation],
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
    controls.createEl('button', { text: 'Review History' }).addEventListener('click', () => this.plugin.openReviews(true));
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
        else if (id === 'review-history') this.openReviews(true);
        else if (id === 'review-readiness') void this.previewReviews();
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

  async previewReviews(proposalIds?: string[]): Promise<void> {
    if (!proposalIds) {
      const report = await this.execute('readiness', true);
      if (report && this.runner.snapshot.status === 'completed' && !this.runner.disposed) new ReviewReadinessModal(this.app, this, report).open();
      return;
    }
    const preview = await this.execute('preview', true, { proposalIds });
    if (!preview || this.runner.snapshot.status !== 'completed' || this.runner.disposed) return;
    if (preview.can_apply !== true || typeof preview.plan_token !== 'string' || !Array.isArray(preview.selected_ids) || preview.selected_ids.length !== proposalIds.length || preview.selected_ids.some(id => typeof id !== 'string' || !proposalIds.includes(id))) {
      new Notice('Selected reviews are no longer ready. Reopen Review Readiness.'); return;
    }
    const readinessItems = isRecord(preview.readiness) && Array.isArray(preview.readiness.items) ? preview.readiness.items.filter(isRecord) : [];
    const details = redact(JSON.stringify({
      reviews: readinessItems.filter(item => proposalIds.includes(String(item.id))).map(item => item.path),
      changes: preview.changes, diffs: preview.diffs, removal_impacts: preview.impact_previews,
      includes_deletion: preview.destructive,
      deferred_approvals: Array.isArray(preview.skipped) ? preview.skipped.length : 0,
    }, null, 2));
    if (details.length > 32000) {
      new Notice('The approval preview is too large for this dialog. Review and apply using the terminal CLI.');
      return;
    }
    new ConfirmModal(this.app, 'Apply reviewed decisions',
      'Review the selected note changes and removal impacts. Continue applies only these selected approvals. If the preview changes, review it again. Deletion requires current impact checks, a clean Git tree and a recoverable backup.',
      () => { void this.execute('apply', false, { proposalIds, planToken: String(preview.plan_token) }); }, details).open();
  }

  async refreshReview(id: string): Promise<void> {
    const result = await this.execute('refresh', false, { id });
    if (result?.outcome === 'concept_comparison_needed') {
      if (typeof result.message === 'string') new Notice(result.message);
      await this.previewReviews(); return;
    }
    if (result && (result.outcome === 'already_represented' || result.outcome === 'already_closed') && typeof result.message === 'string') new Notice(result.message);
    const file = result && typeof result.path === 'string' ? this.app.vault.getAbstractFileByPath(result.path) : undefined;
    if (file instanceof TFile) await this.app.workspace.getLeaf(false).openFile(file);
  }

  async suggestConsolidation(id?: string, memberPaths?: string[]): Promise<void> {
    const result = await this.execute('consolidate', false, memberPaths ? { memberPaths } : { id });
    const file = result && typeof result.path === 'string' ? this.app.vault.getAbstractFileByPath(result.path) : undefined;
    if (file instanceof TFile) await this.app.workspace.getLeaf(false).openFile(file);
  }

  async execute(action: Action, quiet = false, selection?: ReviewSelection): Promise<RecordValue | undefined> {
    try {
      if (!(this.app.vault.adapter instanceof FileSystemAdapter)) throw new Error('Research Vault requires a local desktop filesystem vault.');
      const vault = this.app.vault.adapter.getBasePath();
      const settings = { ...this.settings };
      const result = await this.runner.run(vault, settings, process.platform, action, selection);
      if (action === 'status' && result && this.runner.snapshot.status !== 'failed') {
        statusDetails(this.runner.snapshot, result);
        if (isRecord(result.last_report)) this.runner.snapshot.status = outcome(0, result.last_report);
        else this.runner.snapshot.status = 'idle';
      } else if (['sync', 'full', 'startup', 'apply', 'refresh', 'consolidate'].includes(action) && !this.runner.disposed) {
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
  openReviews(history = false): void { new ReviewPicker(this.app, history).open(); }
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
