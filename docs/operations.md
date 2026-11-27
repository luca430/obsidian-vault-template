# Transactions, backups and recovery

## LLM execution boundary

`runtimes.LLMRuntime` defines the provider-independent execution contract.
`LLMRunner` selects the configured adapter per role, and both `Codex` and
`ClaudeCode` return the unchanged scientific output dictionaries. Python owns
schema validation, provenance checking, review generation and apply decisions.
Role instructions are shared files, not provider-specific copies.

`llm.default_provider` defaults to Codex; `llm.agents` overrides individual roles.
`sync --provider` overrides primary providers for that process, including detached
children. It never edits configuration. Per-role model overrides do not cross
provider boundaries. Authentication and required safety-flag checks occur before
work requiring the configured runtimes; failure does not select another provider.
Optional evaluation is observational and isolated from proposal integration.

Existing Codex stage hashes/cache envelopes remain accepted. New cache envelopes
include separately hashed metadata with provider, requested model, CLI version,
instruction versions, validation status, warnings and available usage. Legacy
entries cannot supply historical CLI versions or usage. Scientific source
extraction remains independent of provider, model, and inference instructions.
Each new inference recipe includes the resolved model (when reported), effort,
prompt version and effective agent settings. A separate content identity pins
successful results: changing runtime settings does not regenerate existing
scientific notes. New inputs, changed scientific instructions/processing recipes,
or explicit full reprocessing use the new profile. A one-time recipe-policy rebase
retains successful existing notes and compatible legacy caches.

### Agent profiles and provider validation

Configuration precedence is provider settings, `llm.defaults`, then
`llm.agents.<role>`. Each role accepts provider, model, reasoning_effort,
max_input_chars, output_reserve_chars, timeout_seconds and retry_policy
(`max_attempts`, `retry_after_seconds`). Claude additionally accepts
`max_output_tokens`. A run-only provider override does not transfer an incompatible
agent model to the other provider. Consolidation inherits linking unless overridden.
The plugin settings page exposes provider, model and reasoning-intensity dropdowns
for each agent after **Load agent settings**. The Agent Settings command opens
the same editor in a dialog. CLI login-status commands enable provider selection;
Save rechecks login for changed effective profiles, without blocking unchanged
profiles using a currently signed-out provider. Failed auth output is not exposed.
Refresh preserves staged choices; no login flow, polling or inference is started.
Codex model metadata supplies model-specific intensity options; configured catalogs
take precedence. Claude's documented Sonnet/Opus aliases expose conservative common
efforts, while Haiku uses default/inherited effort unless a catalog declares its
capabilities. Aliases do not certify account entitlement or the resolved version.
Provider switches visibly reset the affected model and effort to CLI defaults;
models from the previous provider are not transferred. Unsupported saved choices
remain visible until corrected, and no replacement model is silently selected.
The editor stages all changes until explicit Save;
Python validates and atomically saves only the `llm` mapping under the vault lock,
rejecting concurrent edits. It starts no inference or synchronization.

Codex translates effort to `-c model_reasoning_effort=...`; Claude uses `--effort`
and `CLAUDE_CODE_MAX_OUTPUT_TOKENS`. Codex exec has no supported native output-token
cap in this adapter, so that setting is rejected for Codex; output_reserve_chars
remains an estimate for request budgeting. Character counts are not token counts.
Claude's limits are subject to provider/model caps. Timeout applies to each CLI
request, and retries happen only during eligible subsequent synchronization.
Claude CLI transport retries and structured-output loops are bounded; automatic
usage-limit continuation, watchdog retries and fallback models are disabled.
See the official [Claude CLI reference](https://code.claude.com/docs/en/cli-reference),
[environment variables](https://code.claude.com/docs/en/env-vars),
[settings reference](https://code.claude.com/docs/en/settings-reference), and
[Codex configuration reference](https://developers.openai.com/codex/config-reference/).

Malformed settings and provider-incompatible effort names fail before inference.
An optional `llm.model_catalog` maps providers to allowed exact model IDs and
`reasoning_efforts` (plus optional max_output_tokens ceiling); it is a locally
maintained capability allowlist. Known Codex effort capabilities also use its
local model metadata. Without a catalog, account access and unknown model support
are checked by actual provider responses, without speculative validation requests.
Explicit full Claude model substitutions are rejected. CLI aliases/defaults resolve
according to the provider; an unreported actual model remains unknown in receipts.
No replacement model/provider is selected after failure. Host-managed provider
policies and account entitlements cannot be certified offline.

### Task receipts and resumption

Each operation has an atomic JSON receipt under `.pipeline/operations/`. Extraction,
document integration and inference track pending, running, completed, failed and
deferred states. Validated content-addressed outputs precede completed receipts;
normal recovery resets orphaned running operations and validates saved output
before reuse. A crash between cache publication and completion reuses that output.
Corrupt/incomplete cache entries recompute only their affected operation. Scientific
note integration still uses the existing transaction journal and ownership checks.

Provider allowance exhaustion stops further inference requests to that provider
for the run, persists completed work and defers unfinished tasks. Another provider
may continue its configured tasks. Status records the interruption and instructions:
restore the allowance, then run ordinary Sync or allow the next startup Sync.
No `--full`, polling, timer or background worker is needed. Temporary rate limits
have a distinct error and eligible-after time (provider Retry-After or configured
retry_after_seconds). Authentication, unavailable model/effort, context exhaustion,
and invalid/incomplete output remain separate diagnostics. Correct the cause and
resume; `sync --retry` explicitly overrides exhausted retry ceilings/cooldowns.
Scientific tasks never silently use a cheaper model on exhaustion.

Startup has a finite new-inference budget (`startup_max_operations`, default 20)
in addition to its document budget. Deferred graph operations resume on subsequent
startup or manual Sync. Diagnostics include per-agent requests, cache hits,
failures, tokens when reported, deferred operations, and persistent task counts.
Cached historical token usage is never counted as a new request's usage.

### Agent audit

All surviving LLM roles need scientific or semantic judgment. There are no
formatting-only agents to downgrade automatically. Explicit low-effort/lightweight
profiles are available when the user chooses a suitable model; scientific roles
should use models and effort suitable for their evidence and mathematics.

| Work | Execution |
| --- | --- |
| Zotero metadata, identity, filenames and bibliographies | Deterministic Python |
| Attachment rules, exact duplicate checks, chunking and retrieval scoring | Deterministic Python |
| State, receipts, cache validation, schema/provenance validation | Deterministic Python |
| Rendering, review deduplication, review application and approvals | Deterministic Python; exact human decisions govern integration |
| Literature/manuscript extraction and hierarchical scientific consolidation | LLM literature/manuscript roles; scientific claims and uncertainty |
| Concept extraction and conceptual relationships | LLM concepts/linking roles; semantic evidence assessment |
| Synthesis and personal-note revision proposals | LLM synthesis/maintenance roles; advisory scientific reasoning |
| Explicit concept consolidation comparison | LLM consolidation role; semantic equivalence is never inferred from names |

`operations.jsonl` records domain validation and cached reuse; `inference.jsonl`
records transport outcomes. A schema-valid transport result can subsequently fail
provenance validation, which is recorded as a failed operation rather than cached
as valid. Cached usage is historical, not additional inference consumption.
Evaluation responses have their own namespace and never become authoritative
claims, proposals or approvals.

Claude's tool-less execution is an application restriction, not a portable OS
sandbox. Native CLI/Node resolution never invokes a command shell. External
settings, integrations and executable hooks are excluded using the required CLI
profile; organization-managed hooks that override local settings are unsupported.
The installed CLI and administrator-managed host policy remain trust boundaries.
Both CLIs' live inference, native Windows and WSL need environment-specific checks;
mocked regression tests cannot certify a provider installation or account/model.

## Failed synchronization

Source/LLM failures are isolated to their attachment/document. Available main text
is summarized even if supplements are unavailable. Parent coverage is partial until
all selected supported attachments and consolidation succeed. Attachments record
metadata_only/partial/complete/failed independently of parent coverage and job
status. With no usable current result, the existing note stays intact. Never infer
full-text claims from metadata. Each attachment checkpoint and usable parent note
commits through the existing journal. Transient inference failures retry up to `processing.max_retry_attempts`; persistent coverage limitations wait for changed inputs/settings or `sync --retry`. A complete Zotero membership snapshot is saved independently
of source accessibility, so unavailable attachments never become removals.
`last_success` advances only if all jobs and conceptual reasoning finish; exit 2
means partial completion. Invalid configuration, API/authentication failure,
ownership/concurrent-edit conflicts and transaction errors remain blocking.

Automatic quote matching is distinct from structural and source-provenance
validation. A nonempty quote that cannot be located at an existing processed source
and locator, or in an existing supplied note, creates a validation warning with
its evidence reference and failure reason. It does not fail execution. Unknown
sources/locators, unread pages, missing notes, invalid identifiers, malformed JSON
and forbidden agent fields still fail. No provenance check is waived.

Unverifiable summaries become `kind: claim` artifacts in `Reviews/Revisions/`;
existing established notes stay intact. No draft content enters concept indexing
or reasoning. Uncertain links stay in `Reviews/Links/`, with justification,
evidence and warnings. Both queues accept approved/rejected status decisions and
a separate `revision` object in Obsidian. Sync validates revisions and registers
new pending proposals without inheriting approval; apply alone integrates the
exact reviewed replacement. Source hashes, ownership and stale-note checks still
apply. Rejections persist through incremental and full syncs.

Review Readiness validates active reviews independently and reports all blockers,
dependency diffs and concept candidates. New note snapshots live in private state.
Link dependencies ignore only recognized generated graph lines, YAML presentation
and display-only cssclasses; scientific prose, mathematics, identity, provenance,
aliases and user additions remain dependencies. Legacy matching hashes can gain
snapshots; stale legacy hashes are never silently rebased. Targeted `review refresh`
registers a new pending link/claim version without inference or a collection sync,
and supersedes the old review without copying its decision. A changed original
claim source still requires source synchronization.

An exact existing typed source/target relationship is a successful Refresh no-op
for scientific notes. Python records a protected already_represented resolution,
preserves status/payload/applied=false, and removes that obsolete review from
active counts/readiness while retaining it in history. A generic reference or a
different relationship type cannot resolve it. Alternative concept definition
text remains active and opens consolidation comparison. Resolution markers are
integrity checked, and a resolved identity does not prevent a later newly pending
proposal if the relationship disappears and is proposed again.

Read-only apply previews return complete readiness and diffs. Default mutation
remains strict; explicit proposal IDs or `--ready` opt into independent ready work.
The GUI obtains a selected preview and sends only those IDs and its plan token on
confirmation. Revalidation, current decisions, concurrency checks, removal backup
and ownership validation run again before commit. Interacting definition changes
and deletions require separate phases. A preview token cannot authorize a new or
changed decision; it only requires an unchanged set of output changes.

Concept candidates compare existing notes and pending/approved creation proposals.
Title/alias similarity is a heuristic, never equivalence. Explicit member selection
supports differently named concepts. The consolidation role inherits linking's
runtime profile unless explicitly configured and can generate a
bounded merge/keep-separate/relate suggestion only on explicit request. Python
validates and registers a pending consolidation under Reviews/Concepts. Source
quotes, prior names, source evidence and typed relationships must be preserved.
Merge previews enumerate definitions and link impacts. Canonical IDs stay stable;
alternative notes retain original text and additions as concept-redirect records.
Only generated owned links are rewritten; other references retain their old
targets and work through redirects. The retrieval registry records only actual
rewrites and excludes retired concept notes. Scoped keep-separate decisions survive
creation of the compared notes but expire when definitions/scope/aliases change.
First registering a consolidation uses state schema 2 so older code refuses it.
This release reads both schemas; installing code never applies or refreshes live
reviews automatically.

`completed_operations` counts validated inference jobs, including cached reuse;
`completed_documents` counts documents retained as summaries or review drafts.
`pending_claim_reviews`, `pending_link_reviews` and `processing_failures` are
reported separately. Pending scientific reviews permit exit 0 and advance
`last_success` when all technical processing completes. Incomplete extraction,
missing sources and actual processing errors retain the existing incomplete status.

Validated results use distinct content-addressed cache namespaces under
`.pipeline/cache/`:

| Stage | Namespace | Validity |
| --- | --- | --- |
| Source attachment/page extraction | `extractions` | Attachment SHA256, source key, page number (page records), PDF extractor implementation/version, PyMuPDF version and OCR settings |
| Individual document summaries / consolidation | `document_summaries` | Exact source/chunk or summary payload, own role/global instructions, provider/model configuration, summary/chunking contract and upstream extraction version |
| Concept extraction | `concept_extraction` | Canonical scientific note fragment, own role/global instructions, concept schema/contract and provider/model configuration |
| Relationship proposals | `relationship_proposals` | Exact retrieved notes/concepts/passages/coverage, own role/global instructions, relationship schema/contract and provider/model configuration |
| High-level synthesis | `high_level_synthesis` | Exact bounded reasoning context, synthesis instructions/schema/contract and provider/model configuration |
| Maintenance | `maintenance` | Exact bounded context, maintenance instructions/schema/contract and provider/model configuration |
| Optional second-provider comparison | `evaluations` | Secondary provider's own role recipe and exact primary request; results cannot enter the approval queue |

Pure text extraction has no LLM/model or role-instruction dependency. Changing a
synthesis instruction leaves extraction, summaries and concept results valid.
Changed scientific inputs appropriately invalidate dependent downstream results.
`recipes.py` contains independent algorithm versions: implementation changes must
bump the affected contract, rather than hashing unrelated repository files into
every stage. A PDF extractor implementation/version change propagates to summaries.
Source bytes are hashed every snapshot and checked around processing. Invalid
responses are not cached as successful; validated chunks and attachment summaries
survive a later failure. Failed parent consolidation retains the detailed claims
with partial coverage. Original page/file records are cached for bounded retrieval.
PDFs retain per-page `text`/`ocr`/`unprocessed` records with original locators,
incomplete flags and failure reasons. Native extraction runs first; optional local
Tesseract processes only pages below the configured non-whitespace threshold.
Successful pages are reusable; partial document/page cache entries are recorded
and retried with changed inputs/settings or `sync --retry`. This allows an engine/language-data repair to recover missing
pages without repeating successful page OCR. Partial text does not qualify as full
coverage. Blank pages remain unprocessed rather than being claimed as read.
Only usable text enters summary evidence or passage retrieval; missing-page
coverage metadata accompanies summary requests. With no usable source text, no
summary is fabricated and the previous note survives. Sparse native text is kept
even if OCR fails. OCR is uncertain text recognition, not figure understanding.

`ocr.enabled` defaults to false. The local executable, language, resolution,
per-page timeout and sufficiency threshold are configurable (see config.example.yaml).
Changing OCR settings invalidates extraction and dependent document summaries.
`doctor` and `sync --dry-run` inspect native text without OCR or cache writes;
engine availability does not establish installed language data or recognition quality.
`status` retains actual per-page coverage for attachments and PDF manuscripts.
Use Linux Tesseract with WSL Python; PNG stdin avoids cross-OS temporary file paths.

The private persistent concept registry lives inside state, with source annotations,
exact note evidence, coverage/fingerprints, fragment postings and separately reviewed
relationship history. Source associations are provisional retrieval data, not public
graph edges. Updates remove obsolete postings/associations and add only changed
records; registry checkpoints batch inexpensive index updates while every expensive
validated concept call is immediately cached. Interrupted indexing retries from these
caches. Candidate scoring is bounded by query terms and a candidate pool, not a
comparison of every document pair. A non-retrieved candidate is scientifically
undetermined. All new public relationships still require the existing pending review
and exact user approval through apply.

All mutating CLI commands hold an OS lock in `.pipeline/run.lock`. The presence of
that file alone does not mean a process is running. The lock is released when the
process exits; never “unlock” a live process by deleting the path.

At integration, `storage.commit` first copies all prior bytes to
`.pipeline/rollback/` and atomically writes `.pipeline/transaction.json`. It then
replaces each destination atomically. Removing the journal is the commit point.
An ordinary exception rolls the batch back immediately. After abrupt termination,
the next normal `sync`/`apply` restores every pre-transaction file, including state,
before doing any new work. Do not delete the journal or rollback directory.
Status reports an outstanding journal; dry runs refuse it. Read-only status during
an active write may observe an interim state; wait and run it again.

Source bytes are hashed and rechecked after extraction/inference. Before sync
integration, the pipeline also verifies that notes, existing reviews and Home were
not edited during reasoning. If they changed, the user edit wins and the current
batch is not committed; previously completed batches remain intact. This is a conflict guard, not a lock on Obsidian; avoid editing
notes while a short `apply` batch writes them.

A filesystem/OS crash affecting the storage medium itself cannot be fully guarded
against by an application journal. Keep an independent backup. Only one machine
should run this vault at a time; OS locks are local and cloud file-sync systems
are not distributed transaction managers.

## Deletion checkpoints

Deletion requires an explicitly approved removal decision, an unchanged reviewed
note and unchanged link-impact preview, a Git executable, a committed repository
rooted at the vault, and no tracked modifications. Generated data is normally
ignored, so the actual checkpoint is a verified ZIP with SHA256 manifest. The
checkpoint includes old bytes for every changed file and records which destination
paths did not previously exist. State and Home are included. The backup path is
returned in the apply result. `.pipeline/last-impact.json` records the planned impact.

Before deletion use `apply --dry-run`, review incoming/outgoing relationships and
save/commit tracked changes. The Python code cleans only generated links regions.
Personal notes and user sections remain intact even if they now have a broken
link; revision records ask the user to reconsider the relationship and scientific
claim. Do not enable Obsidian's automatic link rewriting during this workflow.

## Restore a deletion backup

Stop pipeline commands and close Obsidian before restoring. Make an independent
copy of the entire current vault, including ignored data. Restore only a backup
created by this vault; it contains state/history specific to the project. Restoring
a checkpoint intentionally replaces later versions of its affected files, so first
inspect the ZIP's MANIFEST.json and the current files.

Use the included script from the project root:

```bash
.venv/bin/python scripts/restore_backup.py .pipeline/backups/BACKUP_ID.zip
```

PowerShell:

```powershell
& .\.venv\Scripts\python.exe scripts\restore_backup.py .pipeline\backups\BACKUP_ID.zip
```

The script checks archive paths and hashes, takes the same pipeline lock, recovers
an unfinished transaction if needed, and restores the checkpoint through the same
write-ahead transaction mechanism. Files listed as previously absent are removed.
The backup archive itself is retained. It never executes archive contents or Git
commands, and refuses a checkpoint from another project identity.

After restoration, review statuses are restored too. In particular a removal file
may again say approved/delete. **Change that decision to pending or rejected before
running apply again.** Open Obsidian, inspect the restored note, then run status
and sync. Restoring state is essential: restoring only the literature Markdown
would leave approval/history inconsistent with the deleted/active identity.

## Working on the code

Install with `pip install -e '.[dev]'`. The package is editable, so updates to src
are used by the CLI immediately. Role instruction changes, code changes and LLM runtime
settings invalidate the relevant stage contracts and changed downstream inputs. Note layouts in
Templates are documentation/manual reference; automatic summaries use the stable
managed-section renderer in notes.py. Keep marker contracts backward compatible
or provide a state/note migration. The current state schema is 1.

The installed Python package is not a standalone vault initializer: scripts and
agent rules must remain in the cloned vault. Use GitHub's template feature for a
new project, not an arbitrary empty working directory with only the wheel installed.

## State and cache migration

State schema remains 1. Old records without explicit job status are interpreted compatibly until
reevaluated. The first updated sync rebuilds managed summaries with structured
claim provenance because its summary schema/contracts changed, adds attachment
coverage and builds the persistent concept registry. Include agents/concepts.md
and the new implementation modules when updating. Later unrelated stage changes
do not force a whole-document rebuild. IDs, user regions,
review payloads and approval/rejection decisions are preserved. A recovery journal
always rolls back only its own current batch; it does not erase earlier document
checkpoints. After a process dies, pending documents and unfinished reasoning resume
from state and validated caches. See the README migration checklist before updating.

Caches/staging are private source-bearing runtime data, ignored by Git. They grow
without automatic garbage collection. While no run is active, clearing only caches
and staging is non-destructive to notes/review history, but loses expensive reuse.
Missing passage caches reduce retrieval context until the relevant source is
processed again; never claim uncached passages were read. Keep state, journals,
backups and review files. No recovery requires broadening inference permissions.

## Native Obsidian plugin and optional detached CLI execution

The bundled Research Vault plugin launches foreground CLI processes asynchronously
with Node spawn and no shell. It does not use `--background`: keeping the finite
child attached supplies streaming progress and its real exit code without blocking
Obsidian. Native mode selects the project venv; Windows-to-WSL mode translates the
vault path with wslpath and runs the Linux venv under wsl.exe in that directory.
Interactive shell profiles are not loaded. See the README's
[Obsidian setup](../README.md#running-the-pipeline-from-obsidian) for PATH and updates.

The plugin performs no scientific processing or direct note/approval/state writes.
Apply Reviews first runs Python `apply --dry-run`, displays its paths/impacts for
confirmation, then invokes `apply`. A cancelled, failed or oversized preview
cannot apply. Python remains the authority for approved decisions, stale evidence,
ownership, impact, clean Git and verified backups. The UI guard prevents duplicate
commands in one app; Python's existing lock covers other windows/terminal runs.
Automatic startup sync is opt in, once after workspace readiness, and uses only
`sync --startup`. No failed or potentially expensive run is retried automatically.

Disabling the plugin allows an already launched finite process to finish; it does
not kill a transaction or only the WSL launcher. Closing Obsidian/OS termination
may still interrupt work. Python's existing recovery/checkpoints apply on the next
ordinary sync/apply. UI logs are bounded and best-effort redacted; full reports
remain in the private pipeline logs. No persistent polling/watcher is installed.

The following detached operations remain available independently from a terminal
or another explicitly configured launcher:

Use `sync --background` for the manual incremental command and
`sync --full --background` for an explicitly requested full run. The launcher
returns promptly and starts the same interpreter/environment with detached I/O;
there is no daemon, watcher or interactive shell. Each child takes the same OS
vault lock as foreground sync/apply. Concurrent launch races may start a losing
child that reports a lock error, but no concurrent synchronization can execute.
The parent never overwrites a fast child's final report.

An external launcher can use `sync --startup --background` for its startup event.
Startup caps document jobs and new inference requests; subsequent manual/startup
sync resumes deferred document and graph operations. It still needs a complete API snapshot and source hashing. WSL runs
validate the actual loopback endpoint and local attachment accessibility, independent
of whether mirrored networking was configured correctly.

Home's managed dashboard shows checkpointed counts/coverage and stage. Status
includes `.pipeline/progress.json`, the last synchronization result and recent
`.pipeline/runs/<id>.json` records. Each run log/report remains private. Launcher
exit 0 means launched, not scientifically complete; inspect the child report's
exit code/result. The final child report distinguishes complete, incomplete and
failed. Abrupt termination can leave a last-reported running/launching record;
it never authorizes success or requires deleting a lock file. Retry ordinary sync.

## Cancellation, migration and finalized-review recovery

The plugin passes a random `--control-id` to each sync. Its Interrupt action writes
only `.pipeline/cancel/<control-id>`; Python polls that marker at safe boundaries
and while waiting for an inference/OCR subprocess. It never kills the WSL launcher
or interrupts Apply Reviews. An acknowledged interruption records status `failed`,
retains `last_success`, and exits 130. Completed documents, valid chunks, concept
indexing and role-specific reasoning batches remain durable. The next Sync resumes
unfinished operations. Native text extraction/API calls reach their next boundary
or timeout first. A process forcibly killed by the OS cannot always record its final
status; recover the transaction on the next mutating run. Do not delete lock/state files.

Dry-run synchronization never authenticates, runs OCR/inference, writes caches or
updates research notes/state. It inspects native text and reports limitations and
approximate operation estimates. Plugin cancellation uses a transient marker even
for a dry run; its UI outcome is Failed and that marker is removed on completion.

Source byte signatures and summary recipes control document work. Canonical scientific
note fingerprints independently control concept extraction and graph reasoning.
Presentation headers, bibliographic JSON, processing reports and Python-owned link
sections are excluded. Completed reasoning-role batches have a scientific-input key
separate from proposal history; changing review status or adding graph bookkeeping
does not invalidate those operations. `sync --full` bypasses reuse deliberately.
Retries are bounded for unchanged temporary failures; use `--retry` after repairing
an external dependency or to retry persistent limitations. Missing/unread pages
always retain explicit incomplete coverage.

`migrate` previews existing key/title filenames, bibliography and finalized reviews.
It uses a complete Zotero snapshot plus stored metadata for retained absent items,
and performs no PDF extraction or inference. Back up the complete private vault,
inspect `diffs`, `renames` and `user_note_previews`, then execute the unchanged plan
with `migrate --execute --plan-token TOKEN`. User-authored reference changes require
explicit repeated `--authorize-note PATH` on BOTH preview and execution. By default
those references remain intact and old paths/keys become aliases. No destination
collision or missing ownership marker is bypassed. Missing bibliographic metadata
is reported and that item is retained. Original proposal records and Markdown are
saved verbatim under `.pipeline/migration_history/`; migrated payload paths and
hashes are updated without reapproving scientific relationships or rebasing stale science.

Review archives live under `.pipeline/review_history/approved`, `rejected`,
`superseded` and `resolved`. Applied approvals and finalized closures move with the
registry/application transaction. Unapplied, stale or conflicting approvals remain
in Reviews. Archive Markdown retains the original decision/payload/timestamp;
registry records retain application outcome/time and archive time. A failed move
rolls back the application and registry update together. Restore archived reviews
alongside state from the SAME backup; never move files manually to resurrect approvals.
Use `review history [ID]` or the plugin history picker for full records. Reconsidering
a rejected link/revision requires the explicit `review reconsider` command and a new
verified supporting excerpt, which creates a separately pending proposal.

Statuses separate technical completion, coverage and human decisions. Persisted
`status` values are `up_to_date`, `completed_reviews_pending`,
`completed_with_limitations`, `incomplete_resumable`, and `failed`.
`technical_complete`, `source_coverage_complete`, `human_reviews_pending` and the
outstanding-error/limitation maps provide the independent dimensions. Legacy
`complete` remains false for incomplete source coverage. The GUI displays the
specific status even when legacy CLI exit 2 indicates retained limitations.

Migration and archive failures use the same write-ahead rollback mechanism as note
application. On power loss, the next mutating command recovers before planning.
Synthetic fault-injection tests cover rollback; native Windows/WSL process behavior
and live provider installations still need environment-specific verification.
