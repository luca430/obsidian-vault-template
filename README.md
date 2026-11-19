# research-vault-template

An empty Obsidian vault and a working Python pipeline for scientific research.
Zotero owns references and source files; Obsidian holds research knowledge;
Python controls synchronization, validation and approvals; Codex supplies
structured scientific summaries and suggestions. The code is independent of your
research subject and Zotero collection.

**Nothing runs continuously.** A command checks sources, processes changes and
exits. Run it manually, from Obsidian's command palette, or optionally when the
vault opens. No custom Obsidian plugin, watcher, editor or persistent LLM session
is required. Zotero must be open during synchronization.

**All new conceptual links require review.** Citation records never automatically
become graph edges. Original manuscripts and Zotero data are read only. Personal
notes are never rewritten during synchronization. Generated notes have separate
managed and user-editable sections.

## Quick start

Install desktop Obsidian, Zotero with its local API, Python 3.11+, Git and a recent
Codex CLI. Install Node.js LTS if using the npm distribution of Codex. Linux and
Windows PowerShell are the target platforms; CI runs both. macOS is expected to
work with the Linux-style commands but has not been integration-tested here.
Obsidian mobile is unsupported because this workflow executes local programs.

First, click **Use this template → Create a new repository** on GitHub. Choose a
name for your project and its visibility. Copy that new repository's clone URL.
Replace `YOUR_ACCOUNT/YOUR_PROJECT` below with it.

### Linux / bash

```bash
git clone https://github.com/YOUR_ACCOUNT/YOUR_PROJECT.git
cd YOUR_PROJECT
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cp config.example.yaml config.yaml
cp works.example.yaml works.yaml
npm install -g @openai/codex
codex login
codex login status
```

Edit `config.yaml`, replace `REPLACE_ME` with your collection key, and enable the
Zotero local API as described below. Keep Zotero running. Then:

```bash
.venv/bin/python scripts/research.py doctor
.venv/bin/python scripts/research.py sync --dry-run
.venv/bin/python scripts/research.py sync
.venv/bin/python scripts/research.py status
.venv/bin/python -m pytest -q
```

### Windows / PowerShell

Use native Windows Python, Git, Zotero, Obsidian and Codex together. Keeping all
components in the same OS avoids WSL/Windows localhost and path translation.
Install Python with the Python launcher and Node.js LTS first.

```powershell
git clone https://github.com/YOUR_ACCOUNT/YOUR_PROJECT.git
Set-Location YOUR_PROJECT
py -3 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item config.example.yaml config.yaml
Copy-Item works.example.yaml works.yaml
npm.cmd install -g @openai/codex
codex.cmd login
codex.cmd login status
```

After editing `config.yaml` and enabling Zotero's API:

```powershell
& .\.venv\Scripts\python.exe scripts\research.py doctor
& .\.venv\Scripts\python.exe scripts\research.py sync --dry-run
& .\.venv\Scripts\python.exe scripts\research.py sync
& .\.venv\Scripts\python.exe scripts\research.py status
& .\.venv\Scripts\python.exe -m pytest -q
```

No virtual-environment activation or PowerShell execution-policy change is needed.
For standard npm installations the Python adapter resolves the `codex.cmd` shim
and invokes its JavaScript entry with Node directly, without a command shell.
A native Codex executable also works. Nonstandard batch wrappers fail explicitly;
set `codex.executable` to a native executable or use the standard npm layout.

Codex may consume account usage allowances or incur charges depending on your
account and authentication. The first synchronization extracts every configured
PDF and may make many inference requests. Extracted document text, manuscript
material and personal-note context are sent to the configured Codex inference
service. Confirm that your research material can be processed there before running
`sync`. An unchanged project makes no inference requests.

## Open the empty vault

In a fresh Obsidian installation choose **Open folder as vault**, then select the
cloned repository folder itself. Open `Home.md`. It is the dashboard and daily
workflow guide. The research folders start empty; no example papers or scientific
conclusions are installed. You can begin writing Markdown in `My Notes/` before
configuring automation.

The `.obsidian` directory contains only portable core settings. No community plugin
is installed or enabled automatically. Obsidian's automatic link rewriting is
turned off so deleting a generated note cannot silently change personal notes.
Keep that setting off when using the pipeline's removal workflow.

## Architecture and folders

```text
research-vault-template/
├── Home.md                   Dashboard; separate generated and editable areas
├── AGENTS.md                 Common scientific integrity and permission rules
├── config.example.yaml       Copy to ignored config.yaml
├── works.example.yaml        Copy to ignored works.yaml
├── agents/                   Five persistent role instruction files
├── Literature/               One note per Zotero parent key
├── Concepts/                 Concepts created through approved link proposals
├── My Work/                  Summaries of registered external documents
├── My Notes/                 User-authored research notes
├── Reviews/
│   ├── Links/                Pending/approved/rejected conceptual relationships
│   ├── Removals/             Collection membership decisions and impact
│   └── Revisions/            Advisory prose and maintenance suggestions
├── Templates/                Reference note/review layouts, optional manual templates
├── scripts/research.py       Same entry point for terminal and Obsidian
├── src/research_vault/       Config, API, extraction, agents, reviews, storage, CLI
├── tests/                    Synthetic PDFs, mocked Zotero/Codex, Git/lock/recovery tests
├── docs/                     Implementation, recovery and integration checklist
├── .pipeline/                Ignored state, staging, locks, logs and backups
└── .obsidian/                Portable core settings only
```

Python retrieves a complete Zotero snapshot before comparing membership, hashes
attachment bytes, follows registered manuscript includes, and detects personal-note
changes. Changed sources go to the appropriate summary agent. A changed graph
context runs linking, synthesis and maintenance roles. Python explicitly reads
`AGENTS.md` and the relevant `agents/<role>.md` on every invocation; it does not
assume arbitrary role files are automatically discovered by Codex.

Codex runs non-interactively with `codex exec`, staged JSON schemas, a read-only
sandbox, no approval prompts, disabled shell tools and disabled web search. User
Codex config and exec-policy rules are ignored; existing authentication is used.
Recent CLI flags `--ignore-user-config`, `--ignore-rules`, `--ephemeral` and
`--output-schema` are required. `doctor` checks them and login status. Upgrade the
CLI if necessary; the pipeline never falls back to broader permissions.
See the [official non-interactive guide](https://developers.openai.com/codex/noninteractive)
and [Codex source/install guide](https://github.com/openai/codex).

Agent responses are staged under `.pipeline/staging/`, checked against JSON schemas,
checked for prohibited link/managed syntax and checked against exact source excerpts.
Literature evidence must cover every processed PDF, including supplements. Python
integrates only permitted changes. An atomic write-ahead journal covers notes,
review registrations, the dashboard and state; an interrupted batch is rolled back
on the next normal `sync` or `apply`. The collection snapshot advances only when
the complete run succeeds. Unsuccessful reasoning is retried on the next run.

## Configure Zotero

1. Open Zotero and select **Settings → Advanced → Allow other applications on this
   computer to communicate with Zotero**. Keep Zotero running. Local reads need no
   API key. Do not configure write authorization; the adapter issues GET requests only.
2. Create or choose the project collection. Download the attached PDFs locally;
   cloud-only attachment entries cannot be extracted.
3. Select that collection, open **Tools → Developer → Run JavaScript** (some versions
   label it **Tools → Run JavaScript**), and evaluate this read-only expression:

   ```javascript
   ZoteroPane.getSelectedCollection().key
   ```

   Alternatively, open the collection in the Zotero web library; the URL's
   `/collections/XXXXXXXX` component contains the key. Do not use the collection's
   display name or an item's key.
4. Set `zotero.collection_key` in `config.yaml`. Use `library: users/0` for your
   personal local library or `groups/<numeric-id>` for a locally available group.
   Set `include_subcollections` to `true` or `false` explicitly.

The [official local API documentation](https://www.zotero.org/support/dev/web_api/v3/local_api)
describes the loopback endpoint, library IDs and local attachment-file URLs.
Collection-key lookup is also described in this
[Zotero support discussion](https://forums.zotero.org/discussion/125474/url-for-subcollection).
This implementation reads `/api/<library>/collections/<key>/items`, parent items,
children and attachment `/file/view/url` endpoints; it paginates with Total-Results
validation, checks available library-version/server headers and repeats membership
reads before accepting a snapshot. Missing totals, malformed/truncated data, API
errors and changed versions abort the run. None is interpreted as an empty collection.

A paper in multiple collections is deduplicated by parent key. Each vault's
`config.yaml`, `.pipeline` state and review history are independent. Do not switch
an initialized vault to a different library/collection; create a new vault. `--full`
reprocesses the current project without changing its identity.

### Main and supplementary attachments

All PDF children of one bibliographic item contribute to `Literature/ITEMKEY.md`.
By default, titles/filenames containing `supplement`, `supporting` or `appendix`
are supplementary; other PDFs are main. Multiple main PDFs are allowed. Review
these heuristics before the first run.

Override by stable **attachment key**, for example in your local configuration:

```yaml
attachments:
  overrides:
    ABCDEFGH: main
    IJKLMNPQ: supplementary
    RSTUVWXY: ignore
```

Alternatively set the attachment tag `research-vault:main` or
`research-vault:supplementary` in Zotero yourself. Config overrides win. The pipeline
never writes these tags. Non-PDF files are reported as ignored; explicitly marking
a non-PDF as main/supplementary raises an unsupported-format error. Supplementary
spreadsheets, videos and archives are not parsed in this baseline.

PyMuPDF extracts text from every PDF page and records attachment keys and page
numbers. Encrypted, missing, invalid or textless-page PDFs fail explicitly; there
is no OCR or figure-image understanding. A metadata-only item without a readable
PDF also fails explicitly rather than receiving a fabricated summary. Attach or
download its PDF, or move it outside the configured processing collection. Unchanged
PDFs are hashed but not re-extracted for literature processing.

## Register personal manuscripts

Keep originals outside the vault. Add authorized manuscript project directories
to local `config.yaml`; relative paths resolve from the vault root:

```yaml
source_roots:
  - ../manuscript
  - ../reports
```

Then edit local `works.yaml`:

```yaml
works:
  - id: draft-001
    title: Project manuscript
    document_type: manuscript
    status: drafting
    source_type: latex
    source_path: ../manuscript/main.tex
    bibliography_path: ../manuscript/references.bib
  - id: report-001
    title: Project report
    document_type: report
    status: submitted
    source_type: pdf
    source_path: ../reports/report.pdf
```

Keep an ID stable across renames or status changes. Prefer `source_type: latex`
when sources are available; use `pdf` for PDF-only work. Only the selected source
is read; the pipeline does not guess between two files or compile LaTeX.

Literal braced `\input{file}` and `\include{file}` are recursively resolved, with
`.tex` added when omitted. Includes are resolved relative to the including file.
Symlink resolutions must remain inside an explicitly authorized root. Cycles,
dynamic/macro include names and unbraced forms fail explicitly. No TeX engine,
`shell-escape`, package loader or arbitrary LaTeX command is executed. Mathematical
source expressions are preserved; complex macro expansion and external package
semantics are not interpreted. A bibliography is attribution only.

Summary notes appear in `My Work/<id>.md`, with objectives, concepts, methodology,
mathematical framework, contributions, limitations, questions and source locations.
They represent manuscript claims. Changed roots, includes, bibliography or registry
metadata trigger updates. Unchanged registered files skip extraction and inference.
A missing registered main source or removed registry entry leaves the existing
summary and produces a warning; it is never deleted automatically.

## Install Shell Commands and use Obsidian

In Obsidian, go to **Settings → Community plugins**, explicitly turn off Restricted
mode if you accept community plugins, choose **Browse**, find **Shell commands**
by **Taitava**, then **Install** and **Enable**. This is an explicit permission to
execute local commands. Do not install a custom research plugin.

Open **Settings → Shell commands**. Add the following four commands. Set each
command's alias/display name exactly as shown, and leave command-palette visibility
enabled. Choose **Bash** on Linux or **PowerShell** on Windows in the plugin's shell
settings. Configure stdout and stderr to an output panel or notification; do not
insert command output into the active note. Restart Obsidian after installing CLI
tools if its inherited PATH is stale.

`{{vault_path}}` is a plugin variable. The default working directory is already the
vault root, but these commands also use absolute vault paths, so virtual-environment
activation and the current note's directory do not matter. For unusual directory
names containing shell metacharacters, use the plugin's variable escaping settings
and inspect its command preview. Standard paths with spaces work with these quotes.
See the [Shell Commands variable documentation](https://publish.obsidian.md/shellcommands/Variables/%7B%7Bvault_path%7D%7D)
and [plugin guide](https://publish.obsidian.md/shellcommands).

### Linux commands (Bash)

**Research: Update knowledge base**

```bash
"{{vault_path}}/.venv/bin/python" "{{vault_path}}/scripts/research.py" sync
```

**Research: Full synchronization**

```bash
"{{vault_path}}/.venv/bin/python" "{{vault_path}}/scripts/research.py" sync --full
```

**Research: Apply reviewed changes**

```bash
"{{vault_path}}/.venv/bin/python" "{{vault_path}}/scripts/research.py" apply
```

**Research: Check status**

```bash
"{{vault_path}}/.venv/bin/python" "{{vault_path}}/scripts/research.py" status
```

### Windows commands (PowerShell)

**Research: Update knowledge base**

```powershell
& "{{vault_path}}\.venv\Scripts\python.exe" "{{vault_path}}\scripts\research.py" sync
```

**Research: Full synchronization**

```powershell
& "{{vault_path}}\.venv\Scripts\python.exe" "{{vault_path}}\scripts\research.py" sync --full
```

**Research: Apply reviewed changes**

```powershell
& "{{vault_path}}\.venv\Scripts\python.exe" "{{vault_path}}\scripts\research.py" apply
```

**Research: Check status**

```powershell
& "{{vault_path}}\.venv\Scripts\python.exe" "{{vault_path}}\scripts\research.py" status
```

Press Ctrl+P to open the command palette and select the alias (some plugin versions
prefix aliases with “Shell commands:”). These are the same one-shot Python calls
used in a terminal. You may assign Obsidian hotkeys to them.

### Optional update on vault startup

First verify the incremental command manually. Open that command's settings in
Shell Commands, open its **Events** settings and enable **Obsidian starts**. Keep
it visible in the command palette as well. Leave the global event-disable switch
off. Enable this event only for the incremental command, never the full or apply
commands. Each vault-open event starts one update and exits; the OS lock rejects
concurrent invocations. Zotero must already be open; a failed startup sync is
reported and does not mark papers removed. Run the palette command again after
opening Zotero. See [Shell Commands events](https://publish.obsidian.md/shellcommands/Events/Events%20-%20general%20principles).

## Graph colors

Use Obsidian's core **Graph view: Open graph view** command. Open the graph settings
(cog icon). In **Filters → Search files**, enter:

```text
-path:"Reviews/" -path:"agents/" -path:"Templates/" -path:"docs/" -file:README -file:AGENTS -file:Home
```

This excludes the review queue and instruction/template nodes from the main
scientific graph. Enable **Existing files only** and turn off **Tags** and
**Attachments** if you only want note nodes. Under **Groups**, add these filters
and colors in order:

| Filter | Category | Color |
| --- | --- | --- |
| `path:"Literature/"` | External literature | Blue `#3B82F6` |
| `path:"My Work/"` | Personal manuscripts | Orange `#F59E0B` |
| `path:"My Notes/"` | Personal hypotheses | Green `#22C55E` |
| `path:"Concepts/"` | Concepts | Purple `#A855F7` |
| `path:"Reviews/"` | Review queue, when shown | Gray `#9CA3AF` |

Temporarily remove the Reviews filter to inspect its gray nodes. Pending proposals
contain no wikilinks, so they do not introduce proposed edges even when visible.
The settings are configured through the supported UI instead of committing an
undocumented `graph.json` schema. No extra graph plugin is needed. See the
[official Graph view instructions](https://help.obsidian.md/plugins/graph).

## Review conceptual relationships

After `sync`, open files in `Reviews/Links/` in Obsidian. Each has a stable proposal
ID, plain-text source/target paths, relationship type, scientific justification,
evidence, timestamp and `status: pending`. A proposed new concept is contained in
`target_concept`; it is not created yet.

Use source mode or Obsidian Properties to change only:

```yaml
status: approved
```

or `status: rejected`. Then run the apply command. Python verifies the registered
payload and unchanged source notes, checks evidence, creates an approved concept
if needed and appends a single relationship to the source's approved-links section.
Approving a source under `My Notes/` explicitly authorizes that appended link;
synchronization and deletion never modify personal notes. Reject a proposal if you
want the personal note untouched. Existing body text is preserved.

Never edit proposal payload fields to approve a different target or justification.
The local registry records their digest and rejects such edits. The LLM cannot set
status or approval fields. Do not manually copy template review layouts into the
queue; only registered proposals can be applied.

If a source changes after you approve it, apply refuses the stale decision. Reset
its status to `pending`, run `sync`, review the current sources/evidence again and
then approve. If the evidence no longer exists, reject the old proposal. Changed
removal impact creates a fresh review; reject older obsolete removal proposals.
A returned collection member invalidates old removal decisions.

Rejected links are retained. The same endpoints, relationship and evidence produce
the same identity even if the LLM rephrases its justification. New evidence can
produce a new proposal. Already-existing links are not proposed again. Python
checks excerpt presence and structural integrity; **scientific validity remains
a human review decision**, especially for claimed equivalence or contradiction.

Synthesis and maintenance output goes to `Reviews/Revisions/`. These are advisory:
edit the target note yourself, then mark the revision approved to acknowledge it.
`apply` records that acknowledgement without rewriting the target. This deliberate
baseline restriction protects personal interpretations and avoids automatic
scientific prose replacement.

## Removed papers

A complete successful snapshot may find that a previously active paper is absent.
This means absent from this project's selected collection, not necessarily deleted
from Zotero or scientifically invalid. A removal review includes identity, incoming
and outgoing conceptual links, affected pages and possible consequences.

Set `status: approved` and choose one `decision`:

| Decision | Result |
| --- | --- |
| `pending` | Make no change |
| `flag` | Keep note and mark its managed content as absent from the active collection |
| `keep` | Retain it without an absence banner |
| `delete` | Explicitly delete the generated literature note after safety checks |

Before deletion, preview from the terminal (or add a fifth Shell Commands alias):

```bash
.venv/bin/python scripts/research.py apply --dry-run
```

PowerShell equivalent:

```powershell
& .\.venv\Scripts\python.exe scripts\research.py apply --dry-run
```

This prints all affected write/delete paths and the current incoming/outgoing link
impact. Commit or stash tracked working-tree changes before actual deletion. In a
newly initialized local repository create its initial commit first. The vault must
be the root of its own Git repository, Git must be available and HEAD must exist.
`sync` changes the tracked Home dashboard, so commit that dashboard before deleting.
Never bypass the check by broadening permissions.

The apply command verifies a ZIP backup with SHA256 manifest under
`.pipeline/backups/` and records `.pipeline/last-impact.json` before deletion. This
protects ignored generated notes and processing state that a Git commit alone
would not cover. Git must be available and clean even though the backup is the
recovery artifact. If a checkpoint cannot be made, deletion is refused.

Only links in Python-owned `links` sections of generated pages are converted back
to plain target text. Personal notes and links in user sections remain unchanged;
revision proposals explain what needs review. External Markdown/HTML links are not
rewritten. If the paper returns to the collection, the same parent key reactivates
its existing note or regenerates the deleted note without creating a new identity.
See [recovery instructions](docs/operations.md) before restoring a backup.

## Commands, status and troubleshooting

All commands accept `--vault PATH` before the command when invoked as a module:

```bash
.venv/bin/python -m research_vault --vault . sync
```

The wrapper script selects its own vault directory, even when called from elsewhere.

| Command | Behavior |
| --- | --- |
| `sync` | Incremental summaries and pending proposals; no approvals applied |
| `sync --full` | Reprocess all active papers/available works and reconsider graph context |
| `sync --dry-run` | Read source metadata/fingerprints and show plan; no writes or inference |
| `apply --dry-run` | Validate approvals and print affected paths/removal impact; no writes |
| `apply` | Deterministically apply validated user decisions |
| `status` | Report counts, last success, warnings and pending transaction status |
| `doctor` | Check config, packages, Zotero snapshot, source authorization and Codex/login/flags |

`Home.md` shows last successful synchronization, processed/active papers, pending
links/removals/revisions, warnings and errors. Edit only below the generated status
section. Normal sync/apply commands take a nonblocking OS file lock. Read-only
status/doctor/dry-run commands may inspect a changing vault; dry runs refuse an
unrecovered journal. No command requires an active VS Code session.

Inspect `.pipeline/logs/events.jsonl` for execution outcomes and
`.pipeline/staging/<run-id>/stderr.txt` for a failed Codex call. Staging also contains
the actual input, schema and response; it may contain sensitive research text.
Keep `.pipeline` private and backed up. Do not delete state to “fix” a failed run:
you would lose rejection/approval history. Run the same command again after fixing
the cause. The next normal mutation recovers an interrupted file transaction first.

| Symptom | Action |
| --- | --- |
| Zotero closed / connection refused | Open Zotero, wait for it to load, run doctor and retry sync |
| Zotero HTTP 403 | Enable the Advanced local API preference; no local API key is needed for reads |
| Wrong collection or library | Verify key/library through Zotero; do not switch an initialized project to another collection |
| Truncated/inconsistent API result | Wait for Zotero synchronization to finish; retry; no removal snapshot is accepted |
| Missing PDF | Download it from Zotero, check linked-file paths, retry; notes/snapshot stay unchanged |
| Scanned or encrypted PDF | Supply an accessible text PDF; OCR/decryption is outside this baseline |
| Inaccessible manuscript/include | Check source_roots, path spelling, permissions and recursive includes |
| Codex not found from Obsidian | Restart Obsidian to refresh PATH or set codex.executable to its local absolute path |
| Login/model/usage error | Run codex login/status in a terminal, select an available model and inspect limits |
| Missing Codex flags | Update Codex; no insecure compatibility fallback is used |
| Invalid agent JSON/evidence | Inspect staging response; retry; prior notes and successful snapshot are retained |
| Input too large | Narrow the collection or explicitly raise max_input_chars within your model's context capacity; no text is silently dropped |
| Plugin command does not run | Confirm plugin enabled, alias visible, correct Bash/PowerShell shell, quoted vault variable, output panel and venv path |
| Another run holds lock | Wait for that process or stop it; the lock releases when the process exits, regardless of the lock file's presence |
| Malformed/missing review | Restore the file from your backup, correct only status/decision; apply refuses the batch |
| Stale approval/changed impact | Reset pending and sync to re-review, or reject an obsolete removal proposal |
| Dirty Git tree blocks delete | Review and commit/stash tracked changes, including the dashboard, then preview/apply again |

## Git, privacy and template updates

Reusable code, agent instructions, templates, examples and portable Obsidian settings
are versioned. Actual `config.yaml`, `works.yaml`, `.pipeline`, research notes,
review queues, source PDFs/TeX/BibTeX and local plugin settings are ignored by default.
Each new template project initializes with no state or scientific content. Do not
copy another project's `.pipeline` directory into a new project.

A GitHub template copy does not automatically receive future template updates.
Before updating, stop running pipeline commands, back up the entire vault including
ignored files and local state, and commit your tracked changes. Add the template
repository as a read-only upstream remote and inspect a diff on an update branch.
Selectively merge/cherry-pick code, tests, agent rules and documentation; preserve
local configuration, state, research folders, Home's user section and review history.
Run the synthetic tests, doctor and a dry run before normal sync. Instruction/code
changes deliberately invalidate processing recipes and may reprocess documents.
State-schema changes must have an explicit migration; incompatible schemas fail.
Avoid a blind reset, copying an upstream Home.md over your dashboard or force-adding
ignored research/configuration files.

Git does not back up ignored content. Use an encrypted local backup for the full
vault, manuscripts and `.pipeline`. If you intentionally want private research
under version control, adapt ignore rules only in your own private project, after
reviewing source rights and secrets. The public template must remain empty.

## Publish this repository as a GitHub template

This workspace contains the implementation but does not create a remote repository
or upload anything. To publish from a normal writable local checkout:

```bash
git init
git add .
git diff --cached --stat
git commit -m "Create reusable research vault template"
git branch -M main
```

Create an **empty** repository called `research-vault-template` on GitHub, then:

```bash
git remote add origin https://github.com/YOUR_ACCOUNT/research-vault-template.git
git push -u origin main
```

If the checkout already has a Git repository/remote, inspect `git status` and
`git remote -v` and reuse it instead of adding a duplicate origin. Review the staged
files to ensure there are no actual sources, local settings, caches or credentials.
In GitHub **Settings → General**, enable **Template repository**. A new project can
then choose **Use this template**, clone its own repository and follow the quick
start. GitHub hosting is optional for local pipeline use.

## Validation and current limits

Run `python -m pytest -q` inside the project venv. Tests use generated synthetic PDFs,
mock HTTP responses and mock agent execution; no Zotero account, Codex session or
inference bill is required. A temporary local Git repository tests deletion and
backup behavior. CI covers Python 3.11/3.13 on Linux and Windows. Local validation
and remaining live checks are recorded in [docs/validation.md](docs/validation.md).

This is a working baseline with explicit limits: no OCR/figure understanding,
non-PDF supplement parser, full TeX engine, automatic prose revision/merge, semantic
proof checker, source-level changed-page batching or large-library retrieval index.
Whole changed parent papers are reprocessed; graph reasoning receives the current
vault context and stops explicitly at the configured input limit. Exact duplicate
concept titles and structural graph issues get deterministic checks; broader
near-duplicates and obsolete scientific claims require agent suggestions and human
review. Initial processing fails safely on an unsupported configured source.

Live Zotero version behavior, actual Codex structured responses and native Windows
sandbox/npm setup, Obsidian plugin events and graph UI need real-world integration
testing. These are not simulated as successful integrations by the tests. See the
[integration checklist](docs/validation.md) before relying on a research project.
