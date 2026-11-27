"""Complete readiness reports, with isolated validation and explicit selection."""

from __future__ import annotations

from copy import deepcopy

from .review_state import candidate_groups, concept_catalog, note_differences
from .storage import VaultError, file_hash


def readiness(root, state, notes, reviews, config):
    from .reviews import apply_decisions

    registry = state["proposals"]
    items = {}
    approved = []
    for pid, record in registry.items():
        if (
            record["applied"]
            or record.get("superseded_by")
            or record.get("resolution")
            or reviews[pid]["status"] == "rejected"
        ):
            continue
        differences = note_differences(record, notes)
        item = {
            "id": pid,
            "path": record["path"],
            "kind": record["kind"],
            "decision": reviews[pid]["status"],
            "state": "needs-review",
            "differences": differences,
            "reason": "Awaiting explicit approval.",
            "refreshable": record["kind"] in {"link", "claim"},
            "notes": sorted(record["expected"]),
        }
        items[pid] = item
        if differences:
            item.update(
                state="needs-revalidation",
                reason=f"Stale approval {pid}: "
                + ", ".join(d["note"] for d in differences)
                + ". Inspect the differences and refresh this review.",
            )
        if reviews[pid]["status"] != "approved":
            continue
        approved.append(pid)
        if differences:
            continue
        try:
            if record["kind"] == "claim":
                for path, expected in record["claim_context"]["files"].items():
                    if file_hash(config.authorized(path)) != expected:
                        raise VaultError(
                            f"Stale claim approval: source file changed: {path}; synchronize this source again"
                        )
            isolated_reviews = deepcopy(reviews)
            for other in isolated_reviews:
                if other != pid and isolated_reviews[other]["status"] == "approved":
                    isolated_reviews[other]["status"] = "pending"
            isolated_state = deepcopy(state)
            apply_decisions(root, isolated_state, dict(notes), isolated_reviews)
            if not isolated_state["proposals"][pid]["applied"]:
                raise VaultError("This removal still needs an explicit decision")
            item.update(
                state="ready", reason="Approved and validated against current dependencies."
            )
        except (VaultError, OSError, ValueError) as exc:
            item.update(state="blocked", reason=str(exc))
    # Compare all approved proposals before any apply. Append-only links can coexist;
    # definition-changing operations need their own reviewed phase.
    concepts = {}
    changing = {}
    for pid in approved:
        r = registry[pid]
        payload = r["payload"]
        if r["kind"] == "link" and payload["target"] not in notes:
            concepts.setdefault(payload["target"], []).append(pid)
        if r["kind"] in {"claim", "removal"}:
            changing.setdefault(payload["note"], []).append(pid)
        if r["kind"] == "consolidation" and payload["operation"] == "merge":
            for path in payload["members"]:
                changing.setdefault(path, []).append(pid)
    from .storage import digest

    for target, pids in concepts.items():
        if len({digest(registry[p]["payload"]["target_concept"]) for p in pids}) > 1:
            paths = "\n".join(
                f"{registry[p]['path']} (source: {registry[p]['payload']['source']}; relationship: {registry[p]['payload']['relationship']})"
                for p in pids
            )
            for pid in pids:
                items[pid].update(
                    state="possible-duplicate",
                    reason=f"Conflicting definitions for {target}:\n{paths}\nRequest a consolidation, or set a review to status: pending. Use the separate revision field to revise a proposal.",
                )
    for pid in approved:
        r = registry[pid]
        payload = r["payload"]
        if r["kind"] == "link":
            dependencies = {payload["source"], payload["target"]}
            blockers = {other for n in dependencies for other in changing.get(n, [])}
        else:
            dependencies = set(payload.get("members", [payload.get("note")]))
            blockers = {
                other for n in dependencies for other in changing.get(n, []) if other != pid
            }
        if blockers and items[pid]["state"] == "ready":
            items[pid].update(
                state="blocked",
                reason="Conflicting approvals: depends on a separately approved definition change/removal: "
                + ", ".join(registry[p]["path"] for p in sorted(blockers)),
            )
    candidates = candidate_groups(notes, registry, reviews, state.get("concept_distinctions"))
    for group in candidates:
        if len(group["members"]) < 2:
            continue
        for member in group["members"]:
            for pid in member["proposals"]:
                if pid in approved and items[pid]["state"] == "ready":
                    items[pid].update(
                        state="possible-duplicate",
                        reason="Possible duplicate concept; compare scope and choose merge, keep separate, or relate. Candidate: "
                        + group["id"],
                    )
    return {
        "items": list(items.values()),
        "ready_ids": [pid for pid in approved if items[pid]["state"] == "ready"],
        "blocked": [items[pid] for pid in approved if items[pid]["state"] != "ready"],
        "concept_candidates": candidates,
        "concept_catalog": [
            {"path": item["path"], "id": item["id"], "title": item["variants"][0]["title"]}
            for item in concept_catalog(notes, registry, reviews).values()
        ],
    }


def select_reviews(reviews, selected):
    result = deepcopy(reviews)
    for pid, review in result.items():
        if pid not in selected and review["status"] == "approved":
            review["status"] = "pending"
    return result
