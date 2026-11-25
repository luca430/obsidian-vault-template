"""Persistent proposals; only deterministic code may apply user decisions."""

from __future__ import annotations

import json
import subprocess
import uuid
import zipfile
from pathlib import Path

from .agents import no_edges, normalized, validate_note_evidence, validate_output, validate_summary
from .notes import impact, new_note, references, summary_content, update_note, valid_note
from .sources import usable_pages
from .storage import (
    VaultError,
    digest,
    file_hash,
    frontmatter,
    inside,
    markdown,
    now,
    replace_section,
)

KINDS = {"link": "Links", "claim": "Revisions", "removal": "Removals", "revision": "Revisions"}


def proposal(kind: str, payload: dict, registry: dict, notes: dict[str, str], changes: dict) -> str:
    # Wording changes alone cannot resurrect a rejected conceptual relationship.
    identity = (
        {
            **{k: payload[k] for k in ("source", "target", "relationship")},
            "evidence": sorted({(e["note"], normalized(e["quote"])) for e in payload["evidence"]}),
        }
        if kind == "link" and "revised_from" not in payload
        else payload
    )
    pid = digest([kind, identity])[:24]
    if pid in registry:
        return pid
    no_edges(json.dumps(payload, ensure_ascii=False))
    path = f"Reviews/{KINDS[kind]}/{pid}.md"
    meta = {"id": pid, "kind": kind, "proposed_at": now(), "status": "pending", **payload}
    if kind == "removal":
        meta["decision"] = "pending"
    if kind in {"claim", "link"}:
        meta["revision"] = None
    referenced = {payload["source"], payload["target"]} if kind == "link" else {payload["note"]}
    expected = {n: digest(notes[n]) for n in referenced if n in notes}
    registry[pid] = {
        "path": path,
        "payload": payload,
        "payload_hash": digest(payload),
        "expected": expected,
        "applied": False,
        "proposed_at": meta["proposed_at"],
        "kind": kind,
        "editable_revision": kind in {"claim", "link"},
    }
    changes[path] = markdown(
        meta,
        "\nEdit `status` (pending/approved/rejected) and, for removals, `decision` (pending/flag/delete/keep).\n"
        "Paths and evidence are plain text: no graph edge exists until an approved link is applied.\n"
        "For claims/links, set `revision` to a complete replacement summary/link object and run sync. Python registers a new pending proposal; approve that exact revision afterward. Original payload fields remain immutable.\n"
        + (
            "\n**UNVERIFIED DRAFT — withheld from established notes and conceptual reasoning.**\n"
            if kind == "claim"
            else ""
        )
        + (
            "\nAutomatic validation warnings require human source verification before approval.\n"
            if payload.get("validation_warnings")
            else ""
        )
        + "Revisions are advisory; edit the source manually after reviewing.\n",
    )
    return pid


def link_proposal(
    payload: dict, registry: dict, notes: dict[str, str], changes: dict, warnings=()
) -> str | None:
    validate_output(
        "linking",
        {
            "links": [
                {
                    k: v
                    for k, v in payload.items()
                    if k not in {"validation_warnings", "revised_from"}
                }
            ]
        },
    )
    source, target = payload["source"], payload["target"]
    if not valid_note(source) or not valid_note(target) or source not in notes or source == target:
        raise VaultError("Link proposal has invalid or absent endpoints")
    warnings = [*warnings, *validate_note_evidence(payload["evidence"], notes)]
    # A retrieved fragment may omit an already approved relationship elsewhere in the note.
    if any(ref.target == target for ref in references(notes[source], source, notes)):
        return
    concept = payload["target_concept"]
    if target not in notes:
        if not target.startswith("Concepts/") or concept is None:
            raise VaultError("Only concept targets can be proposed before they exist")
        # Near-duplicates require human judgment. Exact title collisions must reuse identity.
        for name, text in notes.items():
            if (
                name.startswith("Concepts/")
                and f"# {concept['title']}".casefold() in text.casefold()
            ):
                raise VaultError("Concept already exists; reuse its path")
    elif concept is not None:
        raise VaultError("Existing concept must not be overwritten by a link proposal")
    evidence_notes = {e["note"] for e in payload["evidence"]}
    if source not in evidence_notes or (target in notes and target not in evidence_notes):
        raise VaultError("Conceptual links need evidence from both existing endpoints")
    if warnings:
        payload = {
            **payload,
            "validation_warnings": list({digest(w): w for w in warnings}.values()),
        }
    return proposal("link", payload, registry, notes, changes)


def claim_proposal(result, warnings, context, registry, notes, changes, revised_from=None):
    payload = {
        "note": context["note"],
        "draft": result,
        "validation_warnings": warnings,
        "source_fingerprint": context["fingerprint"],
    }
    if revised_from:
        payload["revised_from"] = revised_from
    pid = proposal("claim", payload, registry, notes, changes)
    registry[pid].setdefault("claim_context", context)
    path = registry[pid]["path"]
    if path in changes:
        changes[path] += "\n## Unverified draft for review\n\n" + summary_content(
            result, context["pages"], context["metadata"]
        )
    return pid


def revise_proposals(root, registry, reviews, notes, changes):
    """Obsidian edits request a separately registered pending replacement, never approval."""
    for pid, record in list(registry.items()):
        review = reviews.get(pid, {})
        revision = review.get("revision")
        if (
            revision is None
            or record["applied"]
            or record.get("superseded_by")
            or review.get("status") == "rejected"
        ):
            continue
        if review.get("status") != "pending":
            raise VaultError(
                "Set a revised proposal to pending and sync before approving its replacement"
            )
        if record["kind"] == "link":
            validate_output("linking", {"links": [revision]})
            replacement = link_proposal({**revision, "revised_from": pid}, registry, notes, changes)
            if replacement is None:
                raise VaultError("Revised relationship already exists")
        elif record["kind"] == "claim":
            context = record["claim_context"]
            validate_output(context["kind"], revision)
            warnings = validate_summary(revision, usable_pages(context["pages"]))
            if {p["source"] for p in usable_pages(context["pages"])} - {
                e["source"] for e in revision["evidence"]
            }:
                raise VaultError("Revised summary must cover every processed source")
            replacement = claim_proposal(revision, warnings, context, registry, notes, changes, pid)
        else:
            continue
        record["superseded_by"] = replacement
        _, body = frontmatter(inside(root, record["path"]).read_text(encoding="utf-8"))
        changes[record["path"]] = markdown(
            review,
            f"\n**Superseded by {registry[replacement]['path']}. This original proposal cannot apply. Review the replacement separately.**\n"
            + body,
        )


def read_reviews(root: Path, registry: dict) -> dict[str, dict]:
    reviews = {}
    for pid, record in registry.items():
        path = inside(root, record["path"])
        if not path.exists():
            raise VaultError(
                f"Registered review is missing: {record['path']}; restore it from backup"
            )
        meta, _ = frontmatter(path.read_text(encoding="utf-8"))
        permitted = {"id", "kind", "proposed_at", "status", *record["payload"]}
        if record["kind"] == "removal":
            permitted.add("decision")
        if record.get("editable_revision"):
            permitted.add("revision")
        if (
            set(meta) != permitted
            or meta.get("id") != pid
            or meta.get("kind") != record["kind"]
            or meta.get("proposed_at") != record["proposed_at"]
            or digest({k: meta.get(k) for k in record["payload"]}) != record["payload_hash"]
        ):
            raise VaultError(
                f"Review payload was changed: {record['path']}; edit only status/decision or the separate revision field"
            )
        if meta.get("status") not in ("pending", "approved", "rejected"):
            raise VaultError(f"Invalid review status in {record['path']}")
        if record["kind"] == "removal" and meta.get("decision") not in (
            "pending",
            "flag",
            "delete",
            "keep",
        ):
            raise VaultError(f"Invalid removal decision in {record['path']}")
        reviews[pid] = meta
    return reviews


def counts(reviews: dict[str, dict], registry: dict) -> dict:
    return {
        kind: sum(
            r["kind"] == kind
            and r["status"] == "pending"
            and not registry[pid]["applied"]
            and not registry[pid].get("superseded_by")
            for pid, r in reviews.items()
        )
        for kind in KINDS
    }


def refresh_pending(root: Path, registry: dict, notes: dict[str, str], reviews: dict) -> None:
    """A user can reset an unapplied stale approval to pending and run sync."""
    for pid, record in registry.items():
        if not record["applied"] and reviews[pid]["status"] == "pending":
            record["expected"] = {n: digest(notes[n]) for n in record["expected"] if n in notes}


def checkpoint(root: Path, changes: dict[str, str | None]) -> str:
    """Require a clean tracked Git tree, then make a verified backup of ALL changed files.

    Generated research is ignored by default, so a Git commit alone cannot recover it.
    A ZIP plus SHA256 manifest protects ignored notes and review/state files as well.
    """
    try:
        repo = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            text=True,
            capture_output=True,
            check=True,
        )
        if Path(repo.stdout.strip()).resolve() != root.resolve():
            raise VaultError("Destructive changes require this vault to be its own Git repository")
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=root,
            text=True,
            capture_output=True,
            check=True,
        )
        if dirty.stdout.strip():
            raise VaultError("Commit or stash tracked changes before destructive execution")
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise VaultError("Deletion requires Git and a repository with an initial commit") from exc
    name = f".pipeline/backups/{uuid.uuid4().hex}.zip"
    path = inside(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"git_head": head, "created_at": now(), "files": {}}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in changes:
            source = inside(root, item)
            if source.exists():
                manifest["files"][item] = file_hash(source)
                archive.writestr(item, source.read_bytes())
            else:
                manifest["files"][item] = None
        archive.writestr("MANIFEST.json", json.dumps(manifest, indent=2))
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise VaultError("Recovery backup verification failed; deletion refused")
        import hashlib

        for item, expected in manifest["files"].items():
            if expected is not None and hashlib.sha256(archive.read(item)).hexdigest() != expected:
                raise VaultError("Recovery backup hash mismatch; deletion refused")
    return name


def apply_decisions(
    root: Path, state: dict, notes: dict[str, str], reviews: dict
) -> tuple[dict, list[dict], bool]:
    changes: dict[str, str | None] = {}
    previews = []
    destructive = False
    registry = state["proposals"]
    original_notes = dict(notes)
    created_concepts = {}
    deletion_targets = {
        r["payload"]["note"]
        for pid, r in registry.items()
        if not r["applied"]
        and r["kind"] == "removal"
        and reviews[pid]["status"] == "approved"
        and reviews[pid]["decision"] == "delete"
    }
    claim_targets = {
        r["payload"]["note"]
        for pid, r in registry.items()
        if r["kind"] == "claim"
        and not r["applied"]
        and not r.get("superseded_by")
        and reviews[pid]["status"] == "approved"
    }
    for pid, r in registry.items():
        if (
            not r["applied"]
            and not r.get("superseded_by")
            and r["kind"] == "link"
            and reviews[pid]["status"] == "approved"
            and {r["payload"]["source"], r["payload"]["target"]} & deletion_targets
        ):
            raise VaultError(
                "Conflicting approvals: a link endpoint is also approved for deletion; defer one decision"
            )
        if (
            not r["applied"]
            and not r.get("superseded_by")
            and r["kind"] == "link"
            and reviews[pid]["status"] == "approved"
            and {r["payload"]["source"], r["payload"]["target"]} & claim_targets
        ):
            raise VaultError(
                "Conflicting approvals: apply the claim update first, then review the changed link endpoints again"
            )
    for pid, record in registry.copy().items():
        review = reviews[pid]
        if record["applied"] or record.get("superseded_by") or review["status"] != "approved":
            continue
        if review.get("revision") is not None:
            raise VaultError(
                "Sync the pending revision and approve its registered replacement before apply"
            )
        payload, kind = record["payload"], record["kind"]
        if kind == "removal" and review["decision"] == "pending":
            continue
        if kind == "revision":
            # Advisory acknowledgements authorize no file edits, including after the
            # user has already made the suggested manual revision.
            record["applied"] = True
            record["applied_at"] = now()
            continue
        for name, expected in record["expected"].items():
            if name not in original_notes or digest(original_notes[name]) != expected:
                raise VaultError(
                    f"Stale approval {pid}: {name} changed. Set status to pending, sync, and review again."
                )
        if kind == "claim":
            context = record["claim_context"]
            target = payload["note"]
            current = state[context["category"]].get(context["key"], {})
            if (
                current.get("fingerprint") != context["fingerprint"]
                or current.get("claim_review") != pid
                or (context["category"] == "papers" and context["key"] not in state["membership"])
            ):
                raise VaultError(
                    "Stale claim approval: source processing changed; review the current draft"
                )
            if target in notes and target not in record["expected"]:
                raise VaultError("Stale claim approval: destination appeared after proposal")
            content = summary_content(payload["draft"], context["pages"], context["metadata"])
            content = (
                "Human-reviewed draft (approval "
                + pid
                + "). Automatic verification warnings remain in the review record.\n\n"
                + content
            )
            notes[target] = update_note(
                notes.get(target),
                context["identity"],
                context["kind"],
                payload["draft"]["title"],
                content,
            )
            changes[target] = notes[target]
            current.update(claim_review_status="approved", processed=True)
        elif kind == "link":
            source, target = payload["source"], payload["target"]
            if source not in notes:
                raise VaultError("Approved source no longer exists")
            warnings = validate_note_evidence(payload["evidence"], original_notes)
            recorded = {digest(w) for w in payload.get("validation_warnings", [])}
            if any(digest(w) not in recorded for w in warnings):
                raise VaultError(
                    "Supporting evidence changed; reject this proposal and review a new proposal with current validation warnings"
                )
            concept = payload["target_concept"]
            if target in notes and target not in record["expected"]:
                if target not in created_concepts or created_concepts[target] != concept:
                    raise VaultError(
                        "Proposed new concept already appeared or has conflicting definitions; reject the old proposal and review again"
                    )
            if target not in notes:
                concept = payload["target_concept"]
                if not concept or not target.startswith("Concepts/"):
                    raise VaultError("Approved target no longer exists")
                notes[target] = new_note(
                    "concept:" + target, "concept", concept["title"], concept["body"]
                )
                changes[target] = notes[target]
                created_concepts[target] = concept
            text = notes[source]
            if not any(ref.target == target for ref in references(text, source, notes)):
                line = f"- [[{target[:-3]}]] — {payload['relationship']}: {payload['justification']} (approval {pid})"
                if "<!-- links:start -->" in text:
                    current = text.split("<!-- links:start -->", 1)[1].split(
                        "<!-- links:end -->", 1
                    )[0]
                    text = replace_section(text, "links", current.strip() + "\n" + line)
                else:
                    text += "\n\n## Approved conceptual relationships\n\n" + line + "\n"
                notes[source] = text
                changes[source] = text
        elif kind == "removal":
            key, target = payload["item_key"], payload["note"]
            if key in state["membership"]:
                raise VaultError(
                    "Paper has returned to the collection; this removal decision is obsolete"
                )
            current_impact = impact(target, original_notes)
            previews.append(
                {"proposal": pid, "decision": review["decision"], "note": target, **current_impact}
            )
            if current_impact != payload["impact"]:
                raise VaultError("Removal impact changed; sync and review the new removal proposal")
            if target not in notes:
                raise VaultError("Removal target is missing")
            head, _ = frontmatter(notes[target])
            if (
                head.get("id") != f"zotero:{key}"
                or head.get("generated") is not True
                or head.get("kind") != "literature"
            ):
                raise VaultError("Removal target is not the registered generated literature note")
            replace_section(
                notes[target], "managed", ""
            )  # Validate ownership markers before any action.
            replace_section(notes[target], "links", "")
            decision = review["decision"]
            if decision == "delete":
                destructive = True
                changes[target] = None
                del notes[target]
                for name, text in list(notes.items()):
                    matches = [
                        ref
                        for ref in references(text, name, original_notes)
                        if ref.target == target
                    ]
                    if not matches:
                        continue
                    # Only the Python-owned links region is automatically cleaned.
                    try:
                        owned = frontmatter(text)[0].get("generated") is True
                    except VaultError:
                        owned = False
                    if (
                        owned
                        and not name.startswith("My Notes/")
                        and "<!-- links:start -->" in text
                    ):
                        replace_section(text, "links", "")
                        start = text.index("<!-- links:start -->")
                        end = text.index("<!-- links:end -->")
                        removable = [m for m in matches if start <= m.start < end]
                        for match in reversed(removable):
                            text = text[: match.start] + target[:-3] + text[match.end :]
                        if removable:
                            notes[name] = text
                            changes[name] = text
                    remaining = [
                        ref
                        for ref in references(text, name, original_notes)
                        if ref.target == target
                    ]
                    if remaining:
                        proposal(
                            "revision",
                            {
                                "note": name,
                                "reason": f"Source removed: {key}",
                                "suggestion": f"Review the relationship and unsupported claims associated with {target}; remove or reattribute links manually.",
                                "evidence": [],
                            },
                            registry,
                            notes,
                            changes,
                        )
                state["papers"][key]["disposition"] = "deleted"
            elif decision == "flag":
                # Kept separate from user-editable frontmatter.
                text = notes[target]
                section = text.split("<!-- managed:start -->")[1].split("<!-- managed:end -->")[0]
                text = replace_section(
                    text,
                    "managed",
                    "**Collection status: removed from the active collection.**\n\n"
                    + section.strip(),
                )
                notes[target] = text
                changes[target] = text
                state["papers"][key]["disposition"] = "flagged"
            else:
                state["papers"][key]["disposition"] = "kept"
        # Advisory revisions are acknowledged, never automatically pasted into sources.
        record["applied"] = True
        record["applied_at"] = now()
    return changes, previews, destructive
