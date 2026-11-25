# Validation record and live integration checklist

## Verified in this workspace

Multiple-runtime follow-up (2026-11-25): the full synthetic suite passed **205 tests**
in 180.65 seconds, including 40 new runtime regression cases. Coverage includes
Codex and Claude Code adapters, mixed-role providers/models, run-only and detached
provider overrides, shared instructions, structured-output normalization, scientific
review warnings, approval safeguards, fabricated provenance, legacy cache keys and
envelopes, provider switches reusing extraction, historical usage metadata, metadata
integrity, optional isolated evaluation, authentication/errors/timeouts, doctor
diagnostics, native Windows npm shims and WSL executable rejection. Changed prompt
budgets also exposed a retrieval deduplication bug: fragment offsets now distinguish
repeated scientific text, and the existing full-character coverage regression passes.
Ruff lint/format, compilation, diff checks, example configuration validation, CLI
help and a wheel build without build isolation passed; the wheel includes runtimes.py.

Provider execution/authentication is mocked. Claude Code is not installed in this
workspace; no real backend inference or account/model entitlement was verified.
Native Windows/WSL CLI execution still needs live integration checks. Doctor can
verify installation/flags/login without proving model access or scientific validity.
Claude's tool restrictions are not an OS filesystem sandbox; organization-managed
executable hooks that override local settings are unsupported. Runtime/model
defaults can change externally; resolved identities/usage unavailable from a CLI
remain unknown, and legacy cache entries cannot reconstruct historical usage.

Scientific review follow-up (2026-11-25): the full synthetic suite passed **165 tests**
in 167.16 seconds, including 28 new scientific-review regression cases. These cover
unverifiable claims and links, isolation from established notes and reasoning,
consolidation warnings, cached uncertainty, Obsidian revisions, explicit approvals,
rejections, stale sources, conflicting approvals, malformed outputs and missing
provenance. A CLI regression confirms pending scientific reviews exit successfully.
Ruff lint/format checks, compilation, diff checks and wrapper CLI help also passed.
Sources and inference are synthetic/mocked; live Obsidian and Zotero execution
remain integration checks.

PDF robustness follow-up (2026-11-24): the full synthetic suite passed **137 tests**
in 427.43 seconds, including 29 PDF/OCR regression cases. Ruff lint/format checks,
compilation and wrapper CLI help also passed. Coverage includes fully scanned and
mixed native/image PDFs, blanks, sparse text, individual page extraction errors,
missing engines/language data, empty OCR output, timeouts, nonzero exits,
interrupted page-cache recovery, partial-to-complete retries, unread-page exclusion
from evidence/retrieval, manuscript coverage and diagnostics without OCR or writes.
Tesseract is not installed in this workspace; OCR engine responses and executable
resolution are mocked, while PyMuPDF extraction and rasterization use synthetic PDFs.
Actual Tesseract recognition and native Windows/WSL engine execution remain live checks.

Earlier baseline validation:

Validated on Linux under a WSL2 kernel with Python 3.12.8 using synthetic sources
and mocked services (2026-11-24):

- `.venv/bin/python -m pytest -q`: **108 passed** in 163.88 seconds.
- `ruff check src scripts tests`: passed.
- `ruff format --check src scripts tests`: passed.
- `python -m compileall -q src scripts tests`: passed.
- `python -m build --no-isolation`: wheel and source distribution built.
- `python -m pip check`: no broken requirements.
- Module, console entry point and wrapper help commands: passed.

The tests cover empty collections; adds/updates; stable parent grouping and distinct
parents with identical filenames; multiple PDFs and supplementary byte changes;
complete/paginated snapshots and failed/truncated responses; removals, flag/keep
and explicitly approved deletion; verified backup and restoration; preservation of
personal notes; approval/rejection, tampered/stale decisions and evidence-order
idempotence; unavailable/invalid agent output; manuscript PDF/LaTeX/include changes,
missing originals and revoked source authorization; unchanged projects and dry
runs avoiding inference; source evidence validation; citation records not creating
links; internal Markdown-link impacts; OS locks, interrupted transactions and
concurrent user edits. Additional regression tests cover native/WSL path modes,
UTF-8 URLs, spaces, Unicode, UNC/drive translation, managed/linked attachments,
missing/offline files, oversized individual documents with complete input coverage,
200-paper collections above the former global input limit, bounded graph context,
old/new discovery with original passage retrieval, cache reuse/invalidation,
interrupted chunks/documents/reasoning, later-document transaction rollback,
partial model failures, untrusted metadata isolation, diagnostic severity, old state
records and preserved reviews. Architectural regressions additionally cover partial
main/supplement processing and independent attachment states, stage-specific cache
isolation, persistent provisional concept provenance, old personal/manuscript
retrieval, bounded postings/candidate pools, section/math-aware input coverage,
structured claim provenance, failed consolidation retention, detached launch options,
child reports/progress, bounded startup scheduling, readable-main dry-run coverage,
non-starving startup queues and query ranking that excludes self-only identifiers.
Every service response is synthetic or mocked.

CI is configured for Linux and Windows on Python 3.11 and 3.13, but that matrix has
not been executed by this local run. Path modes are mocked cross-platform;
real WSL `wslpath` also translated a synthetic encoded Windows path to `/mnt/c/`
with spaces and Unicode. An unmounted synthetic drive produced a translation error,
as intended, rather than a missing-file claim. This does not establish real Windows
Zotero, OneDrive or Obsidian integration. Actual Codex CLI execution was not invoked
for research processing and no inference success is claimed. No real Zotero
collection, manuscript or personal research note was added to this template.

## Live checks before using a research project

1. On a desktop with Zotero, enable its local API and select a small test collection
   containing locally downloaded text PDFs. Configure a new project copy and run
   doctor. Confirm group-library access separately if using groups/<id>.
2. Verify the Zotero version returns Total-Results on item, collection and child
   listings, and a local file URL for every processed attachment. Test subcollections
   and an item present in two collections. Missing/inconsistent responses must fail
   safely rather than manufacture an empty snapshot.
3. Log into the installed Codex CLI in a terminal. Confirm doctor recognizes the
   required flags and authentication. Test one real summary and inspect source/page
   attribution, supplement coverage and the explicit figure/OCR limitation. Model
   responses may fail validation; inspect staging and retry after fixing the cause.
4. Run sync again unchanged and confirm no new Codex staging invocation. Change one
   supplement and confirm only its parent summary is regenerated. Change one included
   LaTeX source and confirm the work summary updates while user additions survive.
5. In Obsidian explicitly install Shell Commands, use the documented OS-specific
   command and inspect stdout/stderr. Verify the virtual-environment executable,
   Node/npm/native Codex resolution, PATH and native sandbox setup on Windows.
   For split WSL mode, use the same distribution for setup/login and Obsidian
   invocation, check mirrored loopback connectivity from WSL, and verify actual
   Windows URLs for managed, linked/ZotMoov and locally available OneDrive PDFs.
   Temporarily make a supplement unavailable: its parent must remain in membership,
   its main summary must remain usable with partial coverage, and the next sync must
   retry the supplement without repeating unchanged main inference. With no usable
   attachment at all, the previous note must survive unchanged.
6. Enable only the incremental command's Obsidian-start event. Close/reopen the
   vault with Zotero open, then closed; the latter must report an error without
   removing membership. Trigger a second run concurrently and verify locking.
7. Inspect a pending conceptual proposal: no graph link or concept page should
   exist yet. Approve it in Properties/source mode, run apply and confirm exactly
   one edge. Reject another and confirm unchanged evidence is not proposed again.
8. Configure the five core graph groups and exclude Reviews/instruction/template
   paths. Confirm colors and filters in your installed Obsidian version.
9. Remove a test paper from the collection. Inspect the review and apply --dry-run
   impact. Back up the vault, commit tracked changes and test explicit deletion;
   personal notes must remain byte-for-byte intact. Restore the checkpoint in this
   disposable project and reset its restored approved/delete decision before apply.
10. Restore the paper to the collection and verify its original key/note identity
    reactivates without duplication. Review scientific statements whose support
    changed; structural tests cannot establish scientific validity.

## Deliberate baseline limits

- PDF processing supplies native text and optional local Tesseract recognition.
  OCR is uncertain, and image/figure interpretation, decryption and non-PDF
  supplement parsers are not included. Unprocessed pages leave coverage partial;
  metadata-only summaries are not fabricated. OCR regressions use synthetic raster
  PDFs and a mocked engine; real Tesseract/language-data integration needs a live check.
- LaTeX support resolves literal local input/include directives and preserves math
  source. It is not a TeX engine, macro evaluator or document build system.
- Synthesis and prose revisions are advisory. Users edit scientific prose and
  resolve/merge duplicate concepts manually; apply acknowledges revision reviews.
- Concept-title collision checks are structural. Broader scientific equivalence,
  contradictory findings and unsupported claims need agent suggestions and human
  judgment. Evidence excerpts prove provenance, not the validity of an inference.
- Changed parents use bounded chunks and hierarchical consolidation; successful
  chunks are cached. All detailed chunk summaries are retained, but a generated
  summary cannot guarantee preservation of every scientific nuance. There is no
  changed-page semantic diff; modified parents are reconsidered as whole jobs.
- The persistent concept registry uses an inverted lexical index and provisional
  annotations. Candidate terms, postings and the scored pool are capped. Each call
  sees selected summaries/concepts/source excerpts. Retrieval can miss distant,
  common-term-capped or vocabulary-disjoint concepts; absence of retrieval proves
  nothing about scientific relatedness. This is not exhaustive scientific review.
- Character budgets include instructions/schema/output reserve, not exact model
  tokens. Consolidation failure is explicit; it leaves validated chunks cached.
  No runtime garbage collector is included for caches/staging.
- Rejected relationships deduplicate by endpoints/type/normalized supporting quotes.
  Materially different quoted evidence permits reconsideration. There is no
  automated semantic test for whether two different excerpts mean the same thing.
- A clean Git repository plus verified local ZIP is required for deletion. Backups
  are local, ignored by Git and need an independent backup strategy. Local OS locks
  do not coordinate multiple computers or cloud-synchronized replicas.
- The wheel contains the Python implementation; use the complete GitHub template
  (or source distribution) for vault folders, scripts, saved roles and configuration.

## Additional live execution checks

Verify the documented background commands in each actual desktop environment:
Obsidian should return immediately, status/Home should advance, and the child JSON
report should finish with the correct exit code. Launch a second run while the first
holds the lock and confirm rejection. Test WSL Obsidian launching the same Linux
interpreter/Codex installation after the launcher exits. Check actual Windows API
reachability and locally accessible managed/linked/OneDrive files with doctor;
mirrored configuration alone is not evidence of successful connectivity. These
GUI/cloud/service checks are not replaced by synthetic tests.
