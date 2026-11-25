# research-vault-template

An empty Obsidian vault and a working Python pipeline for scientific research.
Zotero owns references and source files; Obsidian holds research knowledge;
Python controls synchronization, validation and approvals; Codex or Claude Code supplies
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

## Table of contents

- [Quick start](#quick-start)
- [Choose LLM runtimes](#choose-llm-runtimes)
- [Open the empty vault](#open-the-empty-vault)
- [Architecture and folders](#architecture-and-folders)
- [Configure Zotero](#configure-zotero)
- [Register personal manuscripts](#register-personal-manuscripts)
- [Install Shell Commands and use Obsidian](#install-shell-commands-and-use-obsidian)
- [Graph colors](#graph-colors)
- [Review conceptual relationships](#review-conceptual-relationships)
- [Removed papers](#removed-papers)
- [Initial sync, incremental work and request budgets](#initial-sync-incremental-work-and-request-budgets)
- [Commands, status and troubleshooting](#commands-status-and-troubleshooting)
- [Git, privacy and template updates](#git-privacy-and-template-updates)
- [Publish this repository as a GitHub template](#publish-this-repository-as-a-github-template)
- [Validation and current limits](#validation-and-current-limits)

## Quick start

Install desktop Obsidian, Zotero with its local API, Python 3.11+, Git and a recent
Codex CLI. Install Node.js LTS if using the npm distribution of Codex. Native Linux, native Windows and WSL2 with Windows desktop apps are supported
installation modes. CI is configured for Linux and Windows. macOS is expected to
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

For the native Windows installation, use Windows Python, Git, Zotero, Obsidian
and Codex together. For the split desktop/WSL configuration, use the next section.
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

### WSL2 / Windows desktop apps

Supported split installation: **Windows Obsidian and Zotero**, **WSL2 Python, Git,
Node/Codex**, and one vault on the Windows filesystem (visible inside WSL under
`/mnt/c/`, or your configured mount). Do not run native Windows Python against the
Linux virtual environment. Choose a WSL distribution and use it consistently.

1. Install/update WSL2 using [Microsoft's WSL installation guide](https://learn.microsoft.com/windows/wsl/install).
   Keep Obsidian and Zotero installed on Windows. Clone your new project with Git
   inside WSL into a Windows directory; open that same directory as a Windows
   Obsidian vault. Use `wslpath -u 'YOUR_WINDOWS_VAULT_DIRECTORY'` to find its Linux
   path, then `cd` there. This setup intentionally keeps the vault on Windows for
   the desktop apps; filesystem operations can be slower than Linux-local storage.
2. Inside that directory in WSL, run the Linux quick-start commands from
   `python3 -m venv .venv` onward. Install Linux Node/Codex, then run `codex login`
   **inside that distribution**. A Windows Codex login or npm shim is insufficient.
   Check `command -v python3`, `command -v node` and `command -v codex`; Node/Codex
   must be Linux executables. The adapter rejects Windows `.exe`/`.cmd`/`.bat`
   Codex launchers in WSL and stages Codex in a Linux-visible vault directory.
3. Enable Zotero's local API on Windows. On Windows 11 22H2 or later with current
   WSL, set this in `%UserProfile%\.wslconfig` (merge with existing settings):

   ```ini
   [wsl2]
   networkingMode=mirrored
   ```

   Restart WSL from PowerShell with `wsl --shutdown` after saving other WSL work;
   this stops all distributions. Mirrored mode permits WSL to reach Windows
   servers through loopback. See [Microsoft's networking documentation](https://learn.microsoft.com/windows/wsl/networking#mirrored-mode-networking).
   This pipeline deliberately retains its loopback-only API policy; it does not
   expose Zotero to the LAN or add firewall rules. NAT host-IP workarounds are
   outside this supported split configuration.
4. With Windows Zotero open, check connectivity **from WSL**:

   ```bash
   curl --fail http://127.0.0.1:23119/api/users/0/collections
   .venv/bin/python scripts/research.py doctor
   .venv/bin/python scripts/research.py sync --dry-run
   ```

   Set `zotero.base_url: http://127.0.0.1:23119/api/` if localhost resolves to an
   unsuitable address. Connection refusal/403 remains blocking: check mirrored
   mode, Windows Zotero's API setting and firewall policy. No API key is needed.
5. Select **PowerShell** in Windows Obsidian's Shell Commands settings. Use the
   WSL commands below, not the native Windows Python commands. Check `doctor` from
   that same launch environment before enabling a startup event.

Windows file URLs are UTF-8 decoded and translated with `wslpath -u`, without a
shell or a hardcoded drive/mount/user. This covers Zotero-managed PDFs and linked
PDFs, including ZotMoov-managed locations. OneDrive files must be downloaded and
readable locally; use Windows **Always keep on this device** when needed. Cloud
placeholders may still fail at open/hash/extraction time: the parent is skipped or
retried independently; existing summaries stay intact when no text is available.
A missing supplement does not block an available main article: its note is marked
partial, and the missing attachment is retried on the next synchronization.

`source_roots`, manuscript `source_path` and `bibliography_path` accept Linux
paths, Windows paths or local file URLs using the same resolver. Prefer YAML
single quotes for Windows backslashes. Authorization is checked **after** translation
and symlink resolution. UNC paths require a working Windows share/mount; translation
does not mount a share or download a cloud file. Doctor includes the original URL,
resolved path and reason for attachment access failures.

LLM inference may consume account usage allowances or incur charges depending on your
account and authentication. The first synchronization extracts every configured
PDF and may make many inference requests. Extracted document text, manuscript
material and personal-note context are sent to the explicitly configured LLM inference
service. Confirm that your research material can be processed there before running
`sync`. An unchanged project makes no inference requests.

## Choose LLM runtimes

Codex remains the default. Existing `codex:` configuration and processing state
continue to work without an `llm:` section. You can install only the runtime you
use; an absent optional runtime does not block `doctor`. The earlier quick-start
commands install and authenticate Codex with `npm install -g @openai/codex`,
`codex login` and `codex login status`.

For Claude Code, follow Anthropic's [installation instructions](https://code.claude.com/docs/en/setup).
Use the native installer for Linux/WSL or Windows; WinGet users can run
`winget install Anthropic.ClaudeCode`. In a terminal in the same environment as
Python, run `claude --version`, `claude auth login` and `claude auth status`.
The [CLI reference](https://code.claude.com/docs/en/cli-reference) documents these
authentication and diagnostic commands. Existing CLI-managed credentials are used;
never put credentials into `config.yaml` or commit them. API credentials supplied
through the environment are also left to the CLI to resolve.

Select providers in local `config.yaml`:

```yaml
llm:
  default_provider: codex
  agents:
    literature: codex
    manuscript: codex
    concepts: codex
    linking: {provider: claude, model: sonnet}
    synthesis: claude
    maintenance: claude
  evaluation:
    enabled: false
    provider: claude
codex:
  executable: codex
  model: null
claude:
  executable: claude
  model: null
  timeout_seconds: 600
  max_input_chars: 500000
  output_reserve_chars: 16000
```

Each role accepts either a provider name or a mapping containing `provider` and/or
`model`. Supported provider names are `codex` and `claude`. Both providers use the
same `AGENTS.md`, `agents/<role>.md`, scientific schemas and Python validators.
Provider-specific configuration contains only execution settings. Character
budgets and timeout settings have the same meanings for both providers.

Override all primary role providers for one run, without changing configuration:

```bash
.venv/bin/python scripts/research.py sync --provider claude
.venv/bin/python scripts/research.py sync --provider codex --background
```

On Windows, substitute `.\.venv\Scripts\python.exe` as in the quick start. The
precedence is run override, per-role provider, then default provider. A per-role
model applies only to its configured provider, so a run override never sends a
Claude model name to Codex or vice versa. Model aliases/defaults may change outside
this vault; pin a supported model ID when you need more reproducible inference.

Native Windows accepts native executables and standard npm `.cmd`/`.bat` shims;
Python resolves npm entries through Node without a command shell. In WSL, install
and authenticate the **Linux** provider CLI inside WSL. Windows executables found
on WSL's PATH are rejected. Set `codex.executable` or `claude.executable` to a local
absolute executable path if Obsidian's environment has a different PATH, then
restart Obsidian. Never reuse a Windows Python environment from WSL.

Claude runs through `claude -p` with JSON output. Its wrapper is normalized from
`structured_output` (or a JSON-only `result` fallback); the unchanged scientific
schema is then validated in Python. Unsupported transport string-length
constraints are omitted for Claude, while Python still enforces the full schema.
See Anthropic's [programmatic execution guide](https://code.claude.com/docs/en/headless)
and [structured output constraints](https://platform.claude.com/docs/en/build-with-claude/structured-outputs).

Inference metadata is separate from scientific output: cached values retain the
existing schemas, evidence and provenance. Cache metadata and
`.pipeline/logs/operations.jsonl` record provider, requested model, CLI version,
instruction/schema hashes, validation status, scientific warnings and usage when
available. `.pipeline/logs/inference.jsonl` records transport outcomes. Reports
include `providers`, `inference_metadata`, `inference_operations`,
`inference_failures` and `evaluation_failures`. Cached events retain historical
usage with `cached: true`; that usage is not a new billable operation. Unknown
resolved model/usage data remain unknown rather than being estimated.

Codex keeps its existing read-only sandbox and disabled shell tools. Claude runs
with built-in tools removed, MCP denied/empty, safe and restricted modes, external
instruction discovery disabled, hooks disabled, and no resumable session. Neither
provider can create approvals, write notes through tools or apply proposals. Claude's
tool restrictions are not an OS filesystem sandbox. Both adapters depend on a
trusted installed CLI and host environment; organization-enforced policies are
outside this template's control. Managed Claude hooks can override local hook
settings: deployments requiring managed executable hooks are not supported by
this isolation profile. See [Claude settings policy](https://code.claude.com/docs/en/settings-reference#disableallhooks).
The adapter checks required CLI flags and fails if isolation flags are unavailable;
it never retries with fewer restrictions. It uses safe mode rather than bare mode
so normal CLI login remains available.

Provider changes invalidate only affected inference stages. PDF extraction caches
have no provider dependency. Switching back reuses the earlier provider's valid
inference cache. Codex's existing cache-key contract and old cache envelopes are
accepted; old entries are marked as legacy when metadata is unavailable. Updated
global/role instruction files intentionally invalidate affected inference, as
before. No state migration, review deletion or note renaming is required.

Dual-provider evaluation is **off by default**. Enabling `llm.evaluation` runs the
secondary runtime for scheduled inference jobs and stores its validated responses
under `.pipeline/cache/evaluations/`. It incurs additional inference cost, never
creates secondary proposals or graph edges, and never selects a scientific winner.
An unavailable/invalid comparison is reported in `evaluation_failures` without
failing a successful primary workflow. Enabling evaluation alone does not schedule
unchanged documents; explicitly use `--full` when you want to compare them.

Run `doctor` after changing runtimes. It reports both CLIs' installation, version,
authentication, required flags and which providers each role requires. Invalid
provider names, role names and execution settings fail configuration validation.
If authentication fails, log in from the same environment; if flags are missing,
update the CLI. If a CLI exits unsuccessfully or returns invalid JSON, inspect its
private staging directory's `stderr.txt`, response/output files and `metadata.json`.
No automatic provider fallback occurs: existing notes and approvals remain intact.

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
├── agents/                   Six persistent role instruction files
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
├── tests/                    Synthetic PDFs, mocked Zotero/LLM runtimes, Git/lock/recovery tests
├── docs/                     Implementation, recovery and integration checklist
├── .pipeline/                Ignored state, staging, locks, logs and backups
└── .obsidian/                Portable core settings only
```

Python accepts a complete Zotero membership snapshot before comparing removals,
hashes attachment bytes, follows registered manuscript includes and detects
personal-note changes. File accessibility is separate from collection membership.
Each processing stage has its own versioned cache contract, relevant role/global
instruction hashes, source fingerprints and model settings. Upstream extraction
changes propagate to summaries; editing synthesis instructions does not invalidate
PDF extraction, document summaries or concept extraction.

### Stage A: independent document processing

Each new/changed Zotero **parent key** produces one literature note. Its main and
supplementary attachments are extracted and summarized independently, then their
validated summaries are consolidated. A missing supplement permits a usable main
summary with partial coverage; retries reuse unchanged attachment summaries. Missing
page text also leaves coverage partial without discarding usable pages. Each registered manuscript
is another job. Unchanged documents reuse their successful notes. Text extraction,
validated inference results and original page records are cached privately under
`.pipeline/cache/`. SHA256 content fingerprints include metadata, attachments and
annotations; filenames alone never establish identity. Bytes are rechecked around
processing. Cache entries are reused only with matching inputs and recipe.

Oversized documents use section/paragraph-aware chunks where text headings permit,
preserving every input character and page/file locator. Bounded neighboring text
retains definitions, assumptions and explanatory context. Recognized LaTeX/display
math spans stay together when they fit; over-budget expressions are split only
with an explicit uncertainty warning. Validated partial summaries are consolidated hierarchically.
The resulting note keeps **all detailed chunk summaries and source evidence**
alongside the overview. A failed chunk leaves its attachment incomplete; successful chunks remain cached.
If parent consolidation fails, validated attachment claims remain available in a
partial note, and consolidation is retried. Summarization is still interpretive: complete text
coverage is not proof that an LLM preserved every scientific detail. Inspect the
sources and limitations, especially mathematical derivations.

Successful document notes/state commit individually using the existing write-ahead
journal. An interruption resumes pending jobs without repeating completed inference.
One missing PDF, offline cloud file, extraction error or invalid LLM response does
not discard other completed papers. Parents with no successfully summarized attachment retain their older note.
Partial parents keep successful attachment findings and retry unavailable/failed
sources. No full-text scientific claims are generated from metadata alone.

Coverage is separate from job status, at both parent and attachment levels:

| Coverage | Meaning |
| --- | --- |
| `metadata_only` | Metadata available; no usable full-text summary for this source |
| `partial` | Page/source coverage or consolidation is incomplete; usable text, if any, is retained |
| `complete` | All selected supported sources and required consolidation succeeded |
| `failed` | Processing failed without a usable current document result |

Explicitly ignored attachments are outside the selected processing scope.
Unsupported attachments stay metadata-only and make an otherwise usable parent
partial. Structured claim/evidence/uncertainty records retain exact attachment/page
or filename/section references. PDF equations, table layout and figures remain
unverified text extraction; plausible reconstructions never become verified facts
through formatting or a successful schema check.

### Stage B: bounded conceptual reasoning

A persistent private **concept registry** in `.pipeline/state.json` records
provisional LLM concept definitions, exact supporting note excerpts, source
fingerprints and document associations. It updates only changed records. These
annotations do not create public Concepts pages or Obsidian graph edges. Reviewed
relationships are recorded separately only after the user-approved `apply` action.

A persistent inverted lexical index covers existing summaries, manuscripts,
personal notes and reviewed concepts. Changed fragments query at most
`candidate_terms` rare terms and score at most `candidate_pool` candidate fragments;
`graph_neighbors` bounds the distinct source documents selected. There is no
exhaustive document-pair comparison. Concept annotations enrich the index, and
ranked original cached source excerpts supply additional evidence within each
request budget. New documents can retrieve older literature, manuscripts and
personal notes. Availability/completeness labels distinguish retained historical
findings from currently accessible evidence.

Linking, synthesis and maintenance use the same bounded context. Retrieval proposes
candidates; shared words and citations never authorize links. Outputs need exact
quotes from supplied notes and validation against the full current notes. Only
Python creates pending reviews. The separate `apply` command retains all existing
approval, stale-evidence, backup and removal checks. No automatic graph links are
introduced, and the literature/personal research/personal notes graph groups remain.

Retrieval is deliberately dependency-free and approximate. Common-term postings
and the candidate pool are capped; lexical synonyms and vocabulary-disjoint
relationships can be missed. **Failure to retrieve is not proof of no relationship.** It may miss relationships
with disjoint vocabulary or distant passages; increasing `graph_neighbors` or using
`--full` broadens reconsideration but cannot guarantee exhaustive scientific discovery.
All fragments are indexed, but each reasoning call sees only its selected excerpts.
Validated reasoning calls are cached, so interrupted Stage B work can resume too.

Python explicitly reads `AGENTS.md` and `agents/<role>.md` on every invocation; it
does not assume arbitrary saved role files are automatically discovered by a CLI.

The default Codex adapter runs non-interactively with `codex exec`, staged JSON schemas, a read-only
sandbox, no approval prompts, disabled shell tools and disabled web search. User
Codex config and exec-policy rules are ignored; existing authentication is used.
Recent Codex CLI flags `--ignore-user-config`, `--ignore-rules`, `--ephemeral` and
`--output-schema` are required. `doctor` checks them and login status. Upgrade the
CLI if necessary; the pipeline never falls back to broader permissions.
See the [official non-interactive guide](https://developers.openai.com/codex/noninteractive)
and [Codex source/install guide](https://github.com/openai/codex).

Agent responses are staged under `.pipeline/staging/`, checked against JSON schemas,
checked for prohibited link/managed syntax and checked against exact source excerpts.
Malformed outputs, invalid identifiers and evidence naming sources or locators that
were not processed remain processing errors. Quote mismatches at valid source
locations become scientific review warnings, with the evidence and failure reason retained.
Literature evidence must cover every processed PDF, including supplements. Python
integrates only permitted changes. An atomic write-ahead journal covers notes,
review registrations, the dashboard and state; an interrupted batch is rolled back
on the next normal `sync` or `apply`. Complete membership snapshots are saved
independently from document success. `last_success` advances only when all document
jobs and reasoning complete; unfinished work is retried on the next run.

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
a non-PDF as main/supplementary still reports an unsupported format and ignores it. Supplementary
spreadsheets, videos and archives are not parsed in this baseline.

The dependency floor is PyMuPDF 1.24.3, which supplies the current `pymupdf` import
([upstream change log](https://pymupdf.readthedocs.io/en/latest/changes.html#changes-in-version-1-24-3)).
PyMuPDF extracts each PDF page independently and records attachment keys, original
page numbers and extraction status: `text`, `ocr` or `unprocessed`. By default,
pages with fewer than 10 non-whitespace characters are flagged as insufficient.
This threshold is configurable; it cannot establish text quality or detect every
image-only region on a page with otherwise sufficient text. Isolated blank,
image-only or damaged pages leave coverage **partial** while usable text is retained.
Blank and image-only pages cannot be reliably distinguished by text extraction;
neither is claimed as read. Sparse native text remains available with an incomplete
warning even when OCR cannot improve it. Encrypted, missing and invalid PDFs are
reported independently. With no usable summary, the existing note is retained.
Unsupported non-PDF supplements are reported as ignored; notes cannot claim they
were read. Unchanged PDFs are hashed but reuse extraction and summary caches.

### Optional local OCR

To read scanned text, install Tesseract and the required language data in the same
environment as the pipeline, then configure `config.yaml`:

```yaml
ocr:
  enabled: true             # Default false; no OCR dependency needed when disabled.
  executable: tesseract     # PATH command or executable path, without shell arguments.
  language: eng            # Installed languages; multiple languages use eng+ita.
  dpi: 150                 # Render resolution; maximum 600.
  timeout_seconds: 60       # Timeout per OCR subprocess.
  min_text_chars: 10        # Native/OCR sufficiency threshold, excluding whitespace.
```

Only deficient pages are rendered by PyMuPDF and streamed to local Tesseract;
there is no OCR network service or separate extraction pipeline. If the engine or
language data is unavailable, times out, fails or returns insufficient text, the
document stays partial and retains usable text. Original PDF bytes are never modified.
If no page supplies usable text, the summary job is skipped (or fails validation for
a manuscript), its page coverage remains partial, and an existing note is retained.
OCR provides **unverified recognized text**, not figure understanding or verified
equations/tables. Notes identify OCR pages, recognition uncertainty and unprocessed
pages separately from processed sources. All evidence retains its original
attachment/file and PDF page locator.

On WSL use Linux Tesseract in the same distribution as Python; on native Windows
use a Windows executable on PATH or set its local path (quote YAML paths containing
backslashes with single quotes). Execution uses argument lists without a shell;
PNG data passes through stdin, avoiding temporary Windows/WSL filename conversion.

Successful page extractions and validated summaries remain cached. Ordinary `sync`
retries incomplete pages, including blanks, without repeating successful page OCR;
unchanged summary payloads reuse inference. Changing OCR settings invalidates the
extraction contract and dependent document summaries. `--full` bypasses these caches.
Install/fix an unavailable engine and retry ordinary `sync`; no cache deletion is
needed. Start with a small collection to choose a suitable threshold/resolution.

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
"{{vault_path}}/.venv/bin/python" "{{vault_path}}/scripts/research.py" sync --background
```

**Research: Full synchronization**

```bash
"{{vault_path}}/.venv/bin/python" "{{vault_path}}/scripts/research.py" sync --full --background
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
& "{{vault_path}}\.venv\Scripts\python.exe" "{{vault_path}}\scripts\research.py" sync --background
```

**Research: Full synchronization**

```powershell
& "{{vault_path}}\.venv\Scripts\python.exe" "{{vault_path}}\scripts\research.py" sync --full --background
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

### Windows Obsidian → WSL commands (PowerShell)

Select your distribution as WSL's default, or add `--distribution YOUR_DISTRO`
before `--cd` in **every** command. `--cd` accepts the absolute Windows vault path,
and `--exec` runs the Linux virtual-environment Python directly in that directory.
These semantics are documented in [WSL's command help](https://github.com/microsoft/WSL/blob/master/localization/strings/en-US/Resources.resw).
No Windows username, drive or Linux home is embedded in these commands.

**Research: Update knowledge base**

```powershell
wsl.exe --cd "{{vault_path}}" --exec ./.venv/bin/python ./scripts/research.py sync --background
```

**Research: Full synchronization**

```powershell
wsl.exe --cd "{{vault_path}}" --exec ./.venv/bin/python ./scripts/research.py sync --full --background
```

**Research: Apply reviewed changes**

```powershell
wsl.exe --cd "{{vault_path}}" --exec ./.venv/bin/python ./scripts/research.py apply
```

**Research: Check status**

```powershell
wsl.exe --cd "{{vault_path}}" --exec ./.venv/bin/python ./scripts/research.py status
```

For diagnosis, replace the final command with `doctor` or `sync --dry-run`.
WSL `--exec` does not initialize an interactive shell: if you use nvm or a custom
npm prefix, ensure Linux Node and Codex are on the inherited PATH or configure
`codex.executable` to a Linux executable available in that environment. Restart
Obsidian after changes. Codex's subprocess working directory is its private staging
directory inside this vault, matching its `--cd` sandbox boundary.

### Optional lightweight startup synchronization

Create a separate **Research: Startup update** alias using the appropriate sync
command above with `sync --startup --background`. Enable the Shell Commands
**Obsidian starts** event only for that alias. `--startup` processes at most
`processing.startup_max_documents` changed document jobs (default 5), deferring
concept extraction and cross-document reasoning. A normal manual update resumes
remaining work; `--full` is explicit and cannot be combined with `--startup`.
A complete membership/API scan and content hashing still occur, so startup I/O
cost depends on collection size. This is a finite command, not a daemon or watcher.

`--background` returns immediately after spawning the **same Python environment**
with detached stdin/stdout/stderr. The child takes the existing OS vault lock, so
concurrent sync/apply execution is prevented. No community plugin is enabled by
this repository. Keep plugin output in an output panel, never the active note.

`Home.md` shows checkpointed progress and completeness; **Research: Check status**
shows live `.pipeline/progress.json`, the last result and recent background runs.
The launch result identifies `.pipeline/runs/<id>.log` and `<id>.json`; the JSON
record stores the final exit code and full execution report. Background launch
exit 0 confirms launch only; inspect the child report for completion. Abrupt kills
can leave a last-reported running/launching record; they never imply success.
The OS releases the lock when the process exits. Retry ordinary sync for recovery.
Zotero must be open. On WSL, doctor and every sync actually contact the loopback
API and resolve/open/hash attachments; mirrored networking is never presumed to
be working merely because Python runs in WSL. Check the completed report from the
actual Windows Obsidian launch environment before enabling unattended startup.

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
When an evidence quote cannot be found in the retrieved excerpt or current note,
`validation_warnings` records the reason. This is a pending human review, not a sync
failure. Verify scientific support in the actual notes before approving it.

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

To revise a new claim or link review directly in Obsidian, leave `status: pending`
and replace `revision: null` with a complete replacement object: the summary fields
`title`, `summary`, `evidence`, `claims` for a claim, or the link fields `source`,
`target`, `relationship`, `justification`, `evidence`, `target_concept` for a link.
Use source mode for nested objects. Run `sync` to validate the revision and register
a separate pending review. The original review names its replacement and records
`superseded_by` in private state; it cannot apply. Open and approve or reject the new review. A revision never
inherits approval. This also allows a corrected justification with the same evidence.

Original proposal payload fields remain immutable; the separate `revision` field
requests a new proposal. The local registry records their digest and rejects edits
outside the documented decision/revision fields. The LLM cannot set
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

Unverifiable literature or manuscript summaries are retained as clearly marked
`kind: claim` drafts in the existing `Reviews/Revisions/` folder, including the
complete summary, attributed claims, evidence references, per-page processing
methods and validation warnings. The established literature/manuscript note stays
unchanged; a new paper has no established note until review. Drafts never enter
concept extraction, synthesis or approved graph relationships. Source text remains
in private caches/state for validation and revision; pending reviews reuse completed
inference on later runs.

Approve, reject or revise these claim drafts in Obsidian using the same workflow.
`apply` checks unchanged source files, processing identity, destination ownership
and note hashes before publishing the exact approved draft in the managed section.
Published drafts carry their human approval ID; automatic verification warnings
remain in the review history. Rejecting a draft preserves the established note
and does not cause repeated proposals for identical source content.

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

## Initial sync, incremental work and request budgets

The first `sync` processes parents progressively, then reasons across their summaries.
Later runs hash source bytes, process only new/modified/unfinished documents, and
retrieve context for changed knowledge. Successful earlier jobs survive interruptions.
`--full` deliberately bypasses extraction/inference reuse and reprocesses current
sources; it preserves IDs, user additions, approvals and rejection history. Ordinary
retry usually needs only `sync`, not `--full`.

All parameters are optional in existing `config.yaml` files:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `codex.max_input_chars` | 500000 | Total per-request safety budget: instructions, schema, serialized data and expected output reserve |
| `codex.output_reserve_chars` | 16000 | Expected output allowance subtracted before packing input |
| `processing.chunk_chars` | 60000 | Target serialized source size per chunk; request overhead can lower it |
| `processing.graph_neighbors` | 8 | Maximum other candidate notes per changed fragment |
| `processing.graph_fragment_chars` | 12000 | Maximum indexed note-fragment size; request budget can lower it |
| `processing.retrieval_passages` | 4 | Maximum original excerpts added to one reasoning request |
| `processing.passage_chars` | 2000 | Maximum retrieved excerpt length |
| `processing.chunk_context_chars` | 600 | Adjacent source context supplied with a section/math-aware chunk |
| `processing.candidate_terms` | 32 | Maximum rare query terms per registry fragment |
| `processing.candidate_pool` | 128 | Maximum candidate fragments scored per query |
| `processing.startup_max_documents` | 5 | Changed document jobs attempted by lightweight startup |

Character budgets are not token measurements or a guarantee of the model's context
capacity. Set them conservatively for your selected model. Large instruction files
and JSON escaping consume budget too. Too little room for instructions/schema and
source context is blocking; no source text is silently discarded. Each chunk is
validated separately; a consolidation that cannot reduce within the budget fails
explicitly rather than labeling an incomplete summary successful. Output reserve
is an estimate, not a Codex output-token cap. Chunk summaries and exact evidence
are retained so you can audit what the overview consolidated.

A fully completed sync exits **0**, including runs with pending scientific reviews.
Reports separate `completed_operations` (successful inference jobs, including cache
reuse), `completed_documents`, `pending_claim_reviews`, `pending_link_reviews` and
`processing_failures`. Pending reviews do not prevent `last_success` advancing.
A completed attempt with skipped/failed documents
or incomplete reasoning exits **2**, with details in JSON. Blocking configuration,
API, authentication, ownership, concurrent-edit or transaction errors exit **1**;
keyboard interruption exits **130**. Progress events are JSON on stderr; the final
result stays on stdout. `doctor` exits 1 only for blocking errors; attachment and
manuscript warnings do not make it fail. Dry run reports new/modified/unchanged,
removed/processable/skipped parents, blockers and estimated operations without
writing caches or calling inference. Estimates are approximate: consolidation and
future summary fragment counts depend on generated results.

`doctor` reports `ocr` availability/settings and `pdf_pages` with native extraction
coverage; unavailable optional OCR is a warning, not a blocking dependency error.
`sync --dry-run` reports `page_processing` and `partial_candidates`. Both inspect
native text only and **never run OCR**; scanned PDFs with no native text may be
listed as skipped in a dry run even when OCR is configured for the real sync.
Check `ocr.enabled` for the configured setting and `ocr.status` for executable
availability. `doctor` also shows the configuration file it loaded in `config_file`.
Diagnostic page records have `diagnostic_only: true`, `ocr_configured_enabled`
and `ocr_attempted: false`. When OCR is enabled, their reason says it was not run
during diagnostics; this does not mean OCR is disabled for synchronization.
These checks do not verify installed language data or predict OCR quality.
After `sync`, `status` reports actual per-page methods, incomplete flags and reasons
under `attachments.<parent>.<attachment>.page_processing` and `manuscript_processing`.
Generated notes list unprocessed pages as not read; `partial` coverage keeps exit 2.

`status` distinguishes successful, pending, skipped and failed document IDs, retained
errors, pending human approvals, incomplete reasoning and transaction recovery.
The successful-document status is separate from whether an older note exists.
An inaccessible supplement leaves parent coverage partial while allowing current
main-article findings. If no attachment can be summarized, the old note is retained. Removing a parent from a verified collection snapshot
continues to create a removal review rather than deleting a note.

## Commands, status and troubleshooting

All commands accept `--vault PATH` before the command when invoked as a module:

```bash
.venv/bin/python -m research_vault --vault . sync
```

The wrapper script selects its own vault directory, even when called from elsewhere.

| Command | Behavior |
| --- | --- |
| `sync` | Incremental summaries and pending proposals; no approvals applied |
| `sync --background` | Launch a detached finite incremental run; inspect its final report |
| `sync --startup --background` | Bounded startup document jobs; defer concept extraction and reasoning |
| `sync --full` | Reprocess all active papers/available works and reconsider graph context |
| `sync --dry-run` | Inspect metadata, fingerprints and text availability; show operations/skip reasons; no writes or inference |
| `apply --dry-run` | Validate approvals and print affected paths/removal impact; no writes |
| `apply` | Deterministically apply validated user decisions |
| `status` | Report counts, last success, warnings and pending transaction status |
| `doctor` | Check config, packages, Zotero snapshot, native PDF page coverage, OCR availability, source authorization and both LLM runtimes' versions/login/flags |

`Home.md` shows last successful synchronization, processed/active papers, pending
links/removals/revisions, warnings and errors. Edit only below the generated status
section. Normal sync/apply commands take a nonblocking OS file lock. Read-only
status/doctor/dry-run commands may inspect a changing vault; dry runs refuse an
unrecovered journal. No command requires an active VS Code session.

Inspect `.pipeline/logs/events.jsonl` for execution outcomes and
`.pipeline/staging/<run-id>/stderr.txt` for a failed inference call. Staging also contains
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
| Missing PDF / OneDrive placeholder | Download/keep locally, check linked paths and retry; existing notes remain, membership is still recorded |
| Scanned / mixed PDF or insufficient page text | Enable optional local OCR; inspect per-page coverage, engine/language settings and warnings; usable pages remain available |
| Encrypted PDF | Supply an accessible unencrypted PDF; decryption is outside this pipeline |
| Inaccessible manuscript/include | Check source_roots, path spelling, permissions and recursive includes |
| Codex not found from Obsidian | Restart Obsidian to refresh PATH or set codex.executable to its local absolute path |
| Login/model/usage error | Run codex login/status in a terminal, select an available model and inspect limits |
| Claude missing/authentication failure | Install Claude Code and run `claude auth login` / `claude auth status` in the same environment as Python; check `claude.executable` |
| Claude required isolation flags unavailable | Update Claude Code; `doctor` names missing flags. The adapter refuses to weaken its restrictions |
| Invalid provider/role/model settings | Use `codex` or `claude`, supported role names, and a model available to the selected provider |
| Missing Codex flags | Update Codex; no insecure compatibility fallback is used |
| Invalid agent JSON, identifiers or nonexistent evidence source/locator | Inspect staging response; retry the failed job; completed papers and approvals are retained |
| Quote cannot be verified at a valid source/note | Review the unverified claim draft or pending link and its validation warnings in Obsidian; sync can still succeed |
| Input budget / consolidation failure | Check instructions and output reserve fit the configured budget; chunks remain cached. Adjust chunk sizing or model capacity; no scientific text is silently dropped |
| Windows URL under WSL | Doctor shows the original URL and translated path; check wslpath, Windows mounts and file availability |
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
Run the synthetic tests, doctor and a dry run before normal sync. Relevant instruction/algorithm changes invalidate their own stage contracts; changed
downstream scientific inputs are reconsidered.
State-schema changes must have an explicit migration; incompatible schemas fail.
Avoid a blind reset, copying an upstream Home.md over your dashboard or force-adding
ignored research/configuration files.

### Updating an existing vault to the incremental pipeline

1. Stop pipeline processes and back up the **entire vault including ignored**
   `.pipeline`, notes, review files and local configuration. Complete an old pending
   transaction with the old code before changing versions when possible.
2. Update `src/`, `scripts/`, `agents/`, examples, tests and documentation. Preserve
   your `config.yaml`, `works.yaml`, `.pipeline/state.json`, generated notes, user
   additions, review files and Home dashboard markers. Reinstall the editable
   package with the same platform's Python (`python -m pip install -e '.[dev]'`).
3. Merge desired optional limits from `config.example.yaml`; defaults work with old
   configurations. State schema **1** remains readable: attachment coverage and the persistent
   concept registry are added during normal sync. Include the new
   `agents/concepts.md` and all stage/version modules when updating. No notes or reviews are deleted or renamed.
4. Run synthetic tests, `doctor`, `sync --dry-run`, then ordinary `sync`. The new stage contracts/schema intentionally require a one-time managed-summary
   regeneration and concept registry build. Later instruction changes invalidate
   only their relevant stages and changed downstream inputs. Each completed parent is
   durable, and rerunning ordinary sync resumes interruptions. Do not delete state
   to force migration and do not use `--full` merely to retry.
5. Re-review approvals whose source notes changed. Approved/rejected review files
   remain intact; pending reviews can refresh expected hashes. Apply continues to
   refuse stale approvals; reset them to pending and sync before approving again.

For a native Windows → WSL move, replace the Windows virtual environment with a
new Linux environment after backing it up, sign into Linux Codex, and use the WSL
Shell Commands configuration. Translate/update manuscript roots and include paths
in local configuration as necessary. Old absolute manuscript include paths are
reauthorized before reuse; doctor identifies path problems. Never share one venv
between Windows and Linux. Do not change the Zotero collection/library identity.

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
mock HTTP responses and mock agent execution; no Zotero account, live LLM session or
inference bill is required. A temporary local Git repository tests deletion and
backup behavior. CI covers Python 3.11/3.13 on Linux and Windows. Local validation
and remaining live checks are recorded in [docs/validation.md](docs/validation.md).

The remaining limits include unverified OCR text and no figure understanding, non-PDF supplement
parser, full TeX engine, automatic prose merge or semantic proof checker. Changed
parent papers are processed with bounded chunks and cached operations; unchanged
papers reuse successful results. Retrieval is approximate and does not guarantee
all conceptual relationships. Scientific accuracy still requires source review.
Caches and staging currently accumulate; back them up privately and monitor disk
usage. Removing only cache/staging files when no run is active is safe but forces
more extraction/inference; never remove state, reviews or a transaction journal.

Live Zotero version behavior, actual Codex/Claude structured responses and native Windows
sandbox/npm setup, Obsidian plugin events and graph UI need real-world integration
testing. These are not simulated as successful integrations by the tests. See the
[integration checklist](docs/validation.md) before relying on a research project.
