"""Read-only PDF and constrained LaTeX extraction with explicit provenance."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .config import OCR_DEFAULTS, Config
from .paths import local_path
from .storage import VaultError, digest, file_hash


def ocr_diagnostics(settings: dict) -> dict:
    """Check local availability without running OCR or sending any source material."""
    result = {**settings, "status": "disabled", "resolved_executable": None}
    if settings["enabled"]:
        try:
            executable = shutil.which(str(local_path(settings["executable"])))
            result.update(
                status="available" if executable else "unavailable",
                resolved_executable=executable,
            )
        except (OSError, VaultError) as exc:
            result.update(status="unavailable", reason=str(exc))
    return result


def ocr_page(page, settings: dict) -> str:
    """Render only a deficient page; stream PNG bytes to local Tesseract, without a shell."""
    import pymupdf

    executable = ocr_diagnostics(settings)["resolved_executable"]
    if not executable:
        raise VaultError("OCR unavailable: configured Tesseract executable was not found")
    image = page.get_pixmap(dpi=settings["dpi"], colorspace=pymupdf.csRGB, alpha=False)
    result = subprocess.run(
        [executable, "stdin", "stdout", "-l", settings["language"], "--dpi", str(settings["dpi"])],
        input=image.tobytes("png"),
        capture_output=True,
        timeout=settings["timeout_seconds"],
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()[:1000]
        raise VaultError(f"Tesseract exited {result.returncode}: {detail}")
    return result.stdout.decode("utf-8")


def usable_pages(pages: list[dict]) -> list[dict]:
    return [p for p in pages if p.get("extraction_status") != "unprocessed" and p["text"].strip()]


def extraction_complete(pages: list[dict]) -> bool:
    return bool(pages) and all(
        p.get("extraction_status") != "unprocessed" and not p.get("incomplete") for p in pages
    )


def page_coverage(pages: list[dict]) -> list[dict]:
    """Coverage metadata is never source text or evidence of reading a missing page."""
    return [
        {
            k: p[k]
            for k in ("source", "locator", "extraction_status", "incomplete", "reason")
            if k in p
        }
        for p in pages
        if "extraction_status" in p
    ]


def coverage_error(pages: list[dict]) -> str | None:
    missing = [
        f"{p['locator']}: {p.get('reason', 'insufficient extractable text')}"
        for p in pages
        if p.get("extraction_status") == "unprocessed" or p.get("incomplete")
    ]
    return "Incomplete PDF coverage; " + "; ".join(missing) if missing else None


def extract_pdf_page(document, index: int, source: str, settings: dict) -> dict:
    record = {
        "source": source,
        "locator": f"page {index + 1}",
        "text": "",
        "extraction_status": "unprocessed",
        "incomplete": True,
        "extraction_warnings": [
            "PDF text order, equations and table structure are unverified. Figure/image understanding was not performed."
        ],
    }
    reasons = []
    try:
        page = document[index]
    except Exception as exc:
        record["reason"] = f"Page could not be loaded: {exc}"
        return record
    try:
        record["text"] = page.get_text()
        if record["text"].strip():
            record["extraction_status"] = "text"
    except Exception as exc:
        reasons.append(f"Native text extraction failed: {exc}")
    if len("".join(record["text"].split())) >= settings["min_text_chars"]:
        record["incomplete"] = False
        return record
    reasons.append(
        "Insufficient extractable text (possibly blank or image-only; content unverified)"
    )
    if settings["enabled"]:
        try:
            recognized = ocr_page(page, settings)
            if recognized.strip():
                native = record["text"]
                # Preserve sparse native content as well as the recognized text.
                record.update(text=recognized, extraction_status="ocr")
                if native.strip() and native.strip() not in recognized:
                    record["text"] = native + "\n" + recognized
                record["extraction_warnings"].append(
                    "OCR text is unverified and may contain recognition errors, especially in mathematics and tables; consult the original page."
                )
                if len("".join(recognized.split())) >= settings["min_text_chars"]:
                    record["incomplete"] = False
                    return record
            reasons.append("OCR returned insufficient text")
        except Exception as exc:
            reasons.append(f"OCR failed: {exc}")
    else:
        reasons.append("OCR disabled; no OCR was performed")
    record["reason"] = "; ".join(reasons)
    record["extraction_warnings"].append(record["reason"])
    return record


def pdf(path: Path, source: str, ocr: dict | None = None, page_cache=None) -> list[dict]:
    settings = {**OCR_DEFAULTS, **(ocr or {})}
    try:
        import pymupdf

        with pymupdf.open(path) as document:
            if document.needs_pass:
                raise VaultError(f"Encrypted PDF: {path.name}")
            pages = []
            for i in range(len(document)):

                def extract():
                    return extract_pdf_page(document, i, source, settings)

                pages.append(page_cache(i, extract) if page_cache else extract())
            if not pages:
                raise VaultError("PDF has no pages")
    except Exception as exc:
        raise VaultError(f"Unreadable PDF {path.name}: {exc}") from exc
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
            pdf_reader(path, str(path), hashes[str(path)])
            if pdf_reader
            else pdf(path, str(path), config.ocr)
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
