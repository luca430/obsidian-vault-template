"""One-shot orchestration. Source snapshots and generated files commit together."""

from __future__ import annotations

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
from .config import Config
from .notes import diagnostics, impact, read_notes, summary_content, update_note
from .paths import environment
from .processing import Processor
from .recipes import reasoning_recipe, stage_recipe
from .registry import Registry
from .reviews import (
    apply_decisions,
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
        if not isinstance(state, dict) or state.get("schema") != 1:
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
            f"Errors: {1 if error else 0}",
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
                *[
                    f"agents/{r}.md"
                    for r in (
                        "literature",
                        "manuscript",
                        "concepts",
                        "linking",
                        "synthesis",
                        "maintenance",
                    )
                ],
                *[r["path"] for r in state["proposals"].values()],
            ]
            if inside(self.root, n).exists()
        }

    def save(self, state, changes=None):
        changes = dict(changes or {})
        try:
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
                self._baseline_files[name] = text.encode("utf-8")

    def sync(self, full: bool = False, dry_run: bool = False, startup: bool = False) -> dict:
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
        recipe = reasoning_recipe(self.config)
        summary_recipe = self.recipe("literature")
        manuscript_recipe = self.recipe("manuscript")
        processor = Processor(self.config, self.agent, recipe, self.progress, full)
        changed_papers = [
            key
            for key, item in snapshot.items()
            if full
            or key not in state["membership"]
            or state["papers"].get(key, {}).get("fingerprint")
            != digest([item["fingerprint"], summary_recipe])
            or state["papers"].get(key, {}).get("status", "successful") != "successful"
            or state["papers"].get(key, {}).get("completeness", "complete") != "complete"
            or (
                f"Literature/{key}.md" not in notes
                and not state["papers"].get(key, {}).get("claim_review")
            )
        ]
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
                    and old.get("status", "successful") == "successful"
                    and old.get("completeness", "complete") == "complete"
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
            changed_works.append(key)
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
            if hasattr(self.agent, "authenticate"):
                try:
                    self.authenticate_agent(changed_papers, changed_works, not startup)
                except (VaultError, OSError) as exc:
                    blockers.append(str(exc))
        personal = {n: digest(t) for n, t in notes.items() if n.startswith("My Notes/")}
        graph_input = digest([notes, sorted(snapshot), recipe])
        graph_needed = bool(notes or changed_papers or changed_works) and (
            full or graph_input != state["graph_digest"] or bool(changed_papers or changed_works)
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
            "startup": startup,
            "reasoning_deferred": bool(startup and graph_needed),
        }
        if dry_run:
            return plan
        # Authenticate globally before accepting work requiring inference. Mock agents need no session.
        if (
            any(k not in skipped for k in changed_papers) or works or (graph_needed and notes)
        ) and hasattr(self.agent, "authenticate"):
            self.authenticate_agent(changed_papers, changed_works, graph_needed and not startup)
        for key in removed:
            if key in state["papers"]:
                old = state["papers"][key]
                old["removal_epoch"] = old.get("removal_epoch", 0) + 1
                old["disposition"] = "absent"
        for key in changed_papers:
            state["papers"].setdefault(
                key,
                {
                    "note": f"Literature/{key}.md",
                    "processed": False,
                    "removal_epoch": 0,
                    "completeness": "metadata_only",
                },
            )
            state["papers"][key].update(status="pending", disposition="active")
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
                state["works"].setdefault(work["id"], {})["status"] = "pending"
        state.update(
            identity=self.config.identity,
            server=self.zotero.server,
            membership=sorted(snapshot),
            personal=personal,
            warnings=warnings,
            last_attempt=now(),
        )
        # Membership is complete independently of attachment processing; never infer removals from I/O.
        self.save(state)
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
            try:
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
                    old.update(status="failed", error=error)
                else:
                    skipped[key] = error
                    old.update(status="skipped", error=error)
                warnings.append(f"Paper {key}: {error}; existing note retained.")
                self.save(state)
                completed += 1
                continue
            path = f"Literature/{key}.md"
            changes = {}
            verification = processor.warnings_for(result)
            if verification:
                context = {
                    "note": path,
                    "identity": f"zotero:{key}",
                    "kind": "literature",
                    "category": "papers",
                    "key": key,
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
            )
            if not verification:
                old["passages"] = self.store_passages(key, item["fingerprint"], pages)
            self.save(state, changes)
            completed += 1
            successful_documents += 1
        for key in scheduled_works:
            self.progress(
                "manuscripts", current=key, processed=completed, remaining=total - completed
            )
            old = state["works"][key]
            if key in work_errors:
                old.update(status="skipped", completeness="metadata_only", error=work_errors[key])
                self.save(state)
                completed += 1
                continue
            source = works[key]
            issue = coverage_error(source["pages"])
            old["page_processing"] = page_coverage(source["pages"])
            if issue:
                partial[f"work:{key}"] = {"pages": issue}
            try:
                result = processor.summarize("manuscript", source)
                if any(file_hash(Path(p)) != h for p, h in source["files"].items()):
                    raise VaultError("Manuscript changed while summarizing; retry sync")
                content = summary_content(result, source["pages"], source["metadata"])
            except (AuthenticationError, IntegrationError):
                raise
            except (OSError, VaultError) as exc:
                failed[f"work:{key}"] = str(exc)
                old.update(
                    status="failed", completeness="partial" if issue else "failed", error=str(exc)
                )
                warnings.append(f"Manuscript {key} failed: {exc}")
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
            )
            if not verification:
                old["passages"] = self.store_passages(
                    f"work-{key}", source["fingerprint"], source["pages"]
                )
            self.save(state, changes)
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
        graph_error = None
        if graph_needed and notes and not startup:
            prior = state.get("graph_notes", {})
            changed = {n for n, t in notes.items() if full or prior.get(n) != digest(t)}
            changed.update(
                f"Literature/{k}.md" for k in changed_papers if f"Literature/{k}.md" in notes
            )
            changed.update(f"My Work/{k}.md" for k in changed_works if f"My Work/{k}.md" in notes)
            if (
                state.get("graph_recipe") != recipe
                or removed
                or state.get("graph_error")
                or set(prior) - set(notes)
                or state.get("graph_membership") != sorted(snapshot)
            ):
                changed = set(notes)
            passages = {}
            for record in [*state["papers"].values(), *state["works"].values()]:
                if record.get("passages") and record.get("note") in notes:
                    passages[record["note"]] = read_json(inside(self.root, record["passages"]), [])
            statuses = {
                r["note"]: {
                    "processing": r.get("status", "successful"),
                    "collection": r.get("disposition", "registered"),
                    "completeness": r.get("completeness", "complete"),
                    "source_fingerprint": r.get("fingerprint"),
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
                for name, text in notes.items():
                    self.progress("concepts", current=name)
                    provenance = {"note_fingerprint": digest(text), **statuses.get(name, {})}
                    if concept_registry.update(self.config, name, text, provenance, processor):
                        indexed += 1
                        if indexed % 20 == 0:
                            self.save(state)
                concept_registry.reviewed(state["proposals"])
                batches = concept_registry.batches(
                    self.config, notes, changed, sorted(snapshot), passages, statuses
                )
                for number, payload in enumerate(batches, 1):
                    self.progress("reasoning", batch=number, focus_notes=len(changed))
                    for role in ("linking", "synthesis", "maintenance"):
                        result = processor.run(
                            role,
                            payload,
                            lambda result: self.reasoning_changes(result, payload, state, notes),
                        )
                        registry, changes = self.reasoning_changes(result, payload, state, notes)
                        state["proposals"] = registry
                        if changes:
                            self.save(state, changes)
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
            if not graph_error:
                state.update(
                    graph_digest=digest([notes, sorted(snapshot), recipe]),
                    graph_notes={n: digest(t) for n, t in notes.items()},
                    graph_recipe=recipe,
                    graph_membership=sorted(snapshot),
                )
            changes = {}
            for revision in diagnostics(notes):
                proposal("revision", revision, state["proposals"], notes, changes)
            self.save(state, changes)
        elif not graph_needed or not notes:
            state["graph_digest"] = digest([notes, sorted(snapshot), recipe])
        refresh_pending(
            self.root, {p: v for p, v in state["proposals"].items() if p in reviews}, notes, reviews
        )
        state["graph_error"] = graph_error
        complete = not (
            skipped
            or work_errors
            or failed
            or partial
            or graph_error
            or deferred
            or (startup and graph_needed)
        )
        state["warnings"] = warnings
        if complete:
            state["last_success"] = now()
        self.save(state)
        plan.update(
            failed=failed,
            partial=partial,
            deferred_documents=deferred,
            reasoning_deferred=bool(startup and graph_needed),
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
            pending_claim_reviews=counts(self.merged_reviews(state, {}), state["proposals"])[
                "claim"
            ],
            pending_link_reviews=counts(self.merged_reviews(state, {}), state["proposals"])["link"],
            processing_failures=len(failed)
            + sum(len(e) for k, e in partial.items() if k not in failed)
            + int(bool(graph_error)),
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
            except (AuthenticationError, IntegrationError):
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
        except AuthenticationError:
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
                name.startswith("Reviews/")
                and name not in self._baseline_files
                and inside(self.root, name).exists()
            ):
                raise VaultError(f"Review path already exists: {name}")
        commit(self.root, changes)

    def apply(self, dry_run: bool = False) -> dict:
        state = deepcopy(self.state())
        reviews = read_reviews(self.root, state["proposals"])
        notes = read_notes(self.root)
        for pid, record in state["proposals"].items():
            if (
                record["kind"] == "claim"
                and not record["applied"]
                and not record.get("superseded_by")
                and reviews[pid]["status"] == "approved"
            ):
                for path, expected in record["claim_context"]["files"].items():
                    if file_hash(self.config.authorized(path)) != expected:
                        raise VaultError(
                            "Stale claim approval: source file changed; sync and review again"
                        )
        changes, previews, destructive = apply_decisions(self.root, state, notes, reviews)
        result = {
            "changes": sorted(changes),
            "impact_previews": previews,
            "destructive": destructive,
            "dry_run": dry_run,
        }
        if dry_run:
            return result
        if state.get("concept_registry"):
            concept_registry = Registry(state["concept_registry"])
            for name in set(concept_registry.data["documents"]) - notes.keys():
                concept_registry.remove(name)
            concept_registry.reviewed(state["proposals"])
        updated = self.merged_reviews(state, changes)
        changes["Home.md"] = self.dashboard(state, updated)
        changes[STATE] = json.dumps(state, ensure_ascii=False, indent=2)
        if destructive:
            result["backup"] = checkpoint(self.root, changes)
            # Persist the exact preview before deleting any note.
            atomic(inside(self.root, ".pipeline/last-impact.json"), json.dumps(result, indent=2))
        commit(self.root, changes)
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
            != digest(
                [read_notes(self.root), sorted(state["membership"]), reasoning_recipe(self.config)]
            ),
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
            state = self.state()
            reviews = read_reviews(self.root, state["proposals"])
            atomic(inside(self.root, "Home.md"), self.dashboard(state, reviews, message))
        except (VaultError, OSError):
            pass  # Never hide the original failure if dashboard/reviews are damaged.
