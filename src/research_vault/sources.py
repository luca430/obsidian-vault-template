"""Read-only PDF and constrained LaTeX extraction with explicit provenance."""

from __future__ import annotations

import re
from pathlib import Path

from .config import Config
from .storage import VaultError, digest, file_hash


def pdf(path: Path, source: str) -> list[dict]:
    try:
        import pymupdf

        with pymupdf.open(path) as document:
            if document.needs_pass:
                raise VaultError(f"Encrypted PDF: {path.name}")
            pages = [
                {
                    "source": source,
                    "locator": f"page {i + 1}",
                    "text": page.get_text(),
                    "extraction_warnings": [
                        "PDF text order, equations, table structure and figure content are unverified; no OCR or image understanding was performed."
                    ],
                }
                for i, page in enumerate(document)
            ]
    except Exception as exc:
        raise VaultError(f"Unreadable PDF {path.name}: {exc}") from exc
    if not pages or any(not p["text"].strip() for p in pages):
        raise VaultError(
            f"PDF {path.name} contains pages without extractable text; OCR is required (not implemented)"
        )
    return pages


def latex(config: Config, path: Path) -> tuple[list[dict], dict[str, str]]:
    hashes: dict[str, str] = {}
    pages = []
    active: set[Path] = set()

    def visit(current: Path) -> str:
        current = config.authorized(str(current))
        if current in active:
            raise VaultError(f"Cyclic LaTeX include: {current.name}")
        if len(active) >= 100:
            raise VaultError("LaTeX include depth exceeds 100")
        active.add(current)
        text = current.read_text(encoding="utf-8")
        hashes[str(current)] = file_hash(current)
        # Keep mathematics; remove only unescaped comment tails for include parsing.
        text = re.sub(r"(?<!\\)%[^\n]*", "", text)
        if re.search(r"\\(?:input|include)\s*(?!\s*\{)[^\s{]", text):
            raise VaultError("Only literal braced LaTeX input/include directives are supported")
        pages.append({"source": str(current), "locator": "LaTeX source", "text": text})

        def expand(match: re.Match) -> str:
            name = match.group(1)
            if any(c in name for c in "\\#$~") or Path(name).is_absolute():
                raise VaultError("Dynamic or absolute LaTeX includes are unsupported")
            child = current.parent / name
            if not child.suffix:
                child = child.with_suffix(".tex")
            return "\n" + visit(child) + "\n"

        resolved = re.sub(r"\\(?:input|include)\s*\{([^{}]+)\}", expand, text)
        active.remove(current)
        return resolved

    resolved = visit(path)
    pages.append({"source": str(path), "locator": "resolved LaTeX", "text": resolved})
    return pages, hashes


def manuscript(config: Config, work: dict, pdf_reader=None) -> dict:
    path = config.authorized(work["source_path"])
    if work["source_type"] == "latex":
        pages, hashes = latex(config, path)
    else:
        hashes = {str(path): file_hash(path)}
        pages = (
            pdf_reader(path, str(path), hashes[str(path)]) if pdf_reader else pdf(path, str(path))
        )
    if work.get("bibliography_path"):
        bibliography = config.authorized(work["bibliography_path"])
        hashes[str(bibliography)] = file_hash(bibliography)
        pages.append(
            {
                "source": str(bibliography),
                "locator": "bibliography (attribution only)",
                "text": bibliography.read_text(encoding="utf-8"),
            }
        )
    return {
        "metadata": work,
        "pages": pages,
        "files": hashes,
        "fingerprint": digest([work, hashes]),
    }
