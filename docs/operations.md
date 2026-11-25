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
Changed provider/model invalidates the affected inference stages; returning to an
earlier provider can reuse its original results. No state schema migration occurs.

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
commits through the existing journal, and missing/failed work retries next sync. A complete Zotero membership snapshot is saved independently
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
| Concept extraction | `concept_extraction` | Exact note fragment, source provenance/completeness, own role/global instructions, concept schema/contract and provider/model configuration |
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
but retried next sync. This allows an engine/language-data repair to recover missing
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

## Detached Obsidian execution

Use `sync --background` for the manual incremental command and
`sync --full --background` for an explicitly requested full run. The launcher
returns promptly and starts the same interpreter/environment with detached I/O;
there is no daemon, watcher or interactive shell. Each child takes the same OS
vault lock as foreground sync/apply. Concurrent launch races may start a losing
child that reports a lock error, but no concurrent synchronization can execute.
The parent never overwrites a fast child's final report.

Use a separate `sync --startup --background` alias for the Obsidian-start event.
Startup caps document jobs and defers concept extraction/reasoning; manual sync
resumes them. It still needs a complete API snapshot and source hashing. WSL runs
validate the actual loopback endpoint and local attachment accessibility, independent
of whether mirrored networking was configured correctly.

Home's managed dashboard shows checkpointed counts/coverage and stage. Status
includes `.pipeline/progress.json`, the last synchronization result and recent
`.pipeline/runs/<id>.json` records. Each run log/report remains private. Launcher
exit 0 means launched, not scientifically complete; inspect the child report's
exit code/result. The final child report distinguishes complete, incomplete and
failed. Abrupt termination can leave a last-reported running/launching record;
it never authorizes success or requires deleting a lock file. Retry ordinary sync.
