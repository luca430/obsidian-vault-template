"""Previewed, journaled path migration without source extraction or inference."""

from __future__ import annotations

import difflib
import json
import posixpath
import re
from copy import deepcopy
from urllib.parse import quote

from .identities import literature_path
from .notes import append_aliases, bibliography, references
from .recipes import reasoning_recipe
from .registry import Registry
from .reviews import archive_finalized
from .scientific import graph_signature, scientific_text
from .storage import VaultError, digest, frontmatter, inside, markdown, now, replace_section


def rewrite_links(text, source, notes, paths):
    for ref in reversed(references(text, source, notes)):
        if ref.target not in paths:
            continue
        segment = text[ref.start : ref.end]
        target = paths[ref.target]
        if "[[" in segment[:3]:
            # Keep anchors and display labels; use the full vault-relative target.
            segment = re.sub(r"(\[\[)[^\]|#]+", lambda m: m[1] + target[:-3], segment, count=1)
        else:
            relative = posixpath.relpath(target, posixpath.dirname(paths.get(source, source)))
            segment = segment.replace(ref.raw, quote(relative, safe="/"), 1)
        text = text[: ref.start] + segment + text[ref.end :]
    return text


def migrate(pipeline, execute=False, plan_token=None, authorize_notes=()):
    state = deepcopy(pipeline.state())
    pipeline.baseline(state)
    notes = pipeline._baseline_notes
    # Zotero supplies titles, never filenames or inferred citation strings. No PDF reads.
    snapshot = pipeline.zotero.snapshot()
    paths, metadata, changes, advisory = {}, {}, {}, {}
    for key, record in sorted(state["papers"].items()):
        old = record.get("note")
        if not old or (old not in notes and not record.get("claim_review")):
            continue
        if old in notes:
            meta, _ = frontmatter(notes[old])
            if meta.get("id") != "zotero:" + key or meta.get("generated") is not True:
                raise VaultError(f"Literature ownership mismatch: {old}")
            replace_section(notes[old], "managed", "")
            replace_section(notes[old], "links", "")
        item = snapshot.get(key, {}).get("metadata") or record.get("metadata")
        if not item:
            advisory[old] = "No stored or current Zotero metadata; retained unchanged."
            continue
        metadata[old] = (key, item)
        record["metadata"] = item
        new = literature_path(key, item, pipeline.root)
        if old != new:
            if new.casefold() in {n.casefold() for n in notes if n != old}:
                raise VaultError(f"Literature migration destination exists: {new}")
            paths[old] = new

    migrated_notes = {}
    personal_previews = {}
    for name, text in notes.items():
        updated = text
        if name in metadata:
            key, item = metadata[name]
            updated = bibliography(
                updated, key, item, pipeline.config.zotero.get("library", "users/0")
            )
            if name in paths:
                updated = append_aliases(updated, [name[:-3], name.split("/")[-1][:-3]])
        rewritten = rewrite_links(updated, name, notes, paths)
        if rewritten != updated:
            # Rewrite only the marked Python-owned graph area without authorization.
            safe = updated
            if (
                not name.startswith("My Notes/")
                and updated.startswith("---\n")
                and frontmatter(updated)[0].get("generated") is True
            ):
                replace_section(updated, "links", "")
                start, end = (
                    updated.index("<!-- links:start -->"),
                    updated.index("<!-- links:end -->"),
                )
                safe = (
                    updated[:start]
                    + rewrite_links(updated[start:end], name, notes, paths)
                    + updated[end:]
                )
            if safe != rewritten:
                personal_previews[name] = "".join(
                    difflib.unified_diff(
                        safe.splitlines(True),
                        rewritten.splitlines(True),
                        fromfile=name,
                        tofile=paths.get(name, name),
                    )
                )
                if name in authorize_notes:
                    safe = rewritten
                else:
                    advisory[name] = (
                        "User-authored references retained; inspect preview and authorize this exact note to rewrite links."
                    )
            updated = safe
        destination = paths.get(name, name)
        migrated_notes[destination] = updated
        if updated != text or destination != name:
            changes[destination] = updated
            if destination != name:
                changes[name] = None

    def remap(value):
        if isinstance(value, str):
            return paths.get(value, value)
        if isinstance(value, list):
            return [remap(v) for v in value]
        if isinstance(value, dict):
            return {paths.get(k, k): remap(v) for k, v in value.items()}
        return value

    # Original review bytes and payloads are retained verbatim in migration history.
    # Active path references change, while Zotero identities and proposal IDs persist.
    for pid, record in state["proposals"].items():
        mapped = remap(record)
        if mapped == record:
            continue
        old_text = inside(pipeline.root, record["path"]).read_text(encoding="utf-8")
        audit = f".pipeline/migration_history/{digest([paths, record, old_text])[:24]}/{pid}.json"
        changes[audit] = json.dumps(
            {"record": record, "markdown": old_text}, ensure_ascii=False, indent=2
        )
        meta, body = frontmatter(old_text)
        mapped["payload_hash"] = digest(mapped["payload"])
        # Preserve stale baselines: transform the actual historical snapshots,
        # rather than replacing them with the current note contents.
        for name, previous in record.get("expected_notes", {}).items():
            new_name = paths.get(name, name)
            transformed = rewrite_links(previous, name, record["expected_notes"], paths)
            if name in metadata:
                key, item = metadata[name]
                transformed = bibliography(
                    transformed, key, item, pipeline.config.zotero.get("library", "users/0")
                )
                # Compare science across aliases/presentation, preserving stale differences.
                if scientific_text(previous) == scientific_text(notes.get(name, "")):
                    transformed = migrated_notes[new_name]
            mapped.setdefault("expected_notes", {})[new_name] = transformed
            mapped["expected"][new_name] = digest(transformed)
        mapped.setdefault("migration_history", []).append(audit)
        state["proposals"][pid] = mapped
        changes[mapped["path"]] = markdown(remap(meta), body)

    for record in state["papers"].values():
        record["note"] = paths.get(record["note"], record["note"])
    previous_graph = state.get("graph_notes", {})
    # Preserve any unfinished science by migrating only matching graph baselines.
    graph_clean = graph_signature(notes, reasoning_recipe(pipeline.config)) == state.get(
        "graph_digest"
    )
    state["graph_notes"] = {
        paths.get(n, n): digest(scientific_text(migrated_notes[paths.get(n, n)]))
        if n in notes and h == digest(scientific_text(notes[n]))
        else h
        for n, h in previous_graph.items()
    }
    if graph_clean:
        state["graph_digest"] = graph_signature(migrated_notes, reasoning_recipe(pipeline.config))
    if state.get("concept_registry") and paths:
        # Rebuild retrieval offsets deterministically; carry scientific annotations forward.
        registry = Registry(state["concept_registry"])

        class ExistingAnnotations:
            full = False
            annotations = []

            def run(self, role, payload, validator):
                return {"concepts": self.annotations}

        indexer = ExistingAnnotations()
        for old, new in paths.items():
            if new not in migrated_notes:
                continue  # A withheld claim draft has no established scientific note yet.
            previous = registry.data["documents"].get(old, {})
            indexer.annotations = remap(previous.get("annotations", []))
            registry.remove(old)
            text = scientific_text(migrated_notes[new])
            registry.update(pipeline.config, new, text, {"note_fingerprint": digest(text)}, indexer)
        registry.reviewed(state["proposals"], migrated_notes)
    archived = archive_finalized(pipeline.root, state["proposals"], changes)
    token = digest([changes, personal_previews, sorted(authorize_notes)])
    result = {
        "dry_run": not execute,
        "renames": paths,
        "changes": sorted(changes),
        "user_note_previews": personal_previews,
        "advisory": advisory,
        "archived_reviews": len(archived),
        "plan_token": token,
        "llm_calls": 0,
        "diffs": {
            n: "".join(
                difflib.unified_diff(
                    (
                        inside(pipeline.root, n).read_text(encoding="utf-8")
                        if inside(pipeline.root, n).exists()
                        else ""
                    ).splitlines(True),
                    (t or "").splitlines(True),
                    fromfile=n,
                    tofile=n,
                )
            )
            for n, t in changes.items()
            if n.endswith(".md")
        },
    }
    if execute:
        if token != plan_token:
            raise VaultError(
                "Migration preview changed; preview again and supply the exact --plan-token"
            )
        if set(authorize_notes) - personal_previews.keys():
            raise VaultError("Authorize only notes in this migration's user-note previews")
        state.setdefault("migrations", []).append({"at": now(), "paths": paths, "token": token})
        pipeline.save(state, changes)
    return result
