"""Read-only review dependencies, before/after differences and concept candidates."""

from __future__ import annotations

import difflib
import re
from itertools import combinations
from pathlib import Path

import yaml

from .storage import VaultError, digest, frontmatter

AUTO_LINK = re.compile(r"^- \[\[[^\n]+\]\] — [^\n]+ \(approval [0-9a-f]{24}\)$")


def dependency_text(text: str) -> str:
    """Ignore only known graph bookkeeping and YAML presentation, never scientific text."""
    body = text.replace("\r\n", "\n")
    prefix = ""
    if body.startswith("---\n"):
        meta, body = frontmatter(body)
        # These keys affect display only. Identity, aliases and provenance remain dependencies.
        meta.pop("cssclasses", None)
        prefix = yaml.safe_dump(meta, sort_keys=True, allow_unicode=True)
    start, end = "<!-- links:start -->", "<!-- links:end -->"
    if body.count(start) == body.count(end) == 1 and body.index(start) < body.index(end):
        before, rest = body.split(start)
        links, after = rest.split(end)
        kept = [
            line for line in links.splitlines() if line.strip() and not AUTO_LINK.fullmatch(line)
        ]
        body = before + start + "\n" + "\n".join(kept) + "\n" + end + after
    else:
        # Older personal notes used an appended heading without ownership markers.
        heading = "\n\n## Approved conceptual relationships\n\n"
        if body.count(heading) == 1:
            before, links = body.split(heading)
            if links.strip() and all(
                AUTO_LINK.fullmatch(s) for s in links.splitlines() if s.strip()
            ):
                body = before
    return prefix + "\n" + body


def capture_dependencies(record: dict, notes: dict[str, str]) -> None:
    record["expected_notes"] = {name: notes[name] for name in record["expected"] if name in notes}
    record["dependency_version"] = 1


def note_differences(record: dict, notes: dict[str, str]) -> list[dict]:
    if record["kind"] == "revision":
        return []  # Advisory acknowledgements intentionally permit prior manual edits.
    differences = []
    for name, expected in record["expected"].items():
        previous = record.get("expected_notes", {}).get(name)
        if previous is not None and digest(previous) != expected:
            raise VaultError(f"Review snapshot integrity mismatch: {name}")
        current = notes.get(name)
        same = current is not None and digest(current) == expected
        if not same and previous is not None and current is not None and record["kind"] == "link":
            same = dependency_text(previous) == dependency_text(current)
        if same:
            continue
        diff = (
            None
            if previous is None
            else "".join(
                difflib.unified_diff(
                    previous.splitlines(True),
                    (current or "").splitlines(True),
                    fromfile="approved/" + name,
                    tofile="current/" + name,
                )
            )
        )
        differences.append(
            {
                "note": name,
                "missing": current is None,
                "diff": diff,
                "reason": "No historical snapshot was stored; refresh this legacy proposal for a new review."
                if previous is None
                else "Referenced scientific content changed.",
            }
        )
    return differences


def migrate_matching_dependencies(registry: dict, notes: dict[str, str]) -> None:
    """A legacy approval gains snapshots only when its original exact hashes still match."""
    for record in registry.values():
        if (
            not record["applied"]
            and not record.get("superseded_by")
            and not record.get("resolution")
            and "expected_notes" not in record
            and not note_differences(record, notes)
        ):
            capture_dependencies(record, notes)


def concept_catalog(notes: dict, registry: dict, reviews: dict) -> dict:
    catalog = {}
    for path, text in notes.items():
        if not path.startswith("Concepts/"):
            continue
        try:
            meta, body = frontmatter(text)
        except VaultError:
            meta, body = {}, text
        if meta.get("kind") == "concept-redirect":
            continue
        title = next(
            (s[2:].strip() for s in body.splitlines() if s.startswith("# ")), Path(path).stem
        )
        definition = body
        additions = ""
        if body.count("<!-- managed:start -->") == body.count("<!-- managed:end -->") == 1:
            before, rest = body.split("<!-- managed:start -->")
            definition, after = rest.split("<!-- managed:end -->")
            outside = dependency_text(before + after)
            scaffolding = {
                "<!-- links:start -->",
                "<!-- links:end -->",
                "## Approved conceptual relationships",
                "## Your additions",
                "Write here. This section is preserved by synchronization.",
            }
            additions = "\n".join(
                line
                for line in outside.splitlines()
                if line.strip() and line.strip() not in scaffolding
            )
        definition = definition.strip()
        if definition.startswith("# " + title):
            definition = definition[len("# " + title) :].strip()
        aliases = meta.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
            raise VaultError(f"Concept aliases must be text or a list of strings: {path}")
        catalog[path] = {
            "path": path,
            "id": meta.get("id", "concept:" + digest(path)[:24]),
            "existing": True,
            "variants": [{"title": title, "body": definition}],
            "scope_additions": additions,
            "aliases": aliases,
            "proposals": [],
        }
    for pid, record in registry.items():
        if (
            record["kind"] != "link"
            or record["applied"]
            or record.get("superseded_by")
            or record.get("resolution")
            or reviews.get(pid, {}).get("status") == "rejected"
        ):
            continue
        concept = record["payload"].get("target_concept")
        if not concept:
            continue
        path = record["payload"]["target"]
        item = catalog.setdefault(
            path,
            {
                "path": path,
                "id": "concept:" + digest(path)[:24],
                "existing": False,
                "variants": [],
                "aliases": [],
                "proposals": [],
            },
        )
        if concept not in item["variants"]:
            item["variants"].append(concept)
        item["proposals"].append(pid)
    for item in catalog.values():
        item["signature"] = digest(
            [item["path"], item["variants"], item["aliases"], item.get("scope_additions", "")]
        )
    return catalog


def candidate_groups(notes: dict, registry: dict, reviews: dict, distinctions=None) -> list[dict]:
    """Similarity proposes a review; it never decides equivalence or creates an edge."""
    catalog = concept_catalog(notes, registry, reviews)
    groups = []
    for item in catalog.values():
        if len(item["variants"]) > 1:
            groups.append(
                {"members": [item], "reason": "Multiple definitions use the same concept path."}
            )
    for left, right in combinations(catalog.values(), 2):
        pair = sorted([left["signature"], right["signature"]])
        if digest(pair) in (distinctions or {}):
            continue
        names_left = [v["title"] for v in left["variants"]] + left["aliases"]
        names_right = [v["title"] for v in right["variants"]] + right["aliases"]

        def similar(a, b):
            a, b = a.casefold(), b.casefold()
            ta, tb = set(re.findall(r"\w+", a)), set(re.findall(r"\w+", b))
            ratio = difflib.SequenceMatcher(None, a, b).ratio()
            return (
                a == b
                or ratio >= 0.9
                or (ratio >= 0.72 and len(ta & tb) / max(1, len(ta | tb)) >= 0.4)
            )

        if any(similar(a, b) for a in names_left for b in names_right):
            groups.append(
                {
                    "members": [left, right],
                    "reason": "Similar names or aliases; scope and evidence require review.",
                }
            )
    for group in groups:
        group["id"] = digest(sorted(m["signature"] for m in group["members"]))[:24]
    return groups
