"""Managed Markdown sections leave user additions byte-for-byte intact."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .agents import no_edges
from .sources import coverage_error, usable_pages
from .storage import VaultError, frontmatter, inside, markdown, replace_section

FOLDERS = ("Literature", "My Work", "My Notes", "Concepts")
WIKILINK = re.compile(r"!?\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")


def valid_note(name: str) -> bool:
    parts = Path(name).parts
    return (
        len(parts) >= 2
        and parts[0] in FOLDERS
        and name.endswith(".md")
        and not any(c in name for c in "\\[]|#\n\r")
        and ".." not in parts
    )


def read_notes(root: Path) -> dict[str, str]:
    notes = {}
    for folder in FOLDERS:
        for p in sorted(inside(root, folder).rglob("*.md")):
            name = p.relative_to(root).as_posix()
            if not valid_note(name):
                raise VaultError(f"Unsupported note path: {name}")
            notes[name] = inside(root, name).read_text(encoding="utf-8")
    return notes


def new_note(identity: str, kind: str, title: str, content: str, **meta) -> str:
    no_edges(title)
    no_edges(content)
    return markdown(
        {"id": identity, "generated": True, "kind": kind, **meta},
        f"<!-- managed:start -->\n# {title}\n\n{content.rstrip()}\n<!-- managed:end -->\n\n"
        "## Approved conceptual relationships\n\n<!-- links:start -->\n<!-- links:end -->\n\n"
        "## Your additions\n\nWrite here. This section is preserved by synchronization.\n",
    )


def update_note(old: str | None, identity: str, kind: str, title: str, content: str, **meta) -> str:
    if old is None:
        return new_note(identity, kind, title, content, **meta)
    head, _ = frontmatter(old)
    if head.get("id") != identity or head.get("generated") is not True or head.get("kind") != kind:
        raise VaultError("Refusing to overwrite a note with mismatched ownership or identity")
    no_edges(content)
    no_edges(title)
    return replace_section(old, "managed", f"# {title}\n\n{content}")


def summary_content(result: dict, sources: list[dict], metadata: dict) -> str:
    issue = coverage_error(sources)
    text = (
        (
            "Processing coverage: partial. Unprocessed pages were not read. " + issue + "\n\n"
            if issue
            else ""
        )
        + result["summary"]
        + "\n\nProcessing limitation: PDF processing supplies text only. "
        + (
            "OCR was used on the pages listed below; recognition is unverified. "
            if any(p.get("extraction_status") == "ocr" for p in sources)
            else "No usable OCR text was obtained. "
        )
        + "No figure/image understanding was performed; non-text mathematics was not interpreted."
        + "\n\n### Processed sources\n\n"
    )
    for source in usable_pages(sources):
        method = source.get("extraction_status")
        detail = (
            f" ({method}{'; incomplete text' if source.get('incomplete') else ''})"
            if method
            else ""
        )
        text += f"- {source['source']} — {source['locator']}{detail}\n"
    unprocessed = [s for s in sources if s.get("extraction_status") == "unprocessed"]
    if unprocessed:
        text += "\n### Unprocessed pages (not read)\n\n"
        for source in unprocessed:
            text += f"- {source['source']} — {source['locator']}: {source['reason']}\n"
    text += "\n### Evidence\n\n"
    for evidence in result["evidence"]:
        text += f"- {evidence['source']}, {evidence['locator']}: {evidence['quote']}\n"
    text += "\n### Attributed scientific statements\n\n"
    for claim in result.get("claims", []):
        refs = "; ".join(f"{e['source']}, {e['locator']}" for e in claim["evidence"])
        text += f"- {claim['statement']} ({refs}). Uncertainty: {claim['uncertainty']}\n"
    warnings = sorted(
        {
            f"{p['source']}, {p['locator']}: {warning}"
            for p in sources
            for warning in p.get("extraction_warnings", [])
        }
    )
    if warnings:
        text += "\n### Extraction uncertainty\n\n" + "\n".join("- " + w for w in warnings) + "\n"
    # Bibliography is metadata, never an inferred relationship.
    import json

    text += (
        "\n### Source metadata (attribution only)\n\n```json\n"
        + json.dumps(metadata, ensure_ascii=False, indent=2)
        + "\n```\n"
    )
    no_edges(text)
    return text


def resolve_link(raw: str, source: str, notes: dict[str, str]) -> str | None:
    raw = raw.removesuffix(".md")
    direct = raw + ".md"
    if direct in notes:
        return direct
    relative = posixpath.normpath((Path(source).parent / (raw + ".md")).as_posix())
    if relative in notes:
        return relative
    matches = [n for n in notes if Path(n).stem == raw]
    return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True)
class Reference:
    start: int
    end: int
    raw: str
    target: str | None


MARKDOWN_LINK = re.compile(
    r'!?\[[^\]\n]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+["\'][^"\']*["\'])?\s*\)'
)


def references(text: str, source: str, notes: dict[str, str]) -> list[Reference]:
    output = [
        Reference(m.start(), m.end(), m.group(1), resolve_link(m.group(1), source, notes))
        for m in WIKILINK.finditer(text)
    ]
    for match in MARKDOWN_LINK.finditer(text):
        raw = unquote(match.group(1) or match.group(2))
        parsed = urlsplit(raw)
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue  # External attribution and same-page anchors are not note edges.
        path = parsed.path
        direct = path.removeprefix("/")
        relative = posixpath.normpath((Path(source).parent / path).as_posix())
        target = next((n for n in (relative, direct) if n in notes), None)
        if target is None and not path.endswith(".md"):
            target = resolve_link(path, source, notes)
        output.append(Reference(match.start(), match.end(), raw, target))
    return sorted(output, key=lambda r: r.start)


def impact(target: str, notes: dict[str, str]) -> dict:
    incoming, outgoing = [], []
    for name, text in notes.items():
        for ref in references(text, name, notes):
            if ref.target == target:
                incoming.append(name)
            if name == target:
                outgoing.append(ref.target or ref.raw)
    return {
        "incoming": sorted(set(incoming)),
        "outgoing": sorted(set(outgoing)),
        "affected": sorted(set(incoming + outgoing)),
        "consequences": "Generated support may disappear; user notes require separate revision approval. Scientific claims need reassessment.",
    }


def diagnostics(notes: dict[str, str]) -> list[dict]:
    findings = []
    connected = set()
    for name, text in notes.items():
        for ref in references(text, name, notes):
            target = ref.target
            if target is None:
                findings.append(
                    {
                        "note": name,
                        "reason": f"Broken or ambiguous link: {ref.raw}",
                        "suggestion": "Review the link target manually; no content was changed.",
                        "evidence": [],
                    }
                )
            else:
                connected.update((name, target))
    for name in notes:
        if name not in connected:
            findings.append(
                {
                    "note": name,
                    "reason": "Orphaned note",
                    "suggestion": "Consider a scientifically justified conceptual relationship; disconnected notes are allowed.",
                    "evidence": [],
                }
            )
    titles = {}
    for name, text in notes.items():
        if name.startswith("Concepts/"):
            title = re.search(r"^#\s+(.+)$", text, re.M)
            normalized = (
                re.sub(r"\W", "", title.group(1).casefold())
                if title
                else Path(name).stem.casefold()
            )
            if normalized in titles:
                findings.append(
                    {
                        "note": name,
                        "reason": f"Possible duplicate concept: {titles[normalized]}",
                        "suggestion": "Review scientific equivalence before merging manually.",
                        "evidence": [],
                    }
                )
            titles[normalized] = name
    return findings
