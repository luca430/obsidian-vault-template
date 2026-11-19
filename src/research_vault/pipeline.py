"""One-shot orchestration. Source snapshots and generated files commit together."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from copy import deepcopy
from pathlib import Path

from .agents import Codex, validate_note_evidence, validate_output, validate_summary
from .config import Config
from .notes import diagnostics, impact, read_notes, summary_content, update_note
from .reviews import (
    apply_decisions,
    checkpoint,
    counts,
    link_proposal,
    proposal,
    read_reviews,
    refresh_pending,
)
from .sources import manuscript, pdf
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
    def __init__(self, config: Config, zotero=None, agent=None):
        self.config, self.root = config, config.root
        self.zotero = zotero if zotero is not None else Zotero(config)
        self.agent = agent if agent is not None else Codex(config)

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

    def recipe(self) -> str:
        files = [
            inside(self.root, "AGENTS.md"),
            *inside(self.root, "agents").glob("*.md"),
            *Path(__file__).parent.glob("*.py"),
        ]
        return digest(
            [sorted((p.name, file_hash(p)) for p in files), self.config.data.get("codex", {})]
        )

    def run_agent(self, role: str, payload: dict) -> dict:
        return validate_output(role, self.agent.run(role, payload))

    def dashboard(self, state: dict, reviews: dict, error: str | None = None) -> str:
        path = inside(self.root, "Home.md")
        text = path.read_text(encoding="utf-8")
        pending = counts(reviews, state["proposals"])
        lines = [
            f"Last successful synchronization: {state['last_success'] or 'never'}",
            f"Processed papers: {sum(p.get('processed', False) for p in state['papers'].values())}",
            f"Active collection members: {len(state['membership'])}",
            f"Pending links: {pending['link']}",
            f"Pending removals: {pending['removal']}",
            f"Pending revisions: {pending['revision']}",
            f"Errors: {1 if error else 0}",
        ]
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

    def sync(self, full: bool = False, dry_run: bool = False) -> dict:
        state = deepcopy(self.state())
        reviews = read_reviews(self.root, state["proposals"])
        if getattr(self.zotero, "server", None) is None:
            self.zotero.server = state.get("server")
        snapshot = self.zotero.snapshot()  # Any retrieval failure aborts before removals.
        if state.get("server") and self.zotero.server and state["server"] != self.zotero.server:
            raise VaultError("Zotero instance differs from stored state; use a new vault")
        notes = read_notes(self.root)
        self._baseline_notes = deepcopy(notes)
        self._baseline_files = {
            n: inside(self.root, n).read_bytes()
            for n in ["Home.md", *[r["path"] for r in state["proposals"].values()]]
        }
        recipe = self.recipe()
        changed_papers = [
            key
            for key, item in snapshot.items()
            if full
            or key not in state["membership"]
            or state["papers"].get(key, {}).get("fingerprint")
            != digest([item["fingerprint"], recipe])
            or f"Literature/{key}.md" not in notes
        ]
        removed = sorted(set(state["membership"]) - set(snapshot))
        works, changed_works, warnings = {}, [], []
        for key, item in snapshot.items():
            for attachment in item["attachments"]:
                if attachment["classification"] == "ignore":
                    warnings.append(
                        f"Ignored attachment {attachment['key']} on {key}: {attachment.get('reason', 'configured ignore')}"
                    )
        registered = self.config.works()
        for work in registered:
            key = work["id"]
            old = state["works"].get(key, {})
            try:
                files = old.get("files", {})
                unchanged = (
                    not full
                    and old.get("metadata") == work
                    and old.get("recipe") == recipe
                    and old.get("source") == str(self.config.authorized(work["source_path"]))
                    and files
                    and all(
                        self.config.authorized(p).is_file()
                        and file_hash(self.config.authorized(p)) == h
                        for p, h in files.items()
                    )
                    and f"My Work/{key}.md" in notes
                )
                if unchanged:
                    continue
                source = manuscript(self.config, work)
            except (OSError, VaultError) as exc:
                # A previously registered disappearing source is a warning, never a deletion.
                if old and not self.config.authorized(work["source_path"]).exists():
                    warnings.append(f"Manuscript {key} disappeared; its summary was retained.")
                    continue
                raise VaultError(f"Manuscript {key}: {exc}") from exc
            works[key] = source
            changed_works.append(key)
        for key in state["works"]:
            if key not in {w["id"] for w in registered}:
                warnings.append(f"Work {key} is no longer registered; its summary was retained.")
        personal = {
            name: digest(text) for name, text in notes.items() if name.startswith("My Notes/")
        }
        graph_input = digest([notes, sorted(snapshot), recipe])
        graph_needed = bool(notes or changed_papers or changed_works) and (
            full or graph_input != state["graph_digest"] or bool(changed_papers or changed_works)
        )
        for key in changed_papers:
            if not any(a["classification"] != "ignore" for a in snapshot[key]["attachments"]):
                warnings.append(
                    f"Paper {key} has no processable PDF; synchronization will fail until a PDF is available."
                )
        plan = {
            "papers": changed_papers,
            "works": changed_works,
            "removed": removed,
            "personal_notes_changed": personal != state["personal"],
            "reasoning_needed": graph_needed,
            "warnings": warnings,
            "dry_run": dry_run,
        }
        if dry_run:
            return plan
        changes: dict[str, str | None] = {}
        for key in changed_papers:
            item = snapshot[key]
            pages = []
            for attachment in item["attachments"]:
                if attachment["classification"] == "ignore":
                    continue
                path = Path(attachment["path"])
                pages += [
                    {**p, "classification": attachment["classification"]}
                    for p in pdf(path, attachment["key"])
                ]
                if file_hash(path) != attachment["sha256"]:
                    raise VaultError("PDF changed while extracting; retry sync")
            if not pages:
                raise VaultError(
                    f"Paper {key} has no readable main/supplementary PDF. Attach/download a PDF or configure a supported source; no fabricated summary was made."
                )
            result = self.run_agent(
                "literature",
                {
                    "item_key": key,
                    "metadata": item["metadata"],
                    "pages": pages,
                    "annotations": item.get("annotations", []),
                    "attachments": item["attachments"],
                },
            )
            validate_summary(result, pages)
            if any(
                file_hash(Path(a["path"])) != a["sha256"]
                for a in item["attachments"]
                if a["classification"] != "ignore"
            ):
                raise VaultError("PDF changed while summarizing; retry sync")
            if {p["source"] for p in pages} - {e["source"] for e in result["evidence"]}:
                raise VaultError(
                    "Literature summary must provide evidence from every processed PDF, including supplements"
                )
            path = f"Literature/{key}.md"
            content = summary_content(
                result, pages, {"zotero": item["metadata"], "attachments": item["attachments"]}
            )
            notes[path] = update_note(
                notes.get(path), f"zotero:{key}", "literature", result["title"], content
            )
            changes[path] = notes[path]
            old = state["papers"].get(key, {})
            state["papers"][key] = {
                "fingerprint": digest([item["fingerprint"], recipe]),
                "note": path,
                "processed": True,
                "disposition": "active",
                "files": item["attachments"],
                "removal_epoch": old.get("removal_epoch", 0),
            }
        for key in changed_works:
            source = works[key]
            result = self.run_agent("manuscript", source)
            validate_summary(result, source["pages"])
            if any(file_hash(Path(p)) != h for p, h in source["files"].items()):
                raise VaultError("Manuscript changed while summarizing; retry sync")
            path = f"My Work/{key}.md"
            notes[path] = update_note(
                notes.get(path),
                f"work:{key}",
                "manuscript",
                result["title"],
                summary_content(result, source["pages"], source["metadata"]),
            )
            changes[path] = notes[path]
            state["works"][key] = {
                "fingerprint": source["fingerprint"],
                "files": source["files"],
                "metadata": source["metadata"],
                "recipe": recipe,
                "source": str(self.config.authorized(source["metadata"]["source_path"])),
                "note": path,
            }
        for key in removed:
            state["papers"][key]["removal_epoch"] = state["papers"][key].get("removal_epoch", 0) + 1
            state["papers"][key]["disposition"] = "absent"
        # Re-evaluate pending removal impact when graph/user notes change.
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
        if graph_needed:
            payload = {
                "notes": notes,
                "active_literature": sorted(snapshot),
                "note_categories": {
                    "Literature": "external findings",
                    "My Work": "manuscript claims",
                    "My Notes": "personal hypotheses",
                    "Concepts": "reviewed concepts",
                },
            }
            links = self.run_agent("linking", payload)
            for link in links["links"]:
                link_proposal(link, state["proposals"], notes, changes)
            for role in ("synthesis", "maintenance"):
                result = self.run_agent(role, payload)
                for revision in result["revisions"]:
                    if revision["note"] not in notes:
                        raise VaultError("Revision targets an absent note")
                    validate_note_evidence(revision["evidence"], notes)
                    proposal("revision", revision, state["proposals"], notes, changes)
            for revision in diagnostics(notes):
                proposal("revision", revision, state["proposals"], notes, changes)
        # Refresh only existing reviews explicitly left pending by the user.
        refresh_pending(
            self.root, {p: v for p, v in state["proposals"].items() if p in reviews}, notes, reviews
        )
        state.update(
            identity=self.config.identity,
            server=self.zotero.server,
            membership=sorted(snapshot),
            personal=personal,
            graph_digest=digest([notes, sorted(snapshot), recipe]),
            last_success=now(),
            warnings=warnings,
        )
        updated_reviews = self.merged_reviews(state, changes)
        changes["Home.md"] = self.dashboard(state, updated_reviews)
        changes[STATE] = json.dumps(state, ensure_ascii=False, indent=2)
        self._commit_checked(changes)
        self.log("sync", {**plan, "completed_at": state["last_success"]})
        return plan

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
        changes, previews, destructive = apply_decisions(self.root, state, notes, reviews)
        result = {
            "changes": sorted(changes),
            "impact_previews": previews,
            "destructive": destructive,
            "dry_run": dry_run,
        }
        if dry_run:
            return result
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
        return {
            "last_success": state["last_success"],
            "active_papers": len(state["membership"]),
            "processed_papers": len(state["papers"]),
            "works": len(state["works"]),
            "pending": counts(reviews, state["proposals"]),
            "warnings": state["warnings"],
            "transaction_requires_recovery": inside(
                self.root, ".pipeline/transaction.json"
            ).exists(),
        }

    def doctor(self) -> dict:
        checks = {}
        for package in ("yaml", "fitz", "jsonschema"):
            checks[package] = "ok" if importlib.util.find_spec(package) else "missing"
        for name, operation in [
            (
                "manuscripts",
                lambda: [manuscript(self.config, work) for work in self.config.works()],
            ),
            ("codex", self.agent.available),
            ("zotero", self.zotero.snapshot),
        ]:
            try:
                operation()
                checks[name] = "ok"
            except (VaultError, OSError) as exc:
                checks[name] = str(exc)
        try:
            prefix = self.agent.command_prefix()
            help_result = subprocess.run(
                [*prefix, "exec", "--help"], capture_output=True, text=True, timeout=15
            )
            required = ["--ignore-user-config", "--ignore-rules", "--ephemeral", "--output-schema"]
            missing = [f for f in required if f not in help_result.stdout]
            checks["codex_flags"] = ("missing: " + ", ".join(missing)) if missing else "ok"
            auth = subprocess.run(
                [*prefix, "login", "status"], capture_output=True, text=True, timeout=15
            )
            checks["codex_auth"] = "ok" if auth.returncode == 0 else "Run codex login in a terminal"
        except (VaultError, OSError, subprocess.TimeoutExpired) as exc:
            checks["codex_auth"] = str(exc)
        return checks

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
