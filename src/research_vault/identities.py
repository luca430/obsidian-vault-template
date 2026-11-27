"""Deterministic identities; similarity never establishes scientific equivalence."""

from __future__ import annotations

import re
import unicodedata

from .scientific import scientific_text
from .storage import digest, frontmatter

SYMMETRIC = {"shared-concept", "contradiction"}


def endpoint(path, notes, seen=None):
    seen = set(seen or ())
    if path in seen:
        return "path:" + path
    seen.add(path)
    text = notes.get(path, "")
    if not text:
        matches = []
        for name, candidate in notes.items():
            if not candidate.startswith("---\n"):
                continue
            meta, _ = frontmatter(candidate)
            aliases = meta.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [aliases]
            if path in aliases or path.removesuffix(".md").removeprefix("Concepts/") in aliases:
                matches.append(name)
        if len(matches) == 1:
            return endpoint(matches[0], notes, seen)
    if text.startswith("---\n"):
        meta, _ = frontmatter(text)
        if meta.get("kind") == "concept-redirect":
            return endpoint(meta["redirect_target"], notes, seen)
        if meta.get("id"):
            return str(meta["id"])
    # Proposed concept IDs are allocated deterministically before their approval.
    return (
        "concept:" + digest(path)[:24]
        if path.startswith("Concepts/") and path not in notes
        else "path:" + path
    )


def relationship_key(payload, notes):
    source, target = (endpoint(payload[k], notes) for k in ("source", "target"))
    relation = payload["relationship"]
    if relation == "special-case":
        source, target, relation = target, source, "generalization"
    if relation in SYMMETRIC:
        source, target = sorted([source, target])
    return [source, target, relation]


def relationship_identity(payload, notes):
    return digest(relationship_key(payload, notes))


def registered_relationship_key(record, notes):
    """Keep approved endpoint identities even if a path is reused by another note."""
    key = list(
        record.get("relationship_key")
        or relationship_key(record["payload"], {**notes, **record.get("expected_notes", {})})
    )
    redirects = {}
    for text in notes.values():
        if text.startswith("---\n"):
            meta, _ = frontmatter(text)
            if meta.get("kind") == "concept-redirect" and meta.get("id"):
                redirects[str(meta["id"])] = endpoint(meta["redirect_target"], notes)
    key[:2] = [redirects.get(identity, identity) for identity in key[:2]]
    if key[2] in SYMMETRIC:
        key[:2] = sorted(key[:2])
    return key


def revision_identity(payload, notes):
    # A controlled reason prefix supplies category without changing either runtime's
    # output schema. Legacy prose uses a conservative category and evidence anchors.
    reason = payload["reason"].casefold()
    categories = (
        "orphan",
        "broken",
        "duplicate",
        "attribution",
        "contradiction",
        "uncertainty",
        "assumption",
        "coverage",
        "mathemat",
    )
    prefix = reason.partition(":")[0].strip()
    category = (
        ("mathemat" if prefix == "mathematics" else prefix)
        if prefix in {*categories, "mathematics", "scientific-review"}
        else next((c for c in categories if c in reason), "scientific-review")
    )
    anchors = {}
    for evidence in payload.get("evidence", []):
        name = evidence["note"]
        text = scientific_text(notes.get(name, ""))
        quote = " ".join(evidence["quote"].split())
        paragraphs = [" ".join(p.split()) for p in text.split("\n\n") if p.strip()]
        matching = [p for p in paragraphs if quote and quote in p]
        anchors.setdefault(endpoint(name, notes), set()).update(matching or [quote])
    context = (
        {n: sorted(parts) for n, parts in sorted(anchors.items())}
        if anchors
        else {
            endpoint(payload["note"], notes): digest(
                scientific_text(notes.get(payload["note"], ""))
            )
        }
    )
    # Different broken links and duplicate targets remain separate advisory issues.
    if reason.startswith(("broken or ambiguous link:", "possible duplicate concept:")):
        category = reason
    return digest([endpoint(payload["note"], notes), category, context])


def literature_path(key, metadata, root):
    title = str(metadata.get("data", {}).get("title") or key)
    title = unicodedata.normalize("NFC", title)
    title = re.sub(r'[<>:"/\\|?*\[\]#^\x00-\x1f\x7f]', " ", title)
    title = re.sub(r"\s+", " ", title).strip(" .") or "Untitled"
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", title, re.I):
        title = "_" + title
    # Key suffix makes collisions stable independently of collection ordering.
    suffix = f" — {key}.md"
    budget = min(180, 240 - len(str(root)) - len("/Literature/") - len(suffix))
    if budget < 12:
        from .storage import VaultError

        raise VaultError("Vault path is too long for portable literature filenames")
    while len(title.encode("utf-8")) > budget or len(title.encode("utf-16-le")) // 2 > budget:
        title = title[:-1]
    return "Literature/" + title.rstrip(" .") + suffix
