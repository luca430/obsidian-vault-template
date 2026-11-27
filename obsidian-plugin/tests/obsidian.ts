// Minimal official-API surface for command, startup, confirmation and view tests.
export class Element {
  children: Element[] = [];
  text = '';
  cls = '';
  disabled = false;
  checked = false;
  scrollTop = 0;
  scrollHeight = 0;
  clientHeight = 0;
  events = new Map<string, () => void>();
  createEl(_tag: string, options: { text?: string; cls?: string } = {}): Element {
    const child = new Element(); Object.assign(child, options); this.children.push(child); return child;
  }
  createDiv(options?: { cls?: string }): Element { return this.createEl('div', options); }
  addClass(cls: string): void { this.cls = cls; }
  setText(text: string): void { this.text = text; }
  empty(): void { this.children = []; }
  addEventListener(name: string, callback: () => void): void { this.events.set(name, callback); }
  querySelector(cls: string): Element | undefined { return this.children.find(child => child.cls === cls.slice(1)); }
}
export class FileSystemAdapter {
  constructor(private root: string) {}
  getBasePath(): string { return this.root; }
}
export class TFile { constructor(public path: string) {} }
export class App {
  ready: (() => void)[] = [];
  files: TFile[] = [];
  opened: string[] = [];
  leaves: WorkspaceLeaf[] = [];
  metadataCache = { getFileCache: (_file: TFile) => ({ frontmatter: { status: 'pending' } }) };
  vault = {
    adapter: new FileSystemAdapter('/synthetic-vault'),
    getMarkdownFiles: () => this.files,
    getAbstractFileByPath: (name: string) => this.files.find(file => file.path === name),
  };
  workspace = {
    onLayoutReady: (callback: () => void) => this.ready.push(callback),
    getLeavesOfType: (_type: string) => this.leaves,
    getRightLeaf: (_split: boolean) => new WorkspaceLeaf(),
    revealLeaf: async (_leaf: WorkspaceLeaf) => {},
    getLeaf: (_newLeaf: boolean) => ({ openFile: async (file: TFile) => { this.opened.push(file.path); } }),
  };
}
export class Plugin {
  app = new App();
  commands: { id: string; name: string; callback: () => void }[] = [];
  stored: unknown;
  async loadData(): Promise<unknown> { return this.stored; }
  async saveData(data: unknown): Promise<void> { this.stored = data; }
  registerView(_type: string, _callback: unknown): void {}
  addSettingTab(_tab: unknown): void {}
  addCommand(command: { id: string; name: string; callback: () => void }): void { this.commands.push(command); }
  addRibbonIcon(_icon: string, _label: string, _callback: () => void): void {}
}
export class WorkspaceLeaf { async setViewState(_state: unknown): Promise<void> {} }
export class ItemView {
  contentEl = new Element();
  constructor(_leaf: WorkspaceLeaf) {}
}
export class Modal {
  static opened: Modal[] = [];
  contentEl = new Element();
  constructor(public app: App) {}
  open(): void { Modal.opened.push(this); this.onOpen(); }
  close(): void { this.onClose(); }
  onOpen(): void {}
  onClose(): void {}
}
export class SuggestModal<T> extends Modal {
  setPlaceholder(_text: string): void {}
  onChooseSuggestion(_value: T): void {}
}
class Button {
  constructor(private el: Element) {}
  setButtonText(text: string): this { this.el.text = text; return this; }
  setWarning(): this { return this; }
  onClick(callback: () => void): this { this.el.events.set('click', callback); return this; }
}
class Toggle {
  constructor(private el: Element) {}
  setValue(value: boolean): this { this.el.checked = value; return this; }
  onChange(callback: (value: boolean) => void): this {
    this.el.events.set('change', () => callback(this.el.checked)); return this;
  }
}
export class Setting {
  constructor(private el: Element) {}
  addButton(callback: (button: Button) => void): this { callback(new Button(this.el.createEl('button'))); return this; }
  addToggle(callback: (toggle: Toggle) => void): this { callback(new Toggle(this.el.createEl('input'))); return this; }
}
export class PluginSettingTab {
  containerEl = new Element();
  constructor(_app: App, _plugin: Plugin) {}
}
export class Notice {
  static messages: string[] = [];
  constructor(text: string) { Notice.messages.push(text); }
}
