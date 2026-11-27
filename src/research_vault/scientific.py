"""Canonical scientific inputs, independent of rendering and graph bookkeeping."""

from __future__ import annotations

import json
import re

from .storage import digest, frontmatter

APPROVED_LINE = re.compile(r"^- \[\[[^\n]+\]\] — [^\n]+ \(approval [0-9a-f]{24}\)$")


def math_markdown(text):
    """Normalize delimiters only; never interpret or repair mathematical expressions."""
    text = re.sub(r"\\\[(.*?)\\\]", lambda m: "$$" + m[1] + "$$", text, flags=re.S)
    return re.sub(r"\\\((.*?)\\\)", lambda m: "$" + m[1] + "$", text, flags=re.S)


def scientific_text(text):
    text = text.replace("\r\n", "\n")
    generated = False
    semantic_meta = ""
    if text.startswith("---\n"):
        meta, text = frontmatter(text)
        generated = meta.get("generated") is True
        semantic = {
            k: v
            for k, v in meta.items()
            if k
            not in {
                "id",
                "generated",
                "kind",
                "aliases",
                "cssclasses",
                "created",
                "updated",
                "last_sync",
                "last_success",
            }
        }
        if semantic:
            semantic_meta = (
                json.dumps(semantic, sort_keys=True, ensure_ascii=False, default=str) + "\n\n"
            )
    # Remove only Python-owned graph sections. Manual scientific text stays relevant.
    text = re.sub(r"<!-- links:start -->.*?<!-- links:end -->", "", text, flags=re.S)
    if generated and "<!-- managed:start -->" in text:
        before, rest = text.split("<!-- managed:start -->", 1)
        managed, after = rest.split("<!-- managed:end -->", 1)
        if meta.get("kind") in {"literature", "manuscript"}:
            managed = re.sub(r"^# [^\n]*\n", "", managed.lstrip(), count=1)
        managed = re.sub(
            r"<!-- bibliography:start -->.*?<!-- bibliography:end -->", "", managed, flags=re.S
        )
        # The rendered source metadata and processing report are not scientific claims.
        managed = managed.split("### Source metadata (attribution only)")[0]
        managed = re.sub(
            r"^(?:Processing coverage:|Incomplete coverage:|Human-reviewed draft \(approval )[^\n]*\n?",
            "",
            managed,
            flags=re.M,
        )
        managed = managed.split("### Extraction uncertainty")[0]
        managed = re.sub(
            r"Processing limitation: PDF processing supplies text only\..*?(?=\n\n|$)",
            "",
            managed,
            flags=re.S,
        )
        managed = re.sub(r"### Processed sources\n.*?(?=### Evidence|$)", "", managed, flags=re.S)
        text = before + managed + after
    scaffolding = {
        "## Approved conceptual relationships",
        "## Your additions",
        "Write here. This section is preserved by synchronization.",
    }
    result = (
        semantic_meta
        + "\n".join(
            line.rstrip()
            for line in math_markdown(text).splitlines()
            if line.strip() not in scaffolding and not APPROVED_LINE.fullmatch(line)
        ).strip()
    )
    return re.sub(r"\n{3,}", "\n\n", result).strip()


def scientific_notes(notes):
    return {n: scientific_text(t) for n, t in sorted(notes.items())}


def graph_signature(notes, recipe):
    return digest([scientific_notes(notes), recipe])
