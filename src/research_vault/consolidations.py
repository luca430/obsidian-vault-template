"""Explicitly reviewed concept consolidation; suggestions never imply equivalence."""

from __future__ import annotations

from itertools import combinations

from jsonschema import ValidationError, validate

from .agents import CONSOLIDATION, no_edges, validate_note_evidence
from .notes import references, update_note, valid_note
from .review_state import concept_catalog
from .storage import VaultError, digest, frontmatter, inside, markdown, replace_section


def validate_suggestion(result, members, notes):
    try:
        validate(result, CONSOLIDATION)
    except ValidationError as exc:
        raise VaultError(f"Invalid consolidation: {exc.message}") from exc
    import json

    no_edges(json.dumps(result, ensure_ascii=False))
    paths = {m["path"] for m in members}
    canonical = result["canonical"]
    if result["operation"] == "merge":
        if not canonical or canonical["path"] not in paths:
            raise VaultError("A merge must choose one of the reviewed concept paths")
        if not valid_note(canonical["path"]) or not canonical["path"].startswith("Concepts/"):
            raise VaultError("Invalid canonical concept path")
        names = {v["title"] for m in members for v in m["variants"]} | {
            a for m in members for a in m["aliases"]
        }
        if not (names - {canonical["title"]}).issubset(set(canonical["aliases"])):
            raise VaultError("A merge must preserve previous concept names and aliases explicitly")
    elif canonical is not None:
        raise VaultError("Only a merge may replace a concept definition")
    if len(paths) < 2 and result["operation"] != "merge":
        raise VaultError(
            "Different definitions using one path need a merge or revised distinct paths"
        )
    if result["operation"] == "keep-separate" and result["links"]:
        raise VaultError("Keeping concepts separate cannot create a relationship")
    if result["operation"] == "relate" and not result["links"]:
        raise VaultError("Relating concepts requires explicit, evidenced relationships")
    if validate_note_evidence(result["evidence"], notes):
        raise VaultError("Consolidation evidence must quote the current source notes exactly")
    required = {m["path"] for m in members if m["existing"]}
    if not required.issubset({e["note"] for e in result["evidence"]}):
        raise VaultError("Consolidation must include evidence from every existing member")
    future = dict(notes)
    if canonical:
        future[canonical["path"]] = canonical["title"] + "\n" + canonical["body"]
    for link in result["links"]:
        if (
            link["source"] == link["target"]
            or link["source"] not in future
            or link["target"] not in future
            or link["target_concept"] is not None
        ):
            raise VaultError("Consolidation links must join distinct, existing or canonical notes")
        if not {link["source"], link["target"]}.issubset({e["note"] for e in link["evidence"]}):
            raise VaultError("Consolidation relationships require evidence from both endpoints")
        if validate_note_evidence(link["evidence"], future):
            raise VaultError("Consolidation link evidence does not match the resulting notes")
    keys = [(link["source"], link["target"], link["relationship"]) for link in result["links"]]
    if len(keys) != len(set(keys)):
        raise VaultError("Combine evidence for repeated source/target/relationship assertions")


def register_consolidation(result, group, state, notes, changes):
    from .reviews import proposal

    validate_suggestion(result, group["members"], notes)
    related = sorted({pid for m in group["members"] for pid in m["proposals"]})
    required_sources = {state["proposals"][pid]["payload"]["source"] for pid in related}
    if not required_sources.issubset({e["note"] for e in result["evidence"]}):
        raise VaultError("Consolidation must include evidence from every proposed source")
    if result["operation"] == "merge":
        intended = {
            (link["source"], link["target"], link["relationship"]): link for link in result["links"]
        }
        for pid in related:
            link = state["proposals"][pid]["payload"]
            key = (link["source"], result["canonical"]["path"], link["relationship"])
            if key not in intended:
                raise VaultError(
                    "A merge must preserve every proposed source and relationship type; defer unwanted proposals before requesting consolidation"
                )
            original_evidence = {
                (e["note"], e["quote"]) for e in link["evidence"] if e["note"] == link["source"]
            }
            if not original_evidence.issubset(
                {(e["note"], e["quote"]) for e in intended[key]["evidence"]}
            ):
                raise VaultError("A merged relationship must retain its original source evidence")
    members = [m["path"] for m in group["members"]]
    impacts = []
    for source, text in notes.items():
        matches = [r for r in references(text, source, notes) if r.target in members]
        if matches:
            impacts.append(
                {
                    "note": source,
                    "targets": sorted({r.target for r in matches}),
                    "policy": "Rewrite Python-owned links only; other links retain working redirects.",
                }
            )
    dependencies = sorted(
        set(members)
        | required_sources
        | {i["note"] for i in impacts}
        | {e["note"] for e in result["evidence"]}
        | {n for link in result["links"] for n in [link["source"], link["target"]]}
    )
    payload = {
        **result,
        "members": members,
        "member_signatures": {m["path"]: m["signature"] for m in group["members"]},
        "related_proposals": {pid: state["proposals"][pid]["payload_hash"] for pid in related},
        "dependencies": dependencies,
        "dependency_fingerprint": digest({p: notes.get(p) for p in dependencies}),
        "expected_absent": sorted(p for p in members if p not in notes),
        "impact": impacts,
    }
    # Older apply implementations do not understand this review kind. Fail closed on downgrade.
    state["schema"] = 2
    pid = proposal("consolidation", payload, state["proposals"], notes, changes)
    path = state["proposals"][pid]["path"]
    if path in changes:
        prior = "\n\n### Candidate definitions before consolidation\n\n"
        for member in group["members"]:
            prior += member["path"] + "\n"
            for variant in member["variants"]:
                # Neutralize delimiters in copied note prose so review files add no graph edges.
                prior += (variant["title"] + "\n" + variant["body"]).replace("[[", "［［").replace(
                    "]]", "］］"
                ) + "\n\n"
            prior += "Related review IDs: " + ", ".join(member["proposals"]) + "\n\n"
        changes[path] += (
            "\n## Proposed concept decision\n\n"
            + result["operation"]
            + "\n\n"
            + result["explanation"]
            + "\n\n### Scope and distinctions\n\n"
            + result["distinctions"]
            + (
                "\n\n### Proposed canonical definition\n\n" + result["canonical"]["body"]
                if result["canonical"]
                else ""
            )
            + prior
            + "\n\n### Affected existing notes\n\n"
            + "\n".join(i["note"] + ": " + i["policy"] for i in impacts)
            + "\n\n### Proposed relationships\n\n"
            + "\n".join(
                link["source"] + " -> " + link["target"] + " (" + link["relationship"] + ")"
                for link in result["links"]
            )
            + "\n\nExisting concept notes retain their original definitions and additions as redirects. Review the exact apply preview before approving this consolidation.\n"
        )
    return pid


def append_link(text, target, relationship, justification, pid):
    justification = justification.replace("\r", " ").replace("\n", " ")
    line = f"- [[{target[:-3]}]] — {relationship}: {justification} (approval {pid})"
    if "<!-- links:start -->" in text:
        current = text.split("<!-- links:start -->", 1)[1].split("<!-- links:end -->", 1)[0]
        return replace_section(text, "links", current.strip() + "\n" + line)
    from .review_state import AUTO_LINK

    heading = "\n\n## Approved conceptual relationships\n\n"
    if text.count(heading) == 1 and all(
        AUTO_LINK.fullmatch(s) for s in text.split(heading)[1].splitlines() if s.strip()
    ):
        return text.rstrip() + "\n" + line + "\n"
    return text + heading + line + "\n"


def apply_consolidation(root, pid, record, state, notes, reviews, changes):
    from .reviews import relationship_present

    payload = record["payload"]
    catalog = concept_catalog(notes, state["proposals"], reviews)
    for path, signature in payload["member_signatures"].items():
        if path not in catalog or catalog[path]["signature"] != signature:
            raise VaultError(f"Consolidation candidate changed: {path}; request a new suggestion")
    for path in payload["expected_absent"]:
        if path in notes:
            raise VaultError(f"Consolidation target appeared: {path}")
    for other, expected in payload["related_proposals"].items():
        candidate = state["proposals"].get(other, {})
        if (
            candidate.get("payload_hash") != expected
            or candidate.get("applied")
            or candidate.get("superseded_by")
            or candidate.get("resolution")
            or reviews[other]["status"] == "rejected"
        ):
            raise VaultError(f"Consolidation member review changed: {other}")
    result = {key: payload[key] for key in CONSOLIDATION["properties"]}
    validate_suggestion(result, [catalog[p] for p in payload["members"]], notes)
    if payload["operation"] == "merge":
        canonical = payload["canonical"]
        target = canonical["path"]
        old = notes.get(target)
        identity = frontmatter(old)[0].get("id") if old else "concept:" + digest([target, pid])[:24]
        text = update_note(old, identity, "concept", canonical["title"], canonical["body"])
        meta, body = frontmatter(text)
        aliases = meta.get("aliases", [])
        if isinstance(aliases, str):
            aliases = [aliases]
        meta["aliases"] = sorted(set(aliases) | set(canonical["aliases"]))
        meta["consolidation_approval"] = pid
        meta["merged_from"] = payload["members"]
        notes[target] = changes[target] = markdown(meta, body)
        other_paths = set(payload["members"]) - {target}
        rewritten = []
        for source, text in list(notes.items()):
            if source in other_paths or "<!-- links:start -->" not in text:
                continue
            meta, _ = frontmatter(text) if text.startswith("---\n") else ({}, "")
            if not meta.get("generated") or source.startswith("My Notes/"):
                continue
            start, end = text.index("<!-- links:start -->"), text.index("<!-- links:end -->")
            for ref in reversed(references(text, source, notes)):
                if (
                    ref.target in other_paths
                    and start < ref.start < ref.end < end
                    and text[ref.start : ref.end].startswith("[[")
                ):
                    if source == target:
                        continue
                    replacement = text[ref.start : ref.end].replace(ref.raw, target[:-3], 1)
                    rewritten.append(
                        {"source": source, "previous_target": ref.target, "target": target}
                    )
                    text = text[: ref.start] + replacement + text[ref.end :]
            if text != notes[source]:
                notes[source] = changes[source] = text
        for path in other_paths & notes.keys():
            text = notes[path]
            meta, body = frontmatter(text)
            if meta.get("generated") is not True or meta.get("kind") != "concept":
                raise VaultError(f"Concept is not Python-owned; consolidate manually: {path}")
            replace_section(text, "managed", "")  # Validate ownership before editing.
            meta.update(kind="concept-redirect", redirect_target=target, consolidation_approval=pid)
            body_text = markdown(meta, body)
            body_text = append_link(
                body_text,
                target,
                "shared-concept",
                "Approved concept consolidation; prior definition retained for provenance.",
                pid,
            )
            notes[path] = changes[path] = body_text
        for other in payload["related_proposals"]:
            r = state["proposals"][other]
            r["superseded_by"] = pid
            meta, body = frontmatter(inside(root, r["path"]).read_text(encoding="utf-8"))
            changes[r["path"]] = markdown({**meta, "superseded_by": pid}, body)
        state.setdefault("concept_redirects", {}).update({p: target for p in other_paths})
        record["rewritten_links"] = rewritten
    else:
        signatures = list(payload["member_signatures"].values())
        for pair in combinations(signatures, 2):
            state.setdefault("concept_distinctions", {})[digest(sorted(pair))] = pid
    for link in payload["links"]:
        source, target = link["source"], link["target"]
        if not relationship_present(
            notes[source], target, link["relationship"], source, notes, allow_untyped=False
        ):
            notes[source] = changes[source] = append_link(
                notes[source], target, link["relationship"], link["justification"], pid
            )
