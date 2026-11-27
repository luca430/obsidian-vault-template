"""Synthetic PDF page coverage, optional local OCR and incremental retry regressions."""

from __future__ import annotations

import json
import subprocess

import pymupdf
import pytest
import yaml

from research_vault import sources
from research_vault.config import Config
from research_vault.recipes import stage_recipe
from research_vault.storage import VaultError

NATIVE = "Native scientific evidence with stated assumptions."
RECOGNIZED = "Recognized scientific evidence from a scanned page."


def make_pdf(path, kinds):
    """Rasterize synthetic text to create genuine image-only pages, without OCR."""
    with pymupdf.open() as document:
        for kind in kinds:
            page = document.new_page(width=300, height=200)
            if kind == "scan":
                with pymupdf.open() as raster:
                    original = raster.new_page(width=300, height=200)
                    original.insert_text((10, 50), RECOGNIZED, fontsize=8)
                    page.insert_image(page.rect, stream=original.get_pixmap().tobytes("png"))
            elif kind != "blank":
                page.insert_text((10, 50), NATIVE if kind == "text" else kind, fontsize=8)
        document.save(path)
    return path


def enable_ocr(pipeline):
    pipeline.config.data["ocr"] = {"enabled": True}


def test_mixed_native_scanned_and_blank_pages_preserve_native_content(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / "mixed.pdf", ["text", "scan", "blank"])
    monkeypatch.setattr(sources, "ocr_page", lambda *a: pytest.fail("OCR is disabled"))
    pages = sources.pdf(path, "PDF00001")
    assert [p["extraction_status"] for p in pages] == ["text", "unprocessed", "unprocessed"]
    assert [p["locator"] for p in pages] == ["page 1", "page 2", "page 3"]
    assert all(p["source"] == "PDF00001" for p in pages)
    assert NATIVE in pages[0]["text"]
    assert not sources.extraction_complete(pages)
    assert sources.usable_pages(pages) == pages[:1]


def test_fully_scanned_pdf_uses_ocr_per_page(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / "scanned.pdf", ["scan", "scan"])
    calls = []

    def recognize(page, settings):
        calls.append(page.number)
        return RECOGNIZED

    monkeypatch.setattr(sources, "ocr_page", recognize)
    pages = sources.pdf(path, "SCAN0001", {"enabled": True})
    assert calls == [0, 1]
    assert sources.extraction_complete(pages)
    assert all(p["extraction_status"] == "ocr" for p in pages)
    assert all("recognition errors" in " ".join(p["extraction_warnings"]) for p in pages)


def test_mixed_pdf_ocr_only_runs_on_deficient_pages_and_blank_stays_unread(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / "mixed-ocr.pdf", ["text", "scan", "blank"])
    calls = []

    def recognize(page, settings):
        calls.append(page.number)
        return RECOGNIZED if page.number == 1 else ""

    monkeypatch.setattr(sources, "ocr_page", recognize)
    pages = sources.pdf(path, "PDF00001", {"enabled": True})
    assert calls == [1, 2]
    assert [p["extraction_status"] for p in pages] == ["text", "ocr", "unprocessed"]
    assert sources.page_coverage(pages)[2]["incomplete"]
    assert "OCR returned insufficient text" in pages[2]["reason"]


@pytest.mark.parametrize("failure", ["missing", "timeout", "exit", "empty", "sparse"])
def test_ocr_failure_retains_other_pages_and_sparse_native_text(tmp_path, monkeypatch, failure):
    path = make_pdf(tmp_path / "failure.pdf", ["text", "scan", "42"])

    def recognize(*args):
        if failure == "missing":
            raise FileNotFoundError("Tesseract unavailable")
        if failure == "timeout":
            raise subprocess.TimeoutExpired("tesseract", 1)
        if failure == "exit":
            raise subprocess.CalledProcessError(1, "tesseract")
        return "7" if failure == "sparse" else ""

    monkeypatch.setattr(sources, "ocr_page", recognize)
    pages = sources.pdf(path, "PDF00001", {"enabled": True})
    assert pages[0]["extraction_status"] == "text" and not pages[0]["incomplete"]
    assert NATIVE in pages[0]["text"]
    assert pages[1]["incomplete"] and pages[2]["incomplete"]
    assert "42" in pages[2]["text"]
    assert "page 2" in sources.coverage_error(pages)
    assert not sources.extraction_complete(pages)


def test_native_page_error_is_isolated_and_can_use_ocr(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / "page-error.pdf", ["text", "scan", "text"])
    original = pymupdf.Page.get_text

    def fail_one(page, *args, **kwargs):
        if page.number == 1:
            raise RuntimeError("Damaged page text")
        return original(page, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "get_text", fail_one)
    pages = sources.pdf(path, "PDF00001")
    assert [p["extraction_status"] for p in pages] == ["text", "unprocessed", "text"]
    assert "Damaged page text" in pages[1]["reason"]
    monkeypatch.setattr(sources, "ocr_page", lambda *a: RECOGNIZED)
    assert sources.extraction_complete(sources.pdf(path, "PDF00001", {"enabled": True}))


def test_threshold_is_configurable_and_native_pages_do_not_invoke_ocr(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / "sparse.pdf", ["42"])
    monkeypatch.setattr(sources, "ocr_page", lambda *a: pytest.fail("Sufficient native text"))
    pages = sources.pdf(path, "PDF00001", {"enabled": True, "min_text_chars": 2})
    assert sources.extraction_complete(pages)
    assert pages[0]["extraction_status"] == "text"


def test_tesseract_adapter_streams_png_and_sets_timeout_without_shell(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / "scan.pdf", ["scan"])
    monkeypatch.setattr(sources.shutil, "which", lambda name: "/synthetic path/tesseract")

    def run(args, **kwargs):
        assert args == [
            "/synthetic path/tesseract",
            "stdin",
            "stdout",
            "-l",
            "eng+ita",
            "--dpi",
            "150",
        ]
        assert kwargs["input"].startswith(b"\x89PNG")
        assert kwargs["timeout"] == 7
        assert not kwargs.get("shell")
        return subprocess.CompletedProcess(args, 0, RECOGNIZED.encode(), b"")

    monkeypatch.setattr(sources, "run_process", run)
    pages = sources.pdf(
        path, "PDF00001", {"enabled": True, "language": "eng+ita", "timeout_seconds": 7}
    )
    assert pages[0]["text"] == RECOGNIZED


def test_unavailable_tesseract_does_not_discard_native_pages(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / "mixed.pdf", ["text", "scan"])
    monkeypatch.setattr(sources.shutil, "which", lambda name: None)
    monkeypatch.setattr(sources, "run_process", lambda *a, **k: pytest.fail("No engine"))
    pages = sources.pdf(path, "PDF00001", {"enabled": True})
    assert pages[0]["extraction_status"] == "text"
    assert "OCR unavailable" in pages[1]["reason"]


def test_tesseract_language_failure_records_stderr_for_diagnosis(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / "scan.pdf", ["scan"])
    monkeypatch.setattr(sources.shutil, "which", lambda name: "tesseract")
    monkeypatch.setattr(
        sources,
        "run_process",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, b"", b"Error opening language data eng"),
    )
    pages = sources.pdf(path, "PDF00001", {"enabled": True})
    assert pages[0]["extraction_status"] == "unprocessed"
    assert "Error opening language data eng" in pages[0]["reason"]


def test_partial_paper_records_coverage_excludes_unread_evidence_and_reuses_summary(
    vault, monkeypatch
):
    root, source, zotero, agent, pipeline = vault
    path = make_pdf(source / "mixed.pdf", ["text", "scan", "blank"])
    zotero.paper("PAPER001", [("PDF00001", path, "main")])
    extracted_pages = []
    extract = sources.extract_pdf_page

    def track(document, index, *args):
        extracted_pages.append(index)
        return extract(document, index, *args)

    monkeypatch.setattr(sources, "extract_pdf_page", track)
    report = pipeline.sync()
    assert "PAPER001" in report["partial"] and not report["complete"]
    record = pipeline.state()["papers"]["PAPER001"]
    attachment = record["attachment_processing"]["PDF00001"]
    assert record["completeness"] == attachment["completeness"] == "partial"
    assert attachment["extraction"] == "partial" and attachment["summary"] == "complete"
    assert attachment["pages"] == ["page 1"]
    assert len(attachment["page_processing"]) == 3
    payload = next(p for r, p in agent.calls if r == "literature")
    assert [p["locator"] for p in payload["pages"]] == ["page 1"]
    assert payload["coverage"] == "partial" and len(payload["page_processing"]) == 3
    note = (root / "Literature/Synthetic item — PAPER001.md").read_text()
    processed = note.split("### Processed sources")[1].split("### Unprocessed pages")[0]
    assert "page 1 (text)" in processed and "page 2" not in processed
    assert "Unprocessed pages (not read)" in note and "page 3" in note
    assert all(
        p["locator"] == "page 1" for p in json.loads((root / record["passages"]).read_text())
    )
    summaries = sum(r == "literature" for r, p in agent.calls)
    assert not pipeline.sync()["complete"]
    assert sum(r == "literature" for r, p in agent.calls) == summaries
    assert extracted_pages == [0, 1, 2]
    pipeline.sync(retry=True)
    assert extracted_pages == [0, 1, 2, 1, 2]


def test_ocr_retry_reuses_successful_pages_then_becomes_complete(vault, monkeypatch):
    root, source, zotero, agent, pipeline = vault
    enable_ocr(pipeline)
    path = make_pdf(source / "scans.pdf", ["scan", "scan"])
    zotero.paper("PAPER001", [("PDF00001", path, "main")])
    calls, recovered = [], False

    def recognize(page, settings):
        calls.append(page.number)
        if page.number == 1 and not recovered:
            raise RuntimeError("Temporary engine failure")
        return RECOGNIZED

    monkeypatch.setattr(sources, "ocr_page", recognize)
    assert not pipeline.sync()["complete"]
    assert calls == [0, 1]
    recovered = True
    assert pipeline.sync(retry=True)["complete"]
    assert calls == [0, 1, 1]
    record = pipeline.state()["papers"]["PAPER001"]
    assert record["completeness"] == "complete"
    assert all(
        p["extraction_status"] == "ocr"
        for p in record["attachment_processing"]["PDF00001"]["page_processing"]
    )
    note = (root / "Literature/Synthetic item — PAPER001.md").read_text()
    assert "page 2 (ocr)" in note and "recognition is unverified" in note
    before = len(agent.calls)
    assert pipeline.sync()["complete"]
    assert len(agent.calls) == before and calls == [0, 1, 1]


def test_interrupted_extraction_keeps_completed_page_cache(vault, monkeypatch):
    _, source, zotero, _, pipeline = vault
    enable_ocr(pipeline)
    path = make_pdf(source / "interrupted.pdf", ["scan", "scan"])
    zotero.paper("PAPER001", [("PDF00001", path, "main")])
    calls, interrupted = [], False

    def recognize(page, settings):
        nonlocal interrupted
        calls.append(page.number)
        if page.number == 1 and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return RECOGNIZED

    monkeypatch.setattr(sources, "ocr_page", recognize)
    with pytest.raises(KeyboardInterrupt):
        pipeline.sync()
    assert pipeline.sync()["complete"]
    assert calls == [0, 1, 1]


def test_fully_scanned_without_ocr_records_partial_coverage_without_inference(vault):
    root, source, zotero, agent, pipeline = vault
    path = make_pdf(source / "scan.pdf", ["scan", "blank"])
    zotero.paper("PAPER001", [("PDF00001", path, "main")])
    result = pipeline.sync()
    assert not result["complete"] and "PAPER001" in result["partial"]
    record = pipeline.state()["papers"]["PAPER001"]
    assert record["completeness"] == "partial"
    assert len(record["attachment_processing"]["PDF00001"]["page_processing"]) == 2
    assert not agent.calls and not (root / "Literature/Synthetic item — PAPER001.md").exists()


def test_pdf_manuscript_coverage_is_partial_and_retried(vault, monkeypatch):
    root, source, _, agent, pipeline = vault
    enable_ocr(pipeline)
    path = make_pdf(source / "manuscript.pdf", ["text", "scan"])
    work = {
        "id": "draft",
        "title": "Draft",
        "document_type": "report",
        "status": "draft",
        "source_type": "pdf",
        "source_path": str(path),
    }
    (root / "works.yaml").write_text(yaml.safe_dump({"works": [work]}))
    monkeypatch.setattr(sources, "ocr_page", lambda *a: "")
    assert "work:draft" in pipeline.sync()["partial"]
    assert pipeline.state()["works"]["draft"]["completeness"] == "partial"
    assert "Unprocessed pages (not read)" in (root / "My Work/draft.md").read_text()
    monkeypatch.setattr(sources, "ocr_page", lambda *a: RECOGNIZED)
    assert pipeline.sync(retry=True)["complete"]
    assert pipeline.status()["manuscript_processing"]["draft"]["completeness"] == "complete"


def test_bibliography_does_not_make_unread_pdf_manuscript_processable(vault):
    root, source, _, agent, pipeline = vault
    path = make_pdf(source / "scan.pdf", ["scan"])
    bibliography = source / "refs.bib"
    bibliography.write_text("Synthetic attribution only; no manuscript evidence.")
    work = {
        "id": "scan",
        "title": "Scan",
        "document_type": "report",
        "status": "draft",
        "source_type": "pdf",
        "source_path": str(path),
        "bibliography_path": str(bibliography),
    }
    (root / "works.yaml").write_text(yaml.safe_dump({"works": [work]}))
    assert not pipeline.sync()["complete"]
    assert pipeline.state()["works"]["scan"]["completeness"] == "partial"
    assert not agent.calls and not (root / "My Work/scan.md").exists()


def test_wsl_ocr_executable_uses_existing_local_path_translation(monkeypatch):
    raw = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    seen = []

    def translate(value):
        seen.append(value)
        return sources.Path("/mnt/c/Program Files/Tesseract-OCR/tesseract.exe")

    monkeypatch.setattr(sources, "local_path", translate)
    monkeypatch.setattr(sources.shutil, "which", lambda name: name)
    result = sources.ocr_diagnostics({**sources.OCR_DEFAULTS, "enabled": True, "executable": raw})
    assert seen == [raw] and result["status"] == "available"
    assert result["resolved_executable"].startswith("/mnt/c/")


@pytest.mark.parametrize("available", [False, True])
def test_diagnostics_report_missing_pages_without_ocr_or_writes(vault, monkeypatch, available):
    root, source, zotero, agent, pipeline = vault
    enable_ocr(pipeline)
    path = make_pdf(source / "mixed.pdf", ["text", "scan"])
    zotero.paper("PAPER001", [("PDF00001", path, "main")])
    monkeypatch.setattr(sources, "ocr_page", lambda *a: pytest.fail("Diagnostics must not run OCR"))
    monkeypatch.setattr(
        sources.shutil, "which", lambda name: "/synthetic/tesseract" if available else None
    )
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    report = pipeline.sync(dry_run=True)
    assert report["processable"] == ["PAPER001"]
    assert "PAPER001" in report["partial_candidates"]
    assert report["page_processing"]["PDF00001"][1]["extraction_status"] == "unprocessed"
    assert report["ocr"]["enabled"] is True
    page = report["page_processing"]["PDF00001"][1]
    assert page["ocr_configured_enabled"] is True and page["ocr_attempted"] is False
    assert "OCR not run during native-text-only diagnostics" in page["reason"]
    assert "OCR disabled" not in json.dumps(report)
    agent.command_prefix = lambda: ["fake-codex"]
    agent.authenticate = lambda: None
    monkeypatch.setattr(
        "research_vault.pipeline.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(
            a, 0, "--ignore-user-config --ignore-rules --ephemeral --output-schema", ""
        ),
    )
    doctor = pipeline.doctor()
    assert doctor["ocr"]["enabled"] is True
    assert doctor["ocr"]["status"] == ("available" if available else "unavailable")
    assert doctor["config_file"] == str(root / "config.yaml")
    assert doctor["pdf_pages"]["PDF00001"][1]["extraction_status"] == "unprocessed"
    assert doctor["warnings"] and not doctor["blocking_errors"]
    assert (
        "OCR not run during native-text-only diagnostics"
        in doctor["pdf_pages"]["PDF00001"][1]["reason"]
    )
    assert "OCR disabled" not in json.dumps(doctor)
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert not agent.calls


@pytest.mark.parametrize("enabled", [False, True])
def test_native_inspection_preserves_configured_ocr_setting_and_actual_read_coverage(
    tmp_path, monkeypatch, enabled
):
    path = make_pdf(tmp_path / "inspection.pdf", ["text", "scan"])
    settings = {**sources.OCR_DEFAULTS, "enabled": enabled}
    before = dict(settings)
    monkeypatch.setattr(
        sources, "ocr_page", lambda *a: pytest.fail("Inspection must not invoke OCR")
    )
    pages = sources.inspect_pdf(path, "PDF00001", settings)
    assert settings == before
    assert [p["extraction_status"] for p in pages] == ["text", "unprocessed"]
    assert all(
        p["ocr_configured_enabled"] is enabled and not p["ocr_attempted"]
        for p in sources.page_coverage(pages)
    )
    assert all(p["diagnostic_only"] for p in pages)
    assert ("OCR disabled" in pages[1]["reason"]) is (not enabled)
    assert not sources.extraction_complete(pages)


def test_manuscript_diagnostics_do_not_misreport_enabled_ocr(vault, monkeypatch):
    root, source, _, agent, pipeline = vault
    enable_ocr(pipeline)
    path = make_pdf(source / "manuscript.pdf", ["text", "scan"])
    work = {
        "id": "draft",
        "title": "Draft",
        "document_type": "manuscript",
        "status": "draft",
        "source_type": "pdf",
        "source_path": str(path),
    }
    (root / "works.yaml").write_text(yaml.safe_dump({"works": [work]}))
    monkeypatch.setattr(
        sources, "ocr_page", lambda *a: pytest.fail("Diagnostics must not invoke OCR")
    )
    agent.command_prefix = lambda: ["fake-codex"]
    agent.authenticate = lambda: None
    monkeypatch.setattr(
        "research_vault.pipeline.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(
            a, 0, "--ignore-user-config --ignore-rules --ephemeral --output-schema", ""
        ),
    )
    dry = pipeline.sync(dry_run=True)
    doctor = pipeline.doctor()
    for page in (
        dry["page_processing"]["work:draft"][1],
        doctor["manuscripts"][0]["page_processing"][1],
    ):
        assert page["ocr_configured_enabled"] is True
        assert "OCR not run during native-text-only diagnostics" in page["reason"]
        assert "OCR disabled" not in page["reason"]
    assert pipeline.config.ocr["enabled"] is True
    assert not agent.calls


@pytest.mark.parametrize(
    "settings",
    [
        {"enabled": "yes"},
        {"dpi": True},
        {"dpi": 601},
        {"timeout_seconds": 0},
        {"min_text_chars": 0},
        {"language": "--help"},
        {"executable": ""},
    ],
)
def test_invalid_ocr_config_is_rejected(vault, settings):
    root, _, _, _, _ = vault
    config = yaml.safe_load((root / "config.yaml").read_text())
    config["ocr"] = settings
    (root / "config.yaml").write_text(yaml.safe_dump(config))
    with pytest.raises(VaultError, match="ocr\\."):
        Config.load(root)


def test_ocr_configuration_invalidates_extraction_and_summary_contracts(vault):
    pipeline = vault[-1]
    before = {
        s: stage_recipe(pipeline.config, s) for s in ("extraction", "literature", "synthesis")
    }
    enable_ocr(pipeline)
    after = {s: stage_recipe(pipeline.config, s) for s in before}
    assert before["extraction"] != after["extraction"]
    assert before["literature"] != after["literature"]
    assert before["synthesis"] == after["synthesis"]
