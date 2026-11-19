# Validation record and live integration checklist

## Verified in this workspace

Validated on Linux with Python 3.12.8 using synthetic sources and mock services:

- `python -m pytest -q`: **53 passed**.
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
concurrent user edits. Every service response is synthetic or mocked.

CI is configured for Linux and Windows on Python 3.11 and 3.13, but that matrix has
not been executed by this local run. Actual Codex CLI execution was not invoked
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

- PDF processing is text extraction only. No OCR, image/figure interpretation,
  decryption or non-PDF supplement parsers are included. A configured unreadable
  source fails explicitly; metadata-only bibliographic summaries are not fabricated.
- LaTeX support resolves literal local input/include directives and preserves math
  source. It is not a TeX engine, macro evaluator or document build system.
- Synthesis and prose revisions are advisory. Users edit scientific prose and
  resolve/merge duplicate concepts manually; apply acknowledges revision reviews.
- Concept-title collision checks are structural. Broader scientific equivalence,
  contradictory findings and unsupported claims need agent suggestions and human
  judgment. Evidence excerpts prove provenance, not the validity of an inference.
- Whole changed papers are reprocessed. Graph roles see the current vault context;
  max_input_chars is an explicit fail-fast limit, with no silent truncation. There
  is no retrieval index, large-vault batching or page-level incremental reasoning.
- Rejected relationships deduplicate by endpoints/type/normalized supporting quotes.
  Materially different quoted evidence permits reconsideration. There is no
  automated semantic test for whether two different excerpts mean the same thing.
- A clean Git repository plus verified local ZIP is required for deletion. Backups
  are local, ignored by Git and need an independent backup strategy. Local OS locks
  do not coordinate multiple computers or cloud-synchronized replicas.
- The wheel contains the Python implementation; use the complete GitHub template
  (or source distribution) for vault folders, scripts, saved roles and configuration.
