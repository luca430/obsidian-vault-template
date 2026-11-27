"""One-shot orchestration. Source snapshots and generated files commit together."""

from __future__ import annotations

import difflib
import importlib.util
import json
import subprocess
from copy import deepcopy
from pathlib import Path

from .agents import (
    AuthenticationError,
    payload_budget,
    validate_note_evidence,
)
from .cancellation import check_cancelled
from .config import ROLES, Config
from .identities import literature_path
from .inference_errors import AllowanceExhausted, OperationDeferred, RateLimited
from .notes import bibliography, diagnostics, impact, read_notes, summary_content, update_note
from .operations import OperationStore
from .paths import environment
from .processing import Processor
from .recipes import content_recipe, reasoning_recipe, stage_recipe
from .registry import Registry
from .review_state import candidate_groups, concept_catalog, migrate_matching_dependencies
from .review_workflow import readiness, select_reviews
from .reviews import (
    apply_decisions,
    archive_finalized,
    checkpoint,
    claim_proposal,
    counts,
    link_proposal,
    proposal,
    read_reviews,
    refresh_pending,
    revise_proposals,
)
from .runtimes import LLMRunner
from .scientific import graph_signature, scientific_notes, scientific_text
from .sources import (
    coverage_error,
    inspect_pdf,
    manuscript,
    ocr_diagnostics,
    page_coverage,
    usable_pages,
)
from .storage import (
    VaultError,
    atomic,
    commit,
    digest,
    file_hash,
    frontmatter,
    inside,
    markdown,
    now,
    read_json,
    replace_section,
)
from .zotero import Zotero

STATE = ".pipeline/state.json"


class IntegrationError(VaultError):
    """A failed transaction or concurrent edit requires stopping the run."""


def initial_state() -> dict:
    return {
        "schema": 1,
        "identity": None,
        "server": None,
        "membership": [],
        "papers": {},
        "works": {},
        "personal": {},
        "proposals": {},
        "graph_digest": None,
        "last_success": None,
        "warnings": [],
    }


class Pipeline:
    def __init__(self, config: Config, zotero=None, agent=None, progress=None):
        self.config, self.root = config, config.root
        self.on_progress = progress
        self.zotero = zotero if zotero is not None else Zotero(config)
        self.agent = agent if agent is not None else LLMRunner(config)

    def state(self) -> dict:
        state = read_json(inside(self.root, STATE), initial_state())
        if not isinstance(state, dict) or state.get("schema") not in (1, 2):
            raise VaultError(
                "Unsupported state schema; restore matching code or migrate explicitly"
            )
        required = initial_state()
        if not all(k in state for k in required) or not all(
            isinstance(state[k], type(required[k]))
            for k in ("membership", "papers", "works", "personal", "proposals", "warnings")
        ):
            raise VaultError("State structure is damaged; restore .pipeline/state.json from backup")
        if state["identity"] not in (None, self.config.identity):
            raise VaultError(
                "Zotero library/collection settings changed. Use a fresh vault for a different collection; --full preserves identity."
            )
        return state

    def recipe(self, role=None) -> str:
        if role:
            return stage_recipe(self.config, role)
        return digest(
            [
                stage_recipe(self.config, r)
                for r in (
                    "literature",
                    "manuscript",
                    "concepts",
                    "linking",
                    "synthesis",
                    "maintenance",
                    "retrieval",
                )
            ]
        )

    def retry_limit(self, record):
        role = record.get("processing_role") or (
            "manuscript" if str(record.get("note", "")).startswith("My Work/") else "literature"
        )
        return (
            self.config.runtime(role)[1]
            .get("retry_policy", {})
            .get("max_attempts", self.config.processing("max_retry_attempts"))
        )

    def graph_retry_limit(self):
        return max(
            self.config.runtime(role)[1]
            .get("retry_policy", {})
            .get("max_attempts", self.config.processing("max_retry_attempts"))
            for role in ("concepts", "linking", "synthesis", "maintenance")
        )

    def synchronization_status(self, state, reviews, notes=None):
        previous = (state.get("last_report") or {}).get("status")
        if previous == "failed":
            return "failed"
        if state.get("graph_digest") is None and not state.get("last_attempt"):
            return "incomplete_resumable"
        records = [*state["papers"].values(), *state["works"].values()]
        if state.get("graph_error"):
            return (
                "failed"
                if state.get("graph_retry_attempts", 0) >= self.graph_retry_limit()
                else "incomplete_resumable"
            )
        if any(
            r.get("status") in {"pending", "running", "deferred"}
            or (r.get("retryable") and r.get("attempts", 0) < self.retry_limit(r))
            for r in records
        ):
            return "incomplete_resumable"
        if any(r.get("status") == "failed" for r in records):
            return "failed"
        if notes is not None and graph_signature(notes, reasoning_recipe(self.config)) != state.get(
            "graph_digest"
        ):
            return "incomplete_resumable"
        if any(
            r.get("error") or r.get("completeness") in {"metadata_only", "partial"} for r in records
        ):
            return "completed_with_limitations"
        active = any(
            not r.get("applied")
            and not r.get("superseded_by")
            and not r.get("resolution")
            and reviews[p]["status"] in {"pending", "approved"}
            for p, r in state["proposals"].items()
        )
        return "completed_reviews_pending" if active else "up_to_date"

    def dashboard(self, state: dict, reviews: dict, error: str | None = None) -> str:
        path = inside(self.root, "Home.md")
        text = path.read_text(encoding="utf-8")
        pending = counts(reviews, state["proposals"])
        lines = [
            f"Last successful synchronization: {state['last_success'] or 'never'}",
            f"Processed papers: {sum(p.get('processed', False) for p in state['papers'].values())}",
            f"Active collection members: {len(state['membership'])}",
            f"Pending links: {pending['link']}",
            f"Pending claim reviews: {pending['claim']}",
            f"Pending removals: {pending['removal']}",
            f"Pending revisions: {pending['revision']}",
            f"Pending concept decisions: {pending['consolidation']}",
            f"Errors: {1 if error else 0}",
            f"Synchronization status: {self.synchronization_status(state, reviews)}",
            f"Archived reviews: {sum(bool(r.get('archived_at')) for r in state['proposals'].values())}",
        ]
        for status in ("pending", "skipped", "failed"):
            total = sum(
                r.get("status") == status
                for r in [*state["papers"].values(), *state["works"].values()]
            )
            lines.append(f"{status.capitalize()} documents: {total}")
        for coverage in ("metadata_only", "partial", "complete", "failed"):
            lines.append(
                f"{coverage} coverage: "
                + str(
                    sum(
                        r.get("completeness", "complete" if r.get("processed") else "metadata_only")
                        == coverage
                        for r in state["papers"].values()
                    )
                )
            )
        if getattr(self, "last_progress", None):
            lines.append("Current stage: " + self.last_progress["stage"])
        if state.get("last_report"):
            lines.append(
                f"Completed operations: {state['last_report'].get('completed_operations', 0)}"
            )
            lines.append(
                f"Processing failures: {state['last_report'].get('processing_failures', 0)}"
            )
            lines.append(
                "Last attempt: "
                + (
                    "complete"
                    if state["last_report"]["complete"]
                    else "incomplete; see status for details"
                )
            )
        if error:
            lines.append("Last error: see .pipeline/logs/events.jsonl for details.")
        lines += ["Warnings: " + str(len(state.get("warnings", [])))]
        lines += [
            "- " + w.replace("[[", "［［").replace("]]", "］］") for w in state.get("warnings", [])
        ]
        return replace_section(text, "dashboard", "\n\n".join(lines))

    def merged_reviews(self, state: dict, changes: dict) -> dict:
        result = {}
        for pid, record in state["proposals"].items():
            text = changes.get(record["path"])
            if text is None:
                text = inside(self.root, record["path"]).read_text(encoding="utf-8")
            result[pid] = frontmatter(text)[0]
        return result

    def progress(self, stage: str, **details) -> None:
        check_cancelled()
        self.last_progress = {"stage": stage, **details}
        if self.on_progress:
            self.on_progress(self.last_progress)

    def baseline(self, state):
        self._baseline_notes = read_notes(self.root)
        self._baseline_files = {
            n: inside(self.root, n).read_bytes()
            for n in [
                "Home.md",
                STATE,
                "config.yaml",
                "works.yaml",
                "AGENTS.md",
                *[f"agents/{r}.md" for r in ROLES],
                *[r["path"] for r in state["proposals"].values()],
            ]
            if inside(self.root, n).exists()
        }

    def save(self, state, changes=None):
        changes = dict(changes or {})
        try:
            archived = archive_finalized(self.root, state["proposals"], changes)
            self.archived_reviews = getattr(self, "archived_reviews", 0) + len(archived)
            if state.get("concept_registry") is not None or state["proposals"]:
                registry = Registry(state.get("concept_registry"))
                current_notes = dict(self._baseline_notes)
                for name, value in changes.items():
                    if name.startswith(("Literature/", "Concepts/", "My Notes/", "My Work/")):
                        if value is None:
                            current_notes.pop(name, None)
                        else:
                            current_notes[name] = value
                registry.reviewed(state["proposals"], current_notes)
                state["concept_registry"] = registry.data
            changes["Home.md"] = self.dashboard(state, self.merged_reviews(state, changes))
            changes[STATE] = json.dumps(state, ensure_ascii=False, indent=2)
            self._commit_checked(changes)
        except (OSError, VaultError) as exc:
            raise IntegrationError(str(exc)) from exc
        # Advance expected bytes, never absorb a concurrent user edit into our baseline.
        for name, text in changes.items():
            if name.startswith(("Literature/", "My Work/", "My Notes/", "Concepts/")):
                if text is None:
                    self._baseline_notes.pop(name, None)
                else:
                    self._baseline_notes[name] = text
            else:
                if text is None:
                    self._baseline_files.pop(name, None)
                else:
                    self._baseline_files[name] = text.encode("utf-8")

    def sync(
        self, full: bool = False, dry_run: bool = False, startup: bool = False, retry: bool = False
    ) -> dict:
        self._interruption_reported = False
        try:
            return self._sync(full, dry_run, startup, retry)
        except KeyboardInterrupt:
            if not dry_run:
                self.report_error("Synchronization interrupted; completed work remains resumable")
                self._interruption_reported = True
            raise

    def _sync(self, full=False, dry_run=False, startup=False, retry=False):
        self.archived_reviews = 0
        self.current_processor = None
        self.current_plan = {}
        state = deepcopy(self.state())
        if full and startup:
            raise VaultError("--startup is incremental; use --full separately")
        self.baseline(state)
        reviews = read_reviews(self.root, state["proposals"])
        if getattr(self.zotero, "server", None) is None:
            self.zotero.server = state.get("server")
        self.progress("snapshot")
        snapshot = self.zotero.snapshot()  # Incomplete/API/auth failures never advance membership.
        if state.get("server") and self.zotero.server and state["server"] != self.zotero.server:
            raise VaultError("Zotero instance differs from stored state; use a new vault")
        notes = deepcopy(self._baseline_notes)
        self._run_initial_notes = dict(notes)
        self._run_initial_proposals = set(state["proposals"])
        self._run_initial_duplicates = sum(
            r.get("suppressed_duplicates", 0) for r in state["proposals"].values()
        )
        recipe = reasoning_recipe(self.config)
        summary_recipe = content_recipe(self.config, "literature")
        manuscript_recipe = content_recipe(self.config, "manuscript")
        if state.get("recipe_policy") != 2:
            for r in state["papers"].values():
                if r.get("status", "successful") == "successful" and r.get(
                    "summary_recipe"
                ) == stage_recipe(self.config, "literature", legacy=True):
                    r["summary_recipe"] = summary_recipe
            for r in state["works"].values():
                if r.get("status", "successful") == "successful" and r.get(
                    "recipe"
                ) == stage_recipe(self.config, "manuscript", legacy=True):
                    r["recipe"] = manuscript_recipe
            for name, r in state.get("concept_registry", {}).get("documents", {}).items():
                if (
                    name in notes
                    and r.get("fingerprint") == digest(scientific_text(notes[name]))
                    and r.get("recipe") == stage_recipe(self.config, "concepts", legacy=True)
                ):
                    r["recipe"] = content_recipe(self.config, "concepts")
            legacy_graph_recipe = digest(
                [
                    stage_recipe(self.config, role, legacy=True)
                    for role in ("concepts", "linking", "synthesis", "maintenance", "retrieval")
                ]
            )
            if state.get("graph_recipe") == legacy_graph_recipe and not state.get("graph_error"):
                if state.get("graph_notes") == {
                    n: digest(scientific_text(t)) for n, t in notes.items()
                }:
                    state["graph_digest"] = graph_signature(notes, recipe)
                state["graph_recipe"] = recipe
            state["recipe_policy"] = 2
        processor = Processor(
            self.config,
            self.agent,
            recipe,
            self.progress,
            full,
            dry_run=dry_run,
            retry=retry,
            operation_budget=self.config.processing("startup_max_operations") if startup else None,
        )
        self.current_processor = processor
        identified = {}
        for name, text in sorted(notes.items()):
            if not name.startswith("Literature/") or not text.startswith("---\n"):
                continue
            meta, _ = frontmatter(text)
            identity = meta.get("id")
            if isinstance(identity, str) and identity.startswith("zotero:"):
                key = identity.removeprefix("zotero:")
                if key in identified:
                    raise VaultError(f"Multiple literature notes share Zotero identity {key}")
                identified[key] = name
        for key, name in identified.items():
            registered_path = state["papers"].get(key, {}).get("note")
            if registered_path and registered_path != name:
                raise VaultError(
                    f"Literature note moved outside migration: {key}; restore its registered path {registered_path} before migrating"
                )
        paths = {
            key: state["papers"].get(key, {}).get("note")
            or identified.get(key)
            or literature_path(key, item["metadata"], self.root)
            for key, item in snapshot.items()
        }
        selection_reasons = {}

        def annotation_content(value):
            if isinstance(value, dict):
                return {
                    k: annotation_content(v)
                    for k, v in sorted(value.items())
                    if k not in {"version", "dateAdded", "dateModified", "lastModified"}
                }
            if isinstance(value, list):
                return [annotation_content(v) for v in value]
            return value

        def source_signature(item):
            return digest(
                [
                    sorted(
                        (a["key"], a.get("sha256"), a["classification"], a.get("error"))
                        for a in item["attachments"]
                    ),
                    sorted(annotation_content(item.get("annotations", [])), key=digest),
                ]
            )

        def select_paper(key, item):
            old = state["papers"].get(key, {})
            attempt = digest(
                [source_signature(item), summary_recipe, stage_recipe(self.config, "literature")]
            )
            previous_source = old.get("source_signature")
            changed = (
                previous_source != source_signature(item)
                if previous_source
                else old.get("fingerprint") != digest([item["fingerprint"], summary_recipe])
            )
            reason = (
                "explicit full sync"
                if full
                else "explicit retry"
                if retry
                and (
                    old.get("status", "successful") != "successful"
                    or old.get("completeness", "complete") != "complete"
                )
                else "new collection member"
                if key not in state["membership"]
                else "source or summary recipe changed"
                if changed or old.get("summary_recipe", summary_recipe) != summary_recipe
                else "unfinished operation"
                if old.get("status") in {"pending", "running", "deferred"}
                else "temporary failure retry"
                if old.get("retryable")
                and old.get("attempts", 0)
                < self.config.runtime("literature")[1]
                .get("retry_policy", {})
                .get("max_attempts", self.config.processing("max_retry_attempts"))
                else "unfinished agent configuration changed"
                if old.get("status") == "failed"
                and old.get("agent_recipe") != stage_recipe(self.config, "literature")
                else "missing generated note"
                if paths[key] not in notes and not old.get("claim_review") and old.get("processed")
                else None
            )
            if reason:
                selection_reasons[key] = reason
            return reason, attempt

        selections = {k: select_paper(k, v) for k, v in sorted(snapshot.items())}
        changed_papers = [key for key in sorted(snapshot) if selections[key][0]]
        removed = sorted(set(state["membership"]) - set(snapshot))
        works, changed_works, warnings, skipped, partial_candidates = {}, [], [], {}, {}
        page_diagnostics = {}
        for key, item in snapshot.items():
            attachments = item["attachments"]
            for attachment in attachments:
                if attachment["classification"] == "ignore":
                    warnings.append(
                        f"Ignored attachment {attachment['key']} on {key}: {attachment.get('reason', 'configured ignore or unsupported format')}"
                    )
            errors = [a["error"] for a in attachments if a.get("error")]
            readable = [
                a for a in attachments if a["classification"] != "ignore" and not a.get("error")
            ]
            if not any(a["classification"] != "ignore" for a in attachments):
                errors.append("no processable PDF; no readable main/supplementary PDF")
            if errors and readable:
                partial_candidates[key] = "; ".join(errors)
                warnings.append(
                    f"Paper {key}: available attachments will be processed with partial coverage: {partial_candidates[key]}"
                )
            if errors and not readable:
                skipped[key] = "; ".join(errors)
                warnings.append(f"Paper {key} skipped: {skipped[key]}")
        registered = self.config.works()  # Invalid registry/authorization is fatal.
        work_errors = {}
        for work in registered:
            key = work["id"]
            old = state["works"].get(key, {})
            for name in old.get("files", {}):
                self.config.authorized(
                    name
                )  # Revoked include authorization must never be bypassed.
            try:
                files = old.get("files", {})
                unchanged = (
                    not full
                    and old.get("metadata") == work
                    and old.get("recipe") == manuscript_recipe
                    and not (
                        retry
                        and (
                            old.get("status", "successful") != "successful"
                            or old.get("completeness", "complete") != "complete"
                        )
                    )
                    and old.get("status", "successful") not in {"pending", "running", "deferred"}
                    and not (
                        old.get("status") == "failed"
                        and old.get("agent_recipe") != stage_recipe(self.config, "manuscript")
                    )
                    and not (
                        old.get("retryable")
                        and old.get("attempts", 0)
                        < self.config.runtime("manuscript")[1]
                        .get("retry_policy", {})
                        .get("max_attempts", self.config.processing("max_retry_attempts"))
                    )
                    and old.get("source") == str(self.config.authorized(work["source_path"]))
                    and files
                    and all(file_hash(self.config.authorized(p)) == h for p, h in files.items())
                    and (f"My Work/{key}.md" in notes or old.get("claim_review"))
                )
                if unchanged:
                    continue
                if startup and len([k for k in changed_papers if k not in skipped]) + len(
                    changed_works
                ) >= self.config.processing("startup_max_documents"):
                    changed_works.append(key)
                    continue
                works[key] = manuscript(
                    self.config,
                    work,
                    (lambda path, source, sha: inspect_pdf(path, source, self.config.ocr))
                    if dry_run
                    else processor.extract,
                )
                page_diagnostics[f"work:{key}"] = page_coverage(works[key]["pages"])
                issue = coverage_error(works[key]["pages"])
                if issue:
                    partial_candidates[f"work:{key}"] = issue
                    warnings.append(f"Manuscript {key}: {issue}")
            except (OSError, VaultError) as exc:
                work_errors[key] = str(exc)
                warnings.append(
                    f"Manuscript {key} unavailable/disappeared or invalid: {exc}; its summary was retained."
                )
                if (
                    not full
                    and not retry
                    and old.get("status") == "skipped"
                    and old.get("error") == str(exc)
                ):
                    continue
            changed_works.append(key)
            selection_reasons[f"work:{key}"] = (
                "source/recipe changed, explicit retry, or unfinished operation"
            )
        for key in state["works"]:
            if key not in {w["id"] for w in registered}:
                warnings.append(f"Work {key} is no longer registered; its summary was retained.")
        blockers = [
            f"{role}: input limit cannot fit instructions/schema/output reserve plus source context"
            for role in (
                "literature",
                "manuscript",
                "concepts",
                "linking",
                "synthesis",
                "maintenance",
            )
            if payload_budget(self.config, role) < 1500
        ]
        if blockers and not dry_run:
            raise VaultError("Invalid processing configuration: " + "; ".join(blockers))
        estimates = {}
        if dry_run and not blockers:
            for key in changed_papers:
                if key in skipped:
                    continue
                pages, attachment_errors = [], []
                for attachment in snapshot[key]["attachments"]:
                    if attachment["classification"] == "ignore" or attachment.get("error"):
                        continue
                    try:
                        extracted = inspect_pdf(
                            Path(attachment["path"]),
                            attachment["key"],
                            self.config.ocr,
                        )
                        page_diagnostics[attachment["key"]] = page_coverage(extracted)
                        pages.extend(usable_pages(extracted))
                        issue = coverage_error(extracted)
                        if issue:
                            attachment_errors.append(f"{attachment['key']}: {issue}")
                    except (VaultError, OSError) as exc:
                        attachment_errors.append(f"{attachment['key']}: {exc}")
                if not pages:
                    skipped[key] = "; ".join(attachment_errors) or "no processable PDF"
                    warnings.append(f"Paper {key} skipped: {skipped[key]}")
                    continue
                if attachment_errors:
                    partial_candidates[key] = "; ".join(attachment_errors)
                    warnings.append(f"Paper {key}: partial coverage: {partial_candidates[key]}")
                try:
                    chunks = len(list(processor.batches("literature", {}, pages)))
                    estimates[key] = chunks + (1 if chunks > 1 else 0)
                except VaultError as exc:
                    blockers.append(f"Paper {key}: {exc}")
        personal = {
            n: digest(scientific_text(t)) for n, t in notes.items() if n.startswith("My Notes/")
        }
        graph_input = graph_signature(notes, recipe)
        graph_needed = bool(notes or changed_papers or changed_works) and (
            full or graph_input != state["graph_digest"] or bool(state.get("graph_error"))
        )
        plan = {
            "providers": {
                role: self.config.runtime(role)[0]
                for role in (
                    "literature",
                    "manuscript",
                    "concepts",
                    "linking",
                    "synthesis",
                    "maintenance",
                )
            },
            "papers": changed_papers,
            "works": changed_works,
            "removed": removed,
            "new": [k for k in snapshot if k not in state["papers"]],
            "modified": [k for k in changed_papers if k in state["papers"]],
            "unchanged": [k for k in snapshot if k not in changed_papers],
            "processable": [k for k in snapshot if k not in skipped],
            "skipped": skipped,
            "partial_candidates": partial_candidates,
            "page_processing": page_diagnostics,
            "ocr": ocr_diagnostics(self.config.ocr),
            "extraction_diagnostic_note": "Dry runs inspect native text only; OCR is not run during diagnostics. The configured OCR setting is unchanged and applies during synchronization.",
            "estimated_document_operations": len([k for k in changed_papers if k not in skipped])
            + len(works)
            if not dry_run
            else sum(estimates.values()) + len(works),
            "document_operation_estimates": estimates,
            "estimated_graph_operations": (3 * max(1, len(notes))) if graph_needed else 0,
            "operation_estimate_note": "Minimum summary calls; oversized documents require chunks/consolidation. Graph jobs use bounded retrieved fragments.",
            "personal_notes_changed": personal != state["personal"],
            "reasoning_needed": graph_needed,
            "warnings": warnings,
            "blocking_errors": blockers,
            "dry_run": dry_run,
            "selection_reasons": selection_reasons,
            "retried_operations": {
                key: reason
                for key, reason in selection_reasons.items()
                if "retry" in reason or "unfinished" in reason
            },
            "reasoning_operations": [],
            "startup": startup,
            "reasoning_deferred": bool(startup and graph_needed),
        }
        self.current_plan = plan
        if dry_run:
            return plan
        # Authenticate globally before accepting work requiring inference. Mock agents need no session.
        graph_eligible = (
            full
            or retry
            or state.get("graph_attempt_signature") != graph_input
            or state.get("graph_retry_attempts", 0) < self.graph_retry_limit()
        )
        graph_auth = (
            not startup
            or state.get("graph_deferred")
            or any(
                r.get("role") in {"concepts", "linking", "synthesis", "maintenance"}
                and r["status"] != "completed"
                for r in processor.tasks.records.values()
            )
        ) and (
            (graph_needed and graph_eligible)
            or any(k not in skipped for k in changed_papers)
            or bool(works)
        )
        if (
            any(k not in skipped for k in changed_papers)
            or works
            or (graph_needed and graph_eligible and notes)
        ) and hasattr(self.agent, "authenticate"):
            self.authenticate_agent(changed_papers, changed_works, graph_auth)
        for key in removed:
            if key in state["papers"]:
                old = state["papers"][key]
                old["removal_epoch"] = old.get("removal_epoch", 0) + 1
                old["disposition"] = "absent"
        for key in changed_papers:
            state["papers"].setdefault(
                key,
                {
                    "note": paths[key],
                    "processed": False,
                    "removal_epoch": 0,
                    "completeness": "metadata_only",
                },
            )
            record = state["papers"][key]
            attempt = selections[key][1]
            record.update(
                status="pending",
                disposition="active",
                attempts=record.get("attempts", 0) + 1
                if record.get("attempt_fingerprint") == attempt
                else 1,
                attempt_fingerprint=attempt,
                source_signature=source_signature(snapshot[key]),
                summary_recipe=summary_recipe,
                agent_recipe=stage_recipe(self.config, "literature"),
                metadata=snapshot[key]["metadata"],
                fingerprint=digest([snapshot[key]["fingerprint"], summary_recipe]),
            )
            if startup and key in skipped:
                record = state["papers"][key]
                record.update(status="skipped", completeness="metadata_only", error=skipped[key])
                record["attachment_processing"] = {
                    a["key"]: {
                        "completeness": "metadata_only",
                        "error": a.get("error"),
                        "classification": a["classification"],
                    }
                    for a in snapshot[key]["attachments"]
                }
        for work in registered:
            if work["id"] in changed_works:
                state["works"].setdefault(work["id"], {}).update(
                    status="pending",
                    processing_role="manuscript",
                    agent_recipe=stage_recipe(self.config, "manuscript"),
                )
        state.update(
            identity=self.config.identity,
            server=self.zotero.server,
            membership=sorted(snapshot),
            personal=personal,
            warnings=warnings,
            last_attempt=now(),
        )
        presentation = {}
        for key, item in sorted(snapshot.items()):
            state["papers"].setdefault(key, {})["metadata"] = item["metadata"]
            path = paths[key]
            if path in notes:
                meta, _ = frontmatter(notes[path])
                if (
                    meta.get("id") != f"zotero:{key}"
                    or meta.get("generated") is not True
                    or meta.get("kind") != "literature"
                ):
                    raise VaultError("Refusing to update bibliography with mismatched ownership")
                text = bibliography(
                    notes[path], key, item["metadata"], self.config.zotero.get("library", "users/0")
                )
                if text != notes[path]:
                    notes[path] = text
                    presentation[path] = text
        document_tasks = {}
        for category, keys, role in (
            ("papers", changed_papers, "literature"),
            ("works", changed_works, "manuscript"),
        ):
            for key in keys:
                record = state[category][key]
                task = digest(
                    [
                        "document",
                        category,
                        key,
                        record.get("source_signature") or record.get("fingerprint"),
                        content_recipe(self.config, role),
                    ]
                )
                document_tasks[(category, key)] = task
                processor.tasks.plan(
                    task,
                    kind="document",
                    role=role,
                    provider=self.config.runtime(role)[0],
                    document=key,
                )
        # Membership is complete independently of attachment processing; never infer removals from I/O.
        self.save(state, presentation)
        failed, partial = {}, {}
        scheduled_papers, scheduled_works = changed_papers, changed_works
        if startup:
            limit = self.config.processing("startup_max_documents")
            candidates = [k for k in changed_papers if k not in skipped]
            candidates.sort(
                key=lambda k: (
                    bool(state["papers"][k].get("processed")),
                    state["papers"][k].get("completeness") == "partial",
                    k,
                )
            )
            scheduled_papers = candidates[:limit]
            scheduled_works = changed_works[: max(0, limit - len(scheduled_papers))]
        for category, keys, scheduled in (
            ("papers", changed_papers, scheduled_papers),
            ("works", changed_works, scheduled_works),
        ):
            for key in set(keys) - set(scheduled):
                processor.tasks.update(
                    document_tasks[(category, key)],
                    "deferred",
                    reason="Startup document budget; resume during the next manual/startup Sync",
                )
        deferred = (
            len(changed_papers)
            + len(changed_works)
            - len(scheduled_papers)
            - len(scheduled_works)
            - (len([k for k in changed_papers if k in skipped]) if startup else 0)
        )
        total = len(scheduled_papers) + len(scheduled_works)
        completed = 0
        successful_documents = 0
        for key in scheduled_papers:
            self.progress(
                "documents",
                current=key,
                processed=completed,
                remaining=total - completed,
                skipped=len(skipped),
                failed=len(failed),
            )
            item, old = snapshot[key], state["papers"][key]
            task = document_tasks[("papers", key)]
            processor.tasks.update(task, "running")
            try:
                provider = self.config.runtime("literature")[0]
                if provider in processor.blocked_providers:
                    raise OperationDeferred(processor.blocked_providers[provider])
                result, pages, attachment_errors, unavailable = self.process_paper(
                    key, item, old, processor, state
                )
                if result is not None:
                    content = summary_content(
                        result,
                        pages,
                        {
                            "zotero": item["metadata"],
                            "attachments": item["attachments"],
                            "completeness": old["completeness"],
                        },
                    )
            except (AllowanceExhausted, OperationDeferred, RateLimited) as exc:
                old.update(status="deferred", error=str(exc), retryable=False)
                processor.tasks.update(
                    task, "deferred", reason=str(exc), error_type=getattr(exc, "code", "deferred")
                )
                self.save(state)
                completed += 1
                continue
            except (AuthenticationError, IntegrationError):
                raise
            except (OSError, VaultError) as exc:
                result = None
                old["completeness"] = "failed"
                attachment_errors, unavailable = {key: str(exc)}, {}
            if result is None:
                if old["completeness"] == "partial":
                    partial[key] = {**attachment_errors, **unavailable}
                error = (
                    "; ".join([*attachment_errors.values(), *unavailable.values()])
                    or "no processable PDF; no readable main/supplementary PDF"
                )
                if old["completeness"] == "failed":
                    failed[key] = error
                    old.update(status="failed", error=error, retryable=True)
                else:
                    skipped[key] = error
                    old.update(status="skipped", error=error, retryable=False)
                warnings.append(f"Paper {key}: {error}; existing note retained.")
                processor.tasks.update(
                    task, "failed" if old.get("retryable") else "completed", reason=error
                )
                self.save(state)
                completed += 1
                continue
            path = paths[key]
            changes = {}
            verification = processor.warnings_for(result)
            if verification:
                context = {
                    "note": path,
                    "identity": f"zotero:{key}",
                    "kind": "literature",
                    "category": "papers",
                    "key": key,
                    "library": self.config.zotero.get("library", "users/0"),
                    "fingerprint": digest([item["fingerprint"], summary_recipe]),
                    "pages": pages,
                    "metadata": {
                        "zotero": item["metadata"],
                        "attachments": item["attachments"],
                        "completeness": old["completeness"],
                    },
                    "files": {
                        a["path"]: a["sha256"]
                        for a in item["attachments"]
                        if a.get("path")
                        and a.get("sha256")
                        and not a.get("error")
                        and a["classification"] != "ignore"
                    },
                }
                old["claim_review"] = claim_proposal(
                    result, verification, context, state["proposals"], notes, changes
                )
                old["claim_review_status"] = "pending"
                warnings.append(
                    f"Paper {key}: unverified draft retained for claim review; established note retained."
                )
            else:
                notes[path] = update_note(
                    notes.get(path), f"zotero:{key}", "literature", result["title"], content
                )
                notes[path] = bibliography(
                    notes[path], key, item["metadata"], self.config.zotero.get("library", "users/0")
                )
                changes[path] = notes[path]
                old.pop("claim_review", None)
                old.pop("claim_review_status", None)
            if old["completeness"] == "partial":
                partial[key] = {**attachment_errors, **unavailable}
                warnings.append(
                    f"Paper {key} partially processed; outstanding attachments: {', '.join(partial[key])}"
                )
            old.update(
                fingerprint=digest([item["fingerprint"], summary_recipe]),
                note=path,
                processed=path in notes,
                status="successful",
                disposition="active",
                files=item["attachments"],
                error="; ".join(partial.get(key, {}).values()) or None,
                retryable=bool(attachment_errors),
            )
            if not verification:
                old["passages"] = self.store_passages(key, item["fingerprint"], pages)
            self.save(state, changes)
            processor.tasks.update(task, "completed")
            completed += 1
            successful_documents += 1
        for key in scheduled_works:
            self.progress(
                "manuscripts", current=key, processed=completed, remaining=total - completed
            )
            old = state["works"][key]
            task = document_tasks[("works", key)]
            processor.tasks.update(task, "running")
            if key in work_errors:
                old.update(status="skipped", completeness="metadata_only", error=work_errors[key])
                processor.tasks.update(
                    task, "completed", reason="Source unavailable; coverage limitation retained"
                )
                self.save(state)
                completed += 1
                continue
            source = works[key]
            issue = coverage_error(source["pages"])
            old["page_processing"] = page_coverage(source["pages"])
            if issue:
                partial[f"work:{key}"] = {"pages": issue}
            try:
                provider = self.config.runtime("manuscript")[0]
                if provider in processor.blocked_providers:
                    raise OperationDeferred(processor.blocked_providers[provider])
                result = processor.summarize("manuscript", source)
                if any(file_hash(Path(p)) != h for p, h in source["files"].items()):
                    raise VaultError("Manuscript changed while summarizing; retry sync")
                content = summary_content(result, source["pages"], source["metadata"])
            except (AllowanceExhausted, OperationDeferred, RateLimited) as exc:
                old.update(status="deferred", error=str(exc), retryable=False)
                processor.tasks.update(
                    task, "deferred", reason=str(exc), error_type=getattr(exc, "code", "deferred")
                )
                self.save(state)
                completed += 1
                continue
            except (AuthenticationError, IntegrationError):
                raise
            except (OSError, VaultError) as exc:
                failed[f"work:{key}"] = str(exc)
                old.update(
                    status="failed",
                    completeness="partial" if issue else "failed",
                    error=str(exc),
                    retryable=True,
                    attempts=old.get("attempts", 0) + 1,
                )
                warnings.append(f"Manuscript {key} failed: {exc}")
                processor.tasks.update(
                    task,
                    "failed",
                    reason=str(exc),
                    error_type=getattr(exc, "code", "source_failure"),
                )
                self.save(state)
                completed += 1
                continue
            path = f"My Work/{key}.md"
            if issue:
                warnings.append(f"Manuscript {key} partially processed: {issue}")
            changes = {}
            verification = processor.warnings_for(result)
            if verification:
                context = {
                    "note": path,
                    "identity": f"work:{key}",
                    "kind": "manuscript",
                    "category": "works",
                    "key": key,
                    "fingerprint": source["fingerprint"],
                    "pages": source["pages"],
                    "metadata": source["metadata"],
                    "files": source["files"],
                }
                old["claim_review"] = claim_proposal(
                    result, verification, context, state["proposals"], notes, changes
                )
                old["claim_review_status"] = "pending"
                warnings.append(f"Manuscript {key}: unverified draft retained for claim review.")
            else:
                notes[path] = update_note(
                    notes.get(path), f"work:{key}", "manuscript", result["title"], content
                )
                changes[path] = notes[path]
                old.pop("claim_review", None)
                old.pop("claim_review_status", None)
            old.update(
                fingerprint=source["fingerprint"],
                files=source["files"],
                metadata=source["metadata"],
                recipe=manuscript_recipe,
                completeness="partial" if issue else "complete",
                source=str(self.config.authorized(source["metadata"]["source_path"])),
                note=path,
                status="successful",
                error=issue,
                retryable=False,
            )
            if not verification:
                old["passages"] = self.store_passages(
                    f"work-{key}", source["fingerprint"], source["pages"]
                )
            self.save(state, changes)
            processor.tasks.update(task, "completed")
            completed += 1
            successful_documents += 1
        changes = {}
        revise_proposals(self.root, state["proposals"], reviews, notes, changes)
        for pid, record in state["proposals"].items():
            if record["kind"] == "claim" and record.get("superseded_by"):
                context = record["claim_context"]
                current = state[context["category"]][context["key"]]
                if current.get("claim_review") == pid:
                    current["claim_review"] = record["superseded_by"]
        for key, paper in state["papers"].items():
            path = paper["note"]
            if key not in snapshot and paper.get("disposition") == "absent" and path in notes:
                proposal(
                    "removal",
                    {
                        "item_key": key,
                        "note": path,
                        "reason": "Absent from a complete successful collection snapshot",
                        "epoch": paper["removal_epoch"],
                        "impact": impact(path, notes),
                    },
                    state["proposals"],
                    notes,
                    changes,
                )
        self.save(state, changes)
        graph_needed = (
            full
            or graph_signature(notes, recipe) != state["graph_digest"]
            or bool(state.get("graph_error"))
        )
        graph_attempt = graph_signature(notes, recipe)
        graph_profile = digest(
            [
                stage_recipe(self.config, role)
                for role in ("concepts", "linking", "synthesis", "maintenance")
            ]
        )
        if state.get("graph_error") and state.get("graph_agent_recipe") != graph_profile:
            state["graph_retry_attempts"] = 0
        state["graph_agent_recipe"] = graph_profile
        if state.get("graph_attempt_signature") != graph_attempt:
            state.update(graph_attempt_signature=graph_attempt, graph_retry_attempts=0)
        graph_retry_allowed = (
            full or retry or state.get("graph_retry_attempts", 0) < self.graph_retry_limit()
        )
        graph_error = state.get("graph_error") if graph_needed and not graph_retry_allowed else None
        resume_graph = state.get("graph_deferred", False) or any(
            r.get("role") in {"concepts", "linking", "synthesis", "maintenance"}
            and r["status"] in {"pending", "running", "failed", "deferred"}
            for r in processor.tasks.records.values()
        )
        graph_deferred = bool(startup and graph_needed and not resume_graph)
        if graph_needed and notes and (not startup or resume_graph) and graph_retry_allowed:
            state["graph_retry_attempts"] = state.get("graph_retry_attempts", 0) + 1
            prior = state.get("graph_notes", {})
            changed = {
                n for n, t in notes.items() if full or prior.get(n) != digest(scientific_text(t))
            }
            if state.get("graph_recipe") != recipe:
                changed = set(notes)
            passages = {}
            for record in [*state["papers"].values(), *state["works"].values()]:
                if record.get("passages") and record.get("note") in notes:
                    passages[record["note"]] = read_json(inside(self.root, record["passages"]), [])
            statuses = {
                r["note"]: {
                    "completeness": r.get("completeness", "complete"),
                }
                for r in [*state["papers"].values(), *state["works"].values()]
                if r.get("note")
            }
            concept_registry = Registry(state.get("concept_registry"))
            state["concept_registry"] = concept_registry.data
            try:
                for name in set(concept_registry.data["documents"]) - notes.keys():
                    concept_registry.remove(name)
                indexed = 0
                for name, text in sorted(scientific_notes(notes).items()):
                    self.progress(
                        "concepts",
                        current=name,
                        reason="Scientific content or concept recipe changed; completed indexing is reused",
                    )
                    provenance = {
                        "note_fingerprint": digest(text),
                        "source_fingerprint": next(
                            (
                                r.get("source_signature") or r.get("fingerprint")
                                for r in [*state["papers"].values(), *state["works"].values()]
                                if r.get("note") == name
                            ),
                            None,
                        ),
                    }
                    if concept_registry.update(self.config, name, text, provenance, processor):
                        indexed += 1
                        self.save(state)
                concept_registry.reviewed(state["proposals"], notes)
                batches = concept_registry.batches(
                    self.config,
                    scientific_notes(notes),
                    changed,
                    sorted(snapshot),
                    passages,
                    statuses,
                )
                for number, payload in enumerate(batches, 1):
                    self.progress(
                        "reasoning",
                        batch=number,
                        focus_notes=len(changed),
                        reason="Changed scientific focus, stage recipe, or unfinished batch",
                    )
                    for role in ("linking", "synthesis", "maintenance"):
                        operation = digest(
                            [
                                content_recipe(self.config, role),
                                role,
                                {
                                    k: v
                                    for k, v in payload.items()
                                    if k
                                    not in {
                                        "existing_relationships",
                                        "document_status",
                                        "active_literature",
                                    }
                                },
                            ]
                        )
                        ledger = state.setdefault("reasoning_operations", {})
                        legacy_operation = digest(
                            [
                                stage_recipe(self.config, role, legacy=True),
                                role,
                                {
                                    k: v
                                    for k, v in payload.items()
                                    if k
                                    not in {
                                        "existing_relationships",
                                        "document_status",
                                        "active_literature",
                                    }
                                },
                            ]
                        )
                        if not full and legacy_operation in ledger and operation not in ledger:
                            ledger[operation] = dict(ledger[legacy_operation])
                        if not full and operation in ledger:
                            processor.hits += 1
                            continue
                        selection = {
                            "role": role,
                            "focus": payload["focus_fragment"]["note"],
                            "operation": operation,
                            "completed": False,
                            "reason": "explicit full sync"
                            if full
                            else "reasoning recipe changed"
                            if state.get("graph_recipe") != recipe
                            else "unfinished reasoning batch"
                            if state.get("graph_error")
                            else "scientific focus changed",
                        }
                        plan["reasoning_operations"].append(selection)
                        task_id, _ = processor.plan_inference(role, payload)
                        if startup and processor.calls >= self.config.processing(
                            "startup_max_operations"
                        ):
                            processor.tasks.update(
                                task_id,
                                "deferred",
                                reason="Startup inference budget; resume next Sync",
                            )
                            processor.deferred_operations.append(task_id)
                            graph_deferred = True
                            continue
                        try:
                            result = processor.run(
                                role,
                                payload,
                                lambda result: self.reasoning_changes(
                                    result, payload, state, notes
                                ),
                            )
                        except (AllowanceExhausted, OperationDeferred, RateLimited) as exc:
                            graph_error = str(exc)
                            self.save(state)
                            continue
                        registry, changes = self.reasoning_changes(result, payload, state, notes)
                        state["proposals"] = registry
                        selection["completed"] = True
                        ledger[operation] = {
                            "completed_at": now(),
                            "role": role,
                            "focus": payload["focus_fragment"]["note"],
                        }
                        self.save(state, changes)
            except (AllowanceExhausted, OperationDeferred, RateLimited) as exc:
                graph_error = str(exc)
            except (AuthenticationError, IntegrationError):
                raise
            except (VaultError, OSError) as exc:
                # A failed commit or conflict must never be treated as a recoverable inference failure.
                if (
                    read_notes(self.root) != self._baseline_notes
                    or inside(self.root, ".pipeline/transaction.json").exists()
                ):
                    raise
                graph_error = str(exc)
                warnings.append(f"Conceptual reasoning incomplete: {exc}")
            if not graph_error and not graph_deferred:
                state.update(
                    graph_digest=graph_signature(notes, recipe),
                    graph_notes={n: digest(scientific_text(t)) for n, t in notes.items()},
                    graph_recipe=recipe,
                    graph_membership=sorted(snapshot),
                    graph_retry_attempts=0,
                )
            changes = {}
            for revision in diagnostics(notes):
                proposal("revision", revision, state["proposals"], notes, changes)
            self.save(state, changes)
        elif not graph_needed or not notes:
            state["graph_digest"] = graph_signature(notes, recipe)
        refresh_pending(
            self.root, {p: v for p, v in state["proposals"].items() if p in reviews}, notes, reviews
        )
        state["graph_error"] = graph_error
        state["graph_deferred"] = bool(graph_deferred)
        if processor.blocked_providers or processor.deferred_operations:
            state["graph_retry_attempts"] = 0
        outstanding = {
            k: r.get("error")
            for group in (state["papers"], state["works"])
            for k, r in group.items()
            if r.get("error")
        }
        limitations = {
            k: r.get("completeness")
            for group in (state["papers"], state["works"])
            for k, r in group.items()
            if r.get("completeness") in {"partial", "metadata_only"}
        }
        complete = not (
            skipped
            or work_errors
            or failed
            or partial
            or graph_error
            or deferred
            or graph_deferred
            or outstanding
        )
        state["warnings"] = warnings
        if complete:
            state["last_success"] = now()
        self.save(state)
        plan.update(
            failed=failed,
            partial=partial,
            deferred_documents=deferred,
            reasoning_deferred=bool(graph_deferred),
            graph_error=graph_error,
            complete=complete,
            cache_hits=processor.hits,
            inference_operations=processor.calls,
            completed_operations=processor.completed_operations,
            completed_documents=successful_documents,
            inference_metadata=processor.operations,
            inference_failures=sum(
                r["validation_status"] == "failed" and not r.get("evaluation_only")
                for r in processor.operations
            ),
            evaluation_failures=processor.evaluation_failures,
            usage_by_agent=processor.usage_by_agent(),
            task_status=processor.tasks.summary(),
            deferred_operations=[
                r for r in processor.tasks.records.values() if r["status"] == "deferred"
            ],
            provider_interruptions=processor.blocked_providers,
            resume_instructions=(
                "After the provider allowance resets or the reported problem is fixed, run Sync (or startup synchronization). Completed tasks are retained; --full is not required."
                if not complete
                else None
            ),
            pending_claim_reviews=counts(self.merged_reviews(state, {}), state["proposals"])[
                "claim"
            ],
            pending_link_reviews=counts(self.merged_reviews(state, {}), state["proposals"])["link"],
            processing_failures=len(failed)
            + sum(len(e) for k, e in partial.items() if k not in failed)
            + int(bool(graph_error)),
        )
        final_reviews = self.merged_reviews(state, {})
        pending_total = sum(counts(final_reviews, state["proposals"]).values())
        approved_unapplied = sum(
            not r.get("applied")
            and not r.get("superseded_by")
            and not r.get("resolution")
            and final_reviews[p]["status"] == "approved"
            for p, r in state["proposals"].items()
        )
        retry_pending = any(
            r.get("status") in {"pending", "running", "deferred"}
            or (r.get("retryable") and r.get("attempts", 0) < self.retry_limit(r))
            for group in (state["papers"], state["works"])
            for r in group.values()
        )
        plan.update(
            status="incomplete_resumable"
            if (graph_error and graph_retry_allowed) or retry_pending or deferred or graph_deferred
            else "failed"
            if graph_error
            or any(
                r.get("status") == "failed"
                for group in (state["papers"], state["works"])
                for r in group.values()
            )
            else "completed_with_limitations"
            if limitations or outstanding
            else "completed_reviews_pending"
            if pending_total or approved_unapplied
            else "up_to_date",
            scientific_notes_changed=sorted(
                n
                for n, t in notes.items()
                if state.get("graph_notes", {}).get(n) != digest(scientific_text(t))
            )
            if graph_error
            else sorted(
                n
                for n, t in notes.items()
                if digest(scientific_text(self._run_initial_notes.get(n, "")))
                != digest(scientific_text(t))
            ),
            new_proposals=len(set(state["proposals"]) - self._run_initial_proposals),
            suppressed_duplicates=sum(
                r.get("suppressed_duplicates", 0) for r in state["proposals"].values()
            )
            - self._run_initial_duplicates,
            archived_reviews=self.archived_reviews,
            outstanding_failures=outstanding,
            extraction_limitations=limitations,
            reasoning_needed=bool(graph_needed),
            technical_complete=not (
                graph_error
                or retry_pending
                or deferred
                or failed
                or graph_deferred
                or any(
                    r.get("status") == "failed"
                    for group in (state["papers"], state["works"])
                    for r in group.values()
                )
            ),
            source_coverage_complete=not limitations and not outstanding,
            human_reviews_pending=pending_total,
            approved_unapplied=approved_unapplied,
        )
        if processor.blocked_providers or processor.deferred_operations or graph_deferred:
            plan.update(status="incomplete_resumable", complete=False, technical_complete=False)
        if not processor.calls and not changed_papers and not changed_works:
            plan["message"] = (
                "Vault is up to date; no inference was required."
                if not outstanding
                else "No eligible changes; retained source limitations are documented. No inference was required."
            )
        self.progress(
            "complete" if complete else "incomplete",
            processed=completed,
            remaining=0,
            skipped=len(skipped) + len(work_errors),
            failed=len(failed),
            cache_hits=processor.hits,
        )
        state["last_report"] = {**plan, "completed_at": now()}
        self.save(state)
        self.log("sync", state["last_report"])
        return plan

    def authenticate_agent(self, papers, works, graph_needed):
        if isinstance(self.agent, LLMRunner):
            roles = (["literature"] if papers else []) + (["manuscript"] if works else [])
            if graph_needed:
                roles += ["concepts", "linking", "synthesis", "maintenance"]
            self.agent.authenticate(roles)
        else:
            self.agent.authenticate()

    def process_paper(self, key, item, old, processor, state):
        """Checkpoint each attachment independently, then consolidate only available evidence."""
        attachment_states = old.setdefault("attachment_processing", {})
        pages, summaries, errors, unavailable = [], [], {}, {}
        for attachment in item["attachments"]:
            attachment_key = attachment["key"]
            if attachment["classification"] == "ignore":
                attachment_states[attachment_key] = {
                    "completeness": "metadata_only",
                    "ignored": True,
                    "reason": attachment.get("reason", "unsupported format or configured ignore"),
                }
                if (
                    self.config.data.get("attachments", {}).get("overrides", {}).get(attachment_key)
                    != "ignore"
                ):
                    unavailable[attachment_key] = attachment.get(
                        "reason", "unsupported attachment format"
                    )
                continue
            entry = {
                "source_fingerprint": attachment.get("sha256"),
                "classification": attachment["classification"],
                "completeness": "metadata_only",
                "recipe": stage_recipe(self.config, "literature"),
            }
            attachment_states[attachment_key] = entry
            if attachment.get("error"):
                unavailable[attachment_key] = attachment["error"]
                entry["error"] = attachment["error"]
                self.save(state)
                continue
            try:
                path = Path(attachment["path"])
                extracted = [
                    {**p, "classification": attachment["classification"]}
                    for p in processor.extract(path, attachment_key, attachment["sha256"])
                ]
                if file_hash(path) != attachment["sha256"]:
                    raise VaultError("PDF changed while extracting; retry sync")
                issue = coverage_error(extracted)
                entry.update(
                    completeness="partial",
                    extraction="partial" if issue else "complete",
                    summary="pending",
                    page_processing=page_coverage(extracted),
                    pages=[p["locator"] for p in usable_pages(extracted)],
                )
                pages.extend(extracted)
                if issue:
                    unavailable[attachment_key] = issue
                if not usable_pages(extracted):
                    entry.update(summary="unavailable", error=issue)
                    self.save(state)
                    continue
                result = processor.summarize(
                    "literature",
                    {
                        "item_key": key,
                        "metadata": item["metadata"],
                        "pages": extracted,
                        "attachments": [attachment],
                        "annotations": item.get("annotations", []),
                    },
                )
                if file_hash(path) != attachment["sha256"]:
                    raise VaultError("PDF changed while summarizing; retry sync")
                summaries.append(result)
                entry.update(
                    completeness="partial" if issue else "complete",
                    summary="complete",
                    error=issue,
                )
            except (
                AuthenticationError,
                IntegrationError,
                AllowanceExhausted,
                OperationDeferred,
                RateLimited,
            ):
                raise
            except (VaultError, OSError) as exc:
                errors[attachment_key] = str(exc)
                entry.update(completeness="failed", error=str(exc))
            self.save(state)
        # Attachment keys absent from the latest verified parent are retained as historical records.
        current = {a["key"] for a in item["attachments"]}
        for attachment_key, record in attachment_states.items():
            record["present"] = attachment_key in current
        if not summaries:
            old["completeness"] = (
                "partial"
                if any(r.get("incomplete") for r in pages)
                else "failed"
                if errors
                else "metadata_only"
            )
            return None, pages, errors, unavailable
        try:
            result = processor.combine("literature", summaries)
        except (AuthenticationError, AllowanceExhausted, OperationDeferred, RateLimited):
            raise
        except (VaultError, OSError) as exc:
            errors["consolidation"] = str(exc)
            # Detailed validated attachment results remain usable without claiming a full overview.
            result = {
                "title": summaries[0]["title"],
                "summary": "Parent consolidation incomplete; validated attachment summaries follow.\n\n"
                + "\n\n".join(s["summary"] for s in summaries),
                "evidence": [e for s in summaries for e in s["evidence"]],
                "claims": [c for s in summaries for c in s["claims"]],
            }
            processor.remember(result, [w for s in summaries for w in processor.warnings_for(s)])
        complete = not errors and not unavailable
        old["completeness"] = "complete" if complete else "partial"
        if not complete:
            verification = processor.warnings_for(result)
            result = deepcopy(result)
            result["summary"] = (
                "Processing coverage: partial. Scientific statements below concern only the processed "
                "pages and attachments. Unprocessed pages and missing or failed supplements were not read.\n\n"
                + result["summary"]
                + "\n\nIncomplete coverage: "
                + "; ".join(f"{k}: {v}" for k, v in sorted({**errors, **unavailable}.items()))
            )
            processor.remember(result, verification)
        return result, pages, errors, unavailable

    def reasoning_changes(self, result, payload, state, notes):
        """Validate against both the retrieved context and current full notes before caching."""
        if result.get("consolidations"):
            raise VaultError("Consolidations require an explicit concept review request")
        registry, changes = deepcopy(state["proposals"]), {}
        for candidate in result.get("links", []):
            warnings = validate_note_evidence(candidate["evidence"], payload["notes"])
            link_proposal(candidate, registry, notes, changes, warnings)
        for revision in result.get("revisions", []):
            if revision["note"] not in payload["notes"]:
                raise VaultError("Revision targets a note outside retrieved context")
            warnings = validate_note_evidence(
                revision["evidence"], payload["notes"]
            ) + validate_note_evidence(revision["evidence"], notes)
            if warnings:
                revision = {**revision, "validation_warnings": warnings}
            proposal("revision", revision, registry, notes, changes)
        return registry, changes

    def store_passages(self, key, fingerprint, pages):
        name = f".pipeline/cache/documents/{digest([key, fingerprint])}.json"
        atomic(inside(self.root, name), json.dumps(usable_pages(pages), ensure_ascii=False))
        return name

    def _commit_checked(self, changes: dict) -> None:
        if read_notes(self.root) != self._baseline_notes:
            raise VaultError(
                "Notes changed during processing; staged results were not integrated. Retry sync."
            )
        for name, before in self._baseline_files.items():
            if (
                not inside(self.root, name).exists()
                or inside(self.root, name).read_bytes() != before
            ):
                raise VaultError(f"File changed during processing: {name}; retry sync")
        for name in changes:
            if (
                name.startswith(("Reviews/", ".pipeline/review_history/"))
                and name not in self._baseline_files
                and inside(self.root, name).exists()
            ):
                raise VaultError(f"Review path already exists: {name}")
        commit(self.root, changes)

    def review_status(self) -> dict:
        state = deepcopy(self.state())
        reviews = read_reviews(self.root, state["proposals"])
        notes = read_notes(self.root)
        migrate_matching_dependencies(state["proposals"], notes)
        return {
            **readiness(self.root, state, notes, reviews, self.config),
            "history": [
                {
                    "id": p,
                    "path": r["path"],
                    "decision": reviews[p]["status"],
                    "outcome": r.get("application_outcome"),
                    "archived_at": r.get("archived_at"),
                }
                for p, r in state["proposals"].items()
                if r.get("archived_at")
            ],
        }

    def refresh_review(self, pid: str) -> dict:
        """Register a new pending version; never copy an earlier approval."""
        state = deepcopy(self.state())
        self.baseline(state)
        reviews = read_reviews(self.root, state["proposals"])
        notes = read_notes(self.root)
        record = state["proposals"].get(pid)
        if record and record.get("resolution"):
            return {
                "outcome": "already_closed",
                "review": record["path"],
                "path": record["payload"]["source"],
                "status": reviews[pid]["status"],
                "message": "This review is already closed. Its original decision remains in Review History.",
            }
        if not record or record["applied"] or record.get("superseded_by"):
            raise VaultError("Choose an active, unapplied review to refresh")
        if reviews[pid]["status"] == "rejected":
            raise VaultError("Rejected reviews are not refreshed automatically")
        changes = {}
        if record["kind"] == "link":
            payload = {
                k: v
                for k, v in record["payload"].items()
                if k not in {"revised_from", "validation_warnings"}
            }
            replacement = link_proposal(
                {**payload, "revised_from": pid}, state["proposals"], notes, changes
            )
            if replacement is None:
                concept = payload.get("target_concept")
                if concept:
                    catalog = concept_catalog(notes, state["proposals"], reviews)
                    current = catalog.get(payload["target"])
                    if (
                        not current
                        or not current["existing"]
                        or current["variants"][0] != concept
                        or current.get("scope_additions")
                    ):
                        return {
                            "outcome": "concept_comparison_needed",
                            "review": record["path"],
                            "path": record["path"],
                            "target": payload["target"],
                            "status": reviews[pid]["status"],
                            "message": "The relationship already exists, but this review proposes different concept definition text. It remains active: use Suggest consolidation in Review Readiness to compare the definitions.",
                        }
                record["resolution"] = {
                    "code": "already_represented",
                    "resolved_at": now(),
                    "source": payload["source"],
                    "target": payload["target"],
                    "relationship": payload["relationship"],
                    "observed_source_hash": digest(notes[payload["source"]]),
                }
                meta, body = frontmatter(
                    inside(self.root, record["path"]).read_text(encoding="utf-8")
                )
                changes[record["path"]] = markdown(
                    {**meta, "resolved": "already_represented"},
                    body
                    + "\n\n## Resolution\n\nThe exact source, target and relationship type are already represented in the source note. "
                    "Refresh closed this duplicate without applying or approving its draft. The original decision and proposal remain in history.\n",
                )
                self.save(state, changes)
                return {
                    "outcome": "already_represented",
                    "review": record["path"],
                    "path": payload["source"],
                    "source": payload["source"],
                    "target": payload["target"],
                    "relationship": payload["relationship"],
                    "status": reviews[pid]["status"],
                    "applied": False,
                    "message": "This relationship already exists. The duplicate review moved to history; no scientific notes or approval decisions were changed.",
                }
        elif record["kind"] == "claim":
            context = record["claim_context"]
            for path, expected in context["files"].items():
                if file_hash(self.config.authorized(path)) != expected:
                    raise VaultError(
                        "Claim source changed; synchronize it before refreshing its draft"
                    )
            current = state[context["category"]].get(context["key"], {})
            if (
                current.get("claim_review") != pid
                or current.get("fingerprint") != context["fingerprint"]
            ):
                raise VaultError("A newer source draft exists; review that draft instead")
            replacement = claim_proposal(
                record["payload"]["draft"],
                record["payload"]["validation_warnings"],
                context,
                state["proposals"],
                notes,
                changes,
                pid,
            )
            current["claim_review"] = replacement
        else:
            raise VaultError(
                "Only link and claim reviews can be refreshed; request a new consolidation for changed concepts"
            )
        record["superseded_by"] = replacement
        meta, body = frontmatter(inside(self.root, record["path"]).read_text(encoding="utf-8"))
        changes[record["path"]] = markdown({**meta, "superseded_by": replacement}, body)
        self.save(state, changes)
        return {
            "superseded": pid,
            "replacement": replacement,
            "path": state["proposals"][replacement]["path"],
            "status": "pending",
        }

    def reconsider_review(self, pid, evidence_note, evidence_quote):
        """Explicit human request with a new, verified supporting excerpt."""
        state = deepcopy(self.state())
        self.baseline(state)
        reviews = read_reviews(self.root, state["proposals"])
        record = state["proposals"].get(pid)
        if (
            not record
            or record["kind"] not in {"link", "revision"}
            or reviews[pid]["status"] != "rejected"
        ):
            raise VaultError("Choose a rejected link or advisory revision")
        notes = read_notes(self.root)
        evidence = {"note": evidence_note, "quote": evidence_quote}
        if not evidence_note or not evidence_quote or validate_note_evidence([evidence], notes):
            raise VaultError(
                "Supply a new exact excerpt using --evidence-note and --evidence-quote"
            )
        if evidence in record["payload"]["evidence"]:
            raise VaultError("Reconsideration needs a new supporting excerpt")
        payload = {
            **record["payload"],
            "evidence": [*record["payload"]["evidence"], evidence],
            "revised_from": pid,
        }
        changes = {}
        replacement = (
            link_proposal(payload, state["proposals"], notes, changes)
            if record["kind"] == "link"
            else proposal("revision", payload, state["proposals"], notes, changes)
        )
        if replacement is None:
            raise VaultError("Relationship is already represented")
        self.save(state, changes)
        return {
            "replacement": replacement,
            "path": state["proposals"][replacement]["path"],
            "status": "pending",
        }

    def suggest_consolidation(self, group_id: str | None = None, members=None) -> dict:
        from .consolidations import register_consolidation

        state = deepcopy(self.state())
        self.baseline(state)
        notes = read_notes(self.root)
        reviews = read_reviews(self.root, state["proposals"])
        if members:
            catalog = concept_catalog(notes, state["proposals"], reviews)
            paths = sorted(set(members))
            if len(paths) < 2 or any(p not in catalog for p in paths):
                raise VaultError("Select at least two distinct existing or proposed concept paths")
            chosen = [catalog[p] for p in paths]
            group = {
                "id": digest(sorted(m["signature"] for m in chosen))[:24],
                "members": chosen,
                "reason": "Explicitly selected for comparison; selection does not establish equivalence.",
            }
        else:
            groups = candidate_groups(
                notes, state["proposals"], reviews, state.get("concept_distinctions")
            )
            group = next((g for g in groups if g["id"] == group_id), None)
        if group is None:
            raise VaultError("Concept candidates changed; reopen Review Readiness")
        sources = {
            state["proposals"][pid]["payload"]["source"]
            for m in group["members"]
            for pid in m["proposals"]
        }
        context = {m["path"] for m in group["members"] if m["existing"]} | sources
        payload = {
            "consolidation_request": group,
            "notes": {p: notes[p] for p in sorted(context)},
            "related_links": [
                state["proposals"][pid]["payload"]
                for m in group["members"]
                for pid in m["proposals"]
            ],
        }
        processor = Processor(self.config, self.agent, reasoning_recipe(self.config), self.progress)
        if isinstance(self.agent, LLMRunner):
            self.agent.authenticate(["consolidation"])
        elif hasattr(self.agent, "authenticate"):
            self.agent.authenticate()

        def check(result):
            suggestions = result.get("consolidations", [])
            if len(suggestions) != 1:
                raise VaultError("Return exactly one consolidation suggestion")
            # Validation uses a temporary registry; a rejected result never reaches the queue.
            register_consolidation(suggestions[0], group, deepcopy(state), notes, {})

        result = processor.run("consolidation", payload, check)
        changes = {}
        pid = register_consolidation(result["consolidations"][0], group, state, notes, changes)
        if pid in reviews and (
            reviews[pid]["status"] != "pending" or state["proposals"][pid]["applied"]
        ):
            raise VaultError(
                "This exact consolidation has already been decided; inspect its history before requesting a different proposal"
            )
        for other, record in state["proposals"].items():
            if (
                other != pid
                and record["kind"] == "consolidation"
                and not record["applied"]
                and not record.get("superseded_by")
                and reviews.get(other, {}).get("status") != "rejected"
                and record["payload"]["members"] == state["proposals"][pid]["payload"]["members"]
            ):
                record["superseded_by"] = pid
                meta, body = frontmatter(
                    inside(self.root, record["path"]).read_text(encoding="utf-8")
                )
                changes[record["path"]] = markdown({**meta, "superseded_by": pid}, body)
        self.save(state, changes)
        return {"proposal": pid, "path": state["proposals"][pid]["path"], "status": "pending"}

    def apply(
        self, dry_run: bool = False, proposal_ids=None, ready_only=False, plan_token=None
    ) -> dict:
        state = deepcopy(self.state())
        self.baseline(state)
        reviews = read_reviews(self.root, state["proposals"])
        notes = read_notes(self.root)
        migrate_matching_dependencies(state["proposals"], notes)
        report = readiness(self.root, state, notes, reviews, self.config)
        approved = {
            p
            for p, r in state["proposals"].items()
            if not r["applied"]
            and not r.get("superseded_by")
            and not r.get("resolution")
            and reviews[p]["status"] == "approved"
        }
        selected = set(report["ready_ids"]) if ready_only else approved
        if ready_only and not selected and approved and not dry_run:
            raise VaultError(
                "No approved reviews are ready; inspect Review Readiness before applying"
            )
        if proposal_ids is not None:
            selected = set(proposal_ids)
            if not selected or not selected.issubset(approved):
                raise VaultError("Select only active, explicitly approved proposal IDs")
        blocked = [i for i in report["blocked"] if i["id"] in selected]
        can_apply = not blocked
        if blocked and not dry_run:
            raise VaultError(
                "Reviews need attention:\n"
                + "\n".join(f"{i['path']}: {i['reason']}" for i in blocked)
            )
        selected -= {i["id"] for i in blocked}
        changes, previews, destructive = apply_decisions(
            self.root, state, notes, select_reviews(reviews, selected)
        )
        archived = archive_finalized(self.root, state["proposals"], changes)
        token = digest(
            {
                "selected": sorted(selected),
                "changes": changes,
                "decisions": {p: reviews[p] for p in sorted(selected)},
            }
        )
        if plan_token is not None and token != plan_token:
            raise VaultError("Review preview changed; preview this selection again before applying")
        result = {
            "changes": sorted(changes),
            "impact_previews": previews,
            "destructive": destructive,
            "dry_run": dry_run,
            "archived_reviews": len(archived),
            "readiness": report,
            "selected_ids": sorted(selected),
            "can_apply": can_apply,
            "plan_token": token,
            "skipped": [
                i
                for i in report["items"]
                if i["decision"] == "approved" and i["id"] not in selected
            ],
            "diffs": {
                name: "".join(
                    difflib.unified_diff(
                        (
                            inside(self.root, name).read_text(encoding="utf-8")
                            if inside(self.root, name).exists()
                            else ""
                        ).splitlines(True),
                        (text or "").splitlines(True),
                        fromfile="before/" + name,
                        tofile="after/" + name,
                    )
                )
                for name, text in changes.items()
            },
        }
        if dry_run:
            return result
        if state.get("concept_registry"):
            concept_registry = Registry(state["concept_registry"])
            for name in set(concept_registry.data["documents"]) - notes.keys():
                concept_registry.remove(name)
            concept_registry.reviewed(state["proposals"], notes)
            # A link approval has already reviewed its proposed concept definition.
            # Index it deterministically rather than re-infer the same definition.
            if all(
                state["proposals"][p]["kind"] in {"link", "revision"} for p in selected
            ) and graph_signature(self._baseline_notes, reasoning_recipe(self.config)) == state.get(
                "graph_digest"
            ):

                class IndexOnly:
                    full = False

                    def run(self, role, payload, validator):
                        return {"concepts": []}

                for name in sorted(notes.keys() - self._baseline_notes.keys()):
                    text = scientific_text(notes[name])
                    concept_registry.update(
                        self.config, name, text, {"note_fingerprint": digest(text)}, IndexOnly()
                    )
                state["graph_digest"] = graph_signature(notes, reasoning_recipe(self.config))
                state["graph_notes"] = {n: digest(scientific_text(t)) for n, t in notes.items()}
        updated = self.merged_reviews(state, changes)
        changes["Home.md"] = self.dashboard(state, updated)
        changes[STATE] = json.dumps(state, ensure_ascii=False, indent=2)
        if destructive:
            result["backup"] = checkpoint(self.root, changes)
            # Persist the exact preview before deleting any note.
            atomic(inside(self.root, ".pipeline/last-impact.json"), json.dumps(result, indent=2))
        self._commit_checked(changes)
        self.log("apply", result)
        return result

    def status(self) -> dict:
        state = self.state()
        reviews = read_reviews(self.root, state["proposals"])
        documents = {}
        for category, records in (("papers", state["papers"]), ("works", state["works"])):
            documents[category] = {
                status: sorted(
                    k
                    for k, record in records.items()
                    if record.get("status", "successful") == status
                )
                for status in ("successful", "pending", "skipped", "failed")
            }
        return {
            "last_success": state["last_success"],
            "last_attempt": state.get("last_attempt"),
            "active_papers": len(state["membership"]),
            "processed_papers": sum(p.get("processed", False) for p in state["papers"].values()),
            "works": len(state["works"]),
            "documents": documents,
            "completeness": {
                coverage: sorted(
                    k
                    for k, record in state["papers"].items()
                    if record.get(
                        "completeness", "complete" if record.get("processed") else "metadata_only"
                    )
                    == coverage
                )
                for coverage in ("metadata_only", "partial", "complete", "failed")
            },
            "attachments": {
                k: r.get("attachment_processing", {}) for k, r in state["papers"].items()
            },
            "manuscript_processing": {
                k: {
                    "completeness": r.get("completeness"),
                    "page_processing": r.get("page_processing", []),
                }
                for k, r in state["works"].items()
            },
            "concept_registry": {
                k: len(state.get("concept_registry", {}).get(k, {}))
                for k in ("documents", "concepts", "reviewed_relationships")
            },
            "progress": read_json(inside(self.root, ".pipeline/progress.json")),
            "last_report": state.get("last_report"),
            "task_status": OperationStore(self.root).summary(),
            "usage_by_agent": (state.get("last_report") or {}).get("usage_by_agent", {}),
            "provider_interruptions": (state.get("last_report") or {}).get(
                "provider_interruptions", {}
            ),
            "resume_instructions": (state.get("last_report") or {}).get("resume_instructions"),
            "deferred_operations": [
                r for r in OperationStore(self.root).records.values() if r["status"] == "deferred"
            ],
            "status": self.synchronization_status(state, reviews, read_notes(self.root)),
            "review_history": [
                {
                    "id": p,
                    "path": r["path"],
                    "decision": r.get("decision"),
                    "outcome": r.get("application_outcome"),
                }
                for p, r in state["proposals"].items()
                if r.get("archived_at")
            ],
            "completed_operations": (state.get("last_report") or {}).get("completed_operations", 0),
            "processing_failures": (state.get("last_report") or {}).get("processing_failures", 0),
            "background_runs": [
                read_json(inside(self.root, path.relative_to(self.root).as_posix()))
                for path in sorted(
                    inside(self.root, ".pipeline/runs").glob("*.json"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )[:5]
            ],
            "document_errors": {
                k: p["error"]
                for records in (state["papers"], state["works"])
                for k, p in records.items()
                if p.get("error")
            },
            "reasoning_pending": bool(state.get("graph_error"))
            or state.get("graph_digest")
            != graph_signature(read_notes(self.root), reasoning_recipe(self.config)),
            "reasoning_error": state.get("graph_error"),
            "pending": counts(reviews, state["proposals"]),
            "pending_claim_reviews": counts(reviews, state["proposals"])["claim"],
            "pending_link_reviews": counts(reviews, state["proposals"])["link"],
            "warnings": state["warnings"],
            "transaction_requires_recovery": inside(
                self.root, ".pipeline/transaction.json"
            ).exists(),
        }

    def doctor(self) -> dict:
        checks = {
            "environment": environment(),
            "python": __import__("sys").executable,
            "config_file": str(self.root / "config.yaml"),
            "blocking_errors": [],
            "warnings": [],
            "ocr": ocr_diagnostics(self.config.ocr),
            "pdf_pages": {},
            "extraction_diagnostic_note": "Native text inspection only; doctor does not run OCR. The configured OCR setting is unchanged and applies during synchronization. Availability does not verify language data or recognition quality.",
        }
        if checks["ocr"]["status"] == "unavailable":
            checks["warnings"].append(
                "OCR is enabled but unavailable; usable native text will be retained with partial coverage"
            )
        for package in ("yaml", "pymupdf", "jsonschema"):
            checks[package] = "ok" if importlib.util.find_spec(package) else "missing"
            if checks[package] != "ok":
                checks["blocking_errors"].append(f"Missing Python dependency: {package}")
        try:
            snapshot = self.zotero.snapshot()
            checks["zotero"] = "ok"
            checks["attachments"] = getattr(self.zotero, "attachment_diagnostics", [])
            for key, item in snapshot.items():
                for attachment in item["attachments"]:
                    if attachment.get("error"):
                        checks["warnings"].append(
                            f"Paper {key}, attachment {attachment['key']}: {attachment['error']}"
                        )
                    elif attachment["classification"] == "ignore":
                        checks["warnings"].append(
                            f"Attachment {attachment['key']}: ignored or unsupported format"
                        )
                    elif checks["pymupdf"] == "ok":
                        try:
                            pages = inspect_pdf(
                                Path(attachment["path"]),
                                attachment["key"],
                                self.config.ocr,
                            )
                            checks["pdf_pages"][attachment["key"]] = page_coverage(pages)
                            issue = coverage_error(pages)
                            if issue:
                                checks["warnings"].append(
                                    f"Attachment {attachment['key']}: {issue}"
                                )
                        except (VaultError, OSError) as exc:
                            checks["warnings"].append(f"Attachment {attachment['key']}: {exc}")
                if not any(a["classification"] != "ignore" for a in item["attachments"]):
                    checks["warnings"].append(f"Paper {key}: no processable PDF")
        except (VaultError, OSError) as exc:
            checks["zotero"] = str(exc)
            checks["blocking_errors"].append(str(exc))
        try:
            registered = self.config.works()
        except VaultError as exc:
            checks["blocking_errors"].append(str(exc))
            registered = []
        checks["manuscripts"] = []
        for work in registered:
            try:
                path = self.config.authorized(work["source_path"])
                document = manuscript(
                    self.config,
                    work,
                    lambda path, source, sha: inspect_pdf(path, source, self.config.ocr),
                )
                issue = coverage_error(document["pages"])
                if issue:
                    checks["warnings"].append(f"Manuscript {work['id']}: {issue}")
                checks["manuscripts"].append(
                    {
                        "id": work["id"],
                        "original": work["source_path"],
                        "resolved_path": str(path),
                        "status": "partial" if issue else "ok",
                        "page_processing": page_coverage(document["pages"]),
                    }
                )
            except (VaultError, OSError) as exc:
                checks["warnings"].append(f"Manuscript {work['id']}: {exc}")
        self.provider_doctor(checks)
        for role in ("literature", "manuscript", "concepts", "linking", "synthesis", "maintenance"):
            if payload_budget(self.config, role) < 1500:
                checks["blocking_errors"].append(
                    f"{role}: input limit cannot fit instructions/schema/output reserve plus source context"
                )
        checks["path_translation"] = (
            "wslpath -u for Windows paths" if environment() == "WSL" else "native"
        )
        if environment() == "WSL":
            checks["wsl_validation"] = {
                "local_api": "reachable" if checks.get("zotero") == "ok" else "unreachable",
                "vault_filesystem": "readable"
                if inside(self.root, "config.yaml").is_file()
                else "unavailable",
                "attachment_access": "validated by opening and hashing accessible files; failures listed individually",
                "networking_mode": "not assumed; loopback API reachability was tested",
            }
        return checks

    def provider_doctor(self, checks):
        if isinstance(self.agent, LLMRunner):
            checks["providers"] = self.agent.diagnostics()
            checks["agent_providers"] = {
                role: self.config.runtime(role)[0]
                for role in (
                    "literature",
                    "manuscript",
                    "concepts",
                    "linking",
                    "synthesis",
                    "maintenance",
                )
            }
            for provider, record in checks["providers"].items():
                errors = [record[k] for k in ("error", "configuration_error") if record.get(k)]
                if record["required"]:
                    checks["blocking_errors"].extend(f"{provider}: {error}" for error in errors)
                elif errors:
                    checks["warnings"].append(
                        f"Optional {provider} provider unavailable or unconfigured"
                    )
            codex = checks["providers"]["codex"]
            checks["codex_auth"] = codex["authentication"]
            checks["codex"] = codex.get("command")
            checks["codex_flags"] = codex.get("flags", "unavailable")
            return
        # Injected runners implementing the original API remain supported.
        try:
            prefix = self.agent.command_prefix()
            help_result = subprocess.run(
                [*prefix, "exec", "--help"],
                capture_output=True,
                text=True,
                timeout=15,
                cwd=self.root,
            )
            required = ["--ignore-user-config", "--ignore-rules", "--ephemeral", "--output-schema"]
            missing = [flag for flag in required if flag not in help_result.stdout]
            if help_result.returncode or missing:
                raise VaultError("Codex CLI lacks required flags: " + ", ".join(missing))
            checks.update(codex=prefix, codex_flags="ok")
            self.agent.authenticate()
            checks["codex_auth"] = "ok"
        except (VaultError, OSError, subprocess.TimeoutExpired) as exc:
            checks["codex_auth"] = str(exc)
            checks["blocking_errors"].append(str(exc))

    def log(self, command: str, details: dict) -> None:
        path = inside(self.root, ".pipeline/logs/events.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps({"time": now(), "command": command, **details}, ensure_ascii=False)
                + "\n"
            )

    def report_error(self, message: str) -> None:
        self.log("error", {"error": message})
        try:
            if inside(self.root, ".pipeline/transaction.json").exists():
                return
            state = self.state()
            processor = getattr(self, "current_processor", None)
            state["last_report"] = {
                **getattr(self, "current_plan", {}),
                "status": "failed",
                "complete": False,
                "technical_complete": False,
                "error": message,
                "completed_at": now(),
                "inference_operations": processor.calls if processor else 0,
                "cache_hits": processor.hits if processor else 0,
                "completed_operations": processor.completed_operations if processor else 0,
                "inference_metadata": processor.operations if processor else [],
                "usage_by_agent": processor.usage_by_agent() if processor else {},
                "task_status": processor.tasks.summary() if processor else {},
                "archived_reviews": getattr(self, "archived_reviews", 0),
                "new_proposals": len(
                    set(state["proposals"])
                    - getattr(self, "_run_initial_proposals", set(state["proposals"]))
                ),
            }
            self.baseline(state)
            self.save(state)
            atomic(
                inside(self.root, ".pipeline/progress.json"),
                json.dumps({"time": now(), "stage": "failed", "error": message}),
            )
            reviews = read_reviews(self.root, state["proposals"])
            atomic(inside(self.root, "Home.md"), self.dashboard(state, reviews, message))
        except (VaultError, OSError):
            pass  # Never hide the original failure if dashboard/reviews are damaged.
