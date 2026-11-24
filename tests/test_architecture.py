from __future__ import annotations

import json
import subprocess
from copy import deepcopy

import pytest
from conftest import decide, link, write_pdf
from test_pipeline import add_paper, proposals

from research_vault.agents import fits, validate_summary
from research_vault.chunking import section_chunks
from research_vault.cli import main
from research_vault.execution import launch
from research_vault.pipeline import Pipeline
from research_vault.recipes import stage_recipe
from research_vault.registry import Registry
from research_vault.storage import VaultError, lock


def test_missing_supplement_processes_main_then_reuses_it(vault):
    root, source, z, agent, pipeline = vault
    main_pdf = write_pdf(source / "main.pdf", "Main results with scientific assumptions.")
    supplement = write_pdf(source / "supplement.pdf", "Supplementary scientific measurements.")
    z.paper("PAPER001", [("PDF00001", main_pdf, "main"), ("PDF00002", supplement, "supplementary")])
    complete = deepcopy(z.items["PAPER001"])
    z.items["PAPER001"]["attachments"][1]["error"] = "OneDrive file temporarily unavailable"
    z.items["PAPER001"]["fingerprint"] = "partial-fingerprint"
    result = pipeline.sync()
    assert not result["complete"]
    record = pipeline.state()["papers"]["PAPER001"]
    assert record["status"] == "successful" and record["completeness"] == "partial"
    assert record["attachment_processing"]["PDF00001"]["completeness"] == "complete"
    assert record["attachment_processing"]["PDF00002"]["completeness"] == "metadata_only"
    text = (root / "Literature/PAPER001.md").read_text()
    assert "Processing coverage: partial" in text
    assert "Main results" in text and "Supplementary scientific measurements" not in text
    assert pipeline.sync()[
        "partial"
    ]  # Retry the unavailable attachment; main inference stays cached.
    z.items["PAPER001"] = complete
    assert pipeline.sync()["complete"]
    assert pipeline.state()["papers"]["PAPER001"]["completeness"] == "complete"
    calls = [p for role, p in agent.calls if role == "literature" and p.get("item_key")]
    assert sum(p["attachments"][0]["key"] == "PDF00001" for p in calls) == 1
    assert sum(p["attachments"][0]["key"] == "PDF00002" for p in calls) == 1
    assert "Supplementary scientific measurements" in (root / "Literature/PAPER001.md").read_text()


def test_failed_supplement_keeps_main_scientific_summary(vault):
    root, source, z, _, pipeline = vault
    main_pdf = write_pdf(source / "main.pdf")
    broken = source / "broken.pdf"
    broken.write_bytes(b"Not PDF text")
    z.paper("PAPER001", [("PDF00001", main_pdf, "main"), ("PDF00002", broken, "supplementary")])
    result = pipeline.sync()
    record = pipeline.state()["papers"]["PAPER001"]
    assert "PAPER001" in result["partial"]
    assert record["attachment_processing"]["PDF00002"]["completeness"] == "failed"
    assert (root / "Literature/PAPER001.md").exists()
    assert not proposals(pipeline, "removal")


def test_metadata_only_never_calls_summary_or_creates_fulltext_claims(vault):
    root, _, z, agent, pipeline = vault
    z.paper("PAPER001", abstractNote="Metadata-only unverified finding.")
    assert not pipeline.sync()["complete"]
    assert pipeline.status()["completeness"]["metadata_only"] == ["PAPER001"]
    assert not agent.calls
    assert not (root / "Literature/PAPER001.md").exists()


def test_registry_persists_candidates_and_source_provenance_without_public_edges(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    original = agent.run

    def concepts(role, payload):
        if role == "concepts":
            name = next(iter(payload["notes"]))
            if "Synthetic scientific evidence." in payload["notes"][name]:
                agent.calls.append((role, deepcopy(payload)))
                return {
                    "concepts": [
                        {
                            "title": "Scoped mechanism",
                            "definition": "A provisional mechanistic definition.",
                            "evidence": [{"note": name, "quote": "Synthetic scientific evidence."}],
                        }
                    ]
                }
        return original(role, payload)

    agent.run = concepts
    assert pipeline.sync()["complete"]
    state = pipeline.state()
    registry = state["concept_registry"]
    assert registry["concepts"]
    concept = next(iter(registry["concepts"].values()))
    assert concept["status"] == "unreviewed_retrieval_annotation"
    assert concept["sources"]["Literature/PAPER001.md"]["provenance"]["source_fingerprint"]
    assert not list((root / "Concepts").glob("*.md"))
    assert "[[" not in (root / "Literature/PAPER001.md").read_text()
    before = len(agent.calls)
    Pipeline(pipeline.config, vault[2], agent).sync()
    assert len(agent.calls) == before


def test_stage_specific_instructions_do_not_reprocess_other_stages(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    pipeline.sync()
    baseline = {
        stage: stage_recipe(pipeline.config, stage)
        for stage in ("extraction", "literature", "concepts", "linking", "synthesis")
    }
    before = len(agent.calls)
    with (root / "agents/synthesis.md").open("a") as stream:
        stream.write("\nReassess explicit scope.\n")
    assert pipeline.sync()["complete"]
    recent = [role for role, _ in agent.calls[before:]]
    assert recent and set(recent) == {"synthesis"}
    assert stage_recipe(pipeline.config, "literature") == baseline["literature"]
    assert stage_recipe(pipeline.config, "extraction") == baseline["extraction"]
    before = len(agent.calls)
    with (root / "agents/concepts.md").open("a") as stream:
        stream.write("\nPrefer scoped definitions.\n")
    assert pipeline.sync()["complete"]
    assert all(role not in {"literature", "manuscript"} for role, _ in agent.calls[before:])
    for namespace in (
        "extractions",
        "document_summaries",
        "concept_extraction",
        "relationship_proposals",
        "high_level_synthesis",
    ):
        assert (root / ".pipeline/cache" / namespace).is_dir()


def test_section_and_math_boundaries_preserve_context_and_provenance():
    text = "1 Introduction\nA definition with assumptions.\n\n2 Methods\nAssume x > 0.\n$$E=mc^2$$\nThis explains the result and its limitation.\n"
    records = [{"source": "PDF00001", "locator": "page 7", "text": text}]
    chunks = list(section_chunks(records, 38, 20))
    assert "".join(c["text"] for c in chunks) == text
    assert any(c["section"].startswith("2 Methods") for c in chunks)
    assert any("$$E=mc^2$$" in c["text"] for c in chunks)
    for chunk in chunks:
        assert chunk["source"] == "PDF00001" and chunk["locator"] == "page 7"
        start = chunk["character_offset"]
        assert text[start : start + len(chunk["text"])] == chunk["text"]
    assert any(c.get("context_before") or c.get("context_after") for c in chunks)


def test_oversized_equation_is_explicitly_uncertain_and_not_dropped():
    text = "Assumption before.\n$$" + "x+y+" * 200 + "z$$\nExplanation after."
    chunks = list(
        section_chunks([{"source": "main.tex", "locator": "Methods", "text": text}], 100, 20)
    )
    assert "".join(c["text"] for c in chunks) == text
    assert any(
        any("Mathematical expression exceeds" in w for w in c["extraction_warnings"])
        for c in chunks
    )
    assert all(len(c["text"]) <= 100 for c in chunks)


def test_statement_provenance_is_checked_independently():
    result = {
        "evidence": [{"source": "PDF00001", "locator": "page 1", "quote": "Actual expression."}],
        "claims": [
            {
                "statement": "Uncertain reconstruction",
                "uncertainty": "Unverified equation.",
                "evidence": [
                    {"source": "PDF00001", "locator": "page 1", "quote": "Fabricated expression."}
                ],
            }
        ],
    }
    with pytest.raises(VaultError, match="absent"):
        validate_summary(
            result, [{"source": "PDF00001", "locator": "page 1", "text": "Actual expression."}]
        )


def test_startup_is_bounded_and_manual_sync_resumes_reasoning(vault):
    _, source, z, agent, pipeline = vault
    pipeline.config.data["processing"] = {"startup_max_documents": 1}
    add_paper(vault)
    z.paper("PAPER002", [("PDF00002", write_pdf(source / "second.pdf"), "main")])
    result = pipeline.sync(startup=True)
    assert result["deferred_documents"] == 1 and result["reasoning_deferred"]
    assert not result["complete"]
    assert [role for role, _ in agent.calls] == ["literature"]
    assert pipeline.state()["papers"]["PAPER002"]["status"] == "pending"
    assert pipeline.sync()["complete"]
    assert sum(role == "literature" for role, _ in agent.calls) == 2
    assert any(role == "concepts" for role, _ in agent.calls)


def test_background_launch_is_detached_same_python_and_finite(vault, monkeypatch):
    root, _, _, _, pipeline = vault
    observed = {}

    def popen(command, **options):
        observed.update(command=command, options=options)
        return type("Child", (), {"pid": 12345})()

    monkeypatch.setattr("research_vault.execution.subprocess.Popen", popen)
    report = launch(pipeline.config, startup=True)
    command = observed["command"]
    assert command[command.index("--vault") + 1] == str(root)
    assert "--startup" in command and "--background" not in command
    options = observed["options"]
    assert not options.get("shell")
    assert options["stdin"] == subprocess.DEVNULL
    assert options["stderr"] == subprocess.STDOUT
    assert options.get("start_new_session") or options.get("creationflags")
    assert (root / report["report"]).exists()
    assert options["cwd"] == root
    with lock(root), pytest.raises(VaultError, match="Another pipeline"):
        launch(pipeline.config)


def test_background_child_reports_completion_and_accessible_progress(vault, monkeypatch, capsys):
    root, _, _, _, pipeline = vault
    add_paper(vault)
    monkeypatch.setattr(
        "research_vault.cli.Pipeline",
        lambda config, progress: Pipeline(config, vault[2], vault[3], progress),
    )
    run_id = "a" * 32
    assert main(["--vault", str(root), "sync", "--run-id", run_id]) == 0
    record = json.loads((root / f".pipeline/runs/{run_id}.json").read_text())
    assert record["status"] == "complete" and record["exit_code"] == 0
    progress = json.loads((root / ".pipeline/progress.json").read_text())
    assert progress["stage"] == "complete"
    assert pipeline.status()["last_report"]["complete"]
    assert "Last attempt: complete" in (root / "Home.md").read_text()
    capsys.readouterr()


def test_wsl_doctor_reports_tested_connectivity_not_assumed_mirroring(vault, monkeypatch):
    _, _, z, agent, pipeline = vault
    monkeypatch.setattr("research_vault.pipeline.environment", lambda: "WSL")
    monkeypatch.setattr(agent, "command_prefix", lambda: ["mock-codex"], raising=False)
    monkeypatch.setattr(agent, "authenticate", lambda: None, raising=False)
    monkeypatch.setattr(
        "research_vault.pipeline.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(
            a, 0, "--ignore-user-config --ignore-rules --ephemeral --output-schema", ""
        ),
    )
    assert pipeline.doctor()["wsl_validation"]["local_api"] == "reachable"
    z.fail = True
    result = pipeline.doctor()
    assert result["wsl_validation"]["local_api"] == "unreachable"
    assert result["blocking_errors"]


def test_registry_retrieves_old_personal_notes_and_manuscripts_for_new_paper(vault):
    import yaml

    root, source, z, agent, pipeline = vault
    personal = root / "My Notes/transport.md"
    personal.write_text(
        "Bifurcation under conserved transport; personal hypothesis, not established evidence."
    )
    draft = source / "draft.tex"
    draft.write_text(
        "\\section{Assumptions}\nBifurcation under conserved transport is a manuscript claim."
    )
    (root / "works.yaml").write_text(
        yaml.safe_dump(
            {
                "works": [
                    {
                        "id": "draft",
                        "title": "Draft",
                        "document_type": "manuscript",
                        "status": "draft",
                        "source_type": "latex",
                        "source_path": str(draft),
                    }
                ]
            }
        )
    )
    assert pipeline.sync()["complete"]
    before = len(agent.calls)
    path = write_pdf(
        source / "new.pdf",
        "Bifurcation under conserved transport. Explicit scientific assumptions.",
    )
    z.paper("PAPER001", [("PDF00001", path, "main")])
    assert pipeline.sync()["complete"]
    payloads = [p for r, p in agent.calls[before:] if r == "linking"]
    assert any(
        {"Literature/PAPER001.md", "My Notes/transport.md", "My Work/draft.md"} <= p["notes"].keys()
        for p in payloads
    )
    assert personal.read_text().startswith("Bifurcation under conserved transport;")


def test_bounded_registry_candidates_and_nonretrieval_is_not_absence(vault):
    from research_vault.processing import Processor

    config = vault[-1].config
    config.data["processing"] = {"candidate_pool": 3, "candidate_terms": 2, "graph_neighbors": 2}
    processor = Processor(config, vault[3], vault[-1].recipe(), lambda *a, **k: None)
    registry = Registry()
    notes = {
        f"My Notes/n{i:03d}.md": f"Conserved bifurcation hypothesis number {i}." for i in range(15)
    }
    for name, text in notes.items():
        registry.update(config, name, text, {}, processor)
    focus = "My Notes/n014.md"
    requests = list(registry.batches(config, notes, {focus}, [], {}, {}))
    assert requests
    assert all(len(p["notes"]) <= 3 for p in requests)
    assert all("not evidence of no relationship" in p["scope"] for p in requests)
    assert all(fits(config, "linking", p) for p in requests)
    assert registry.data["postings"] and len(registry.data["documents"]) == 15


def test_consolidation_failure_retains_validated_attachment_claims(vault):
    root, source, z, agent, pipeline = vault
    z.paper(
        "PAPER001",
        [
            ("PDF00001", write_pdf(source / "main.pdf"), "main"),
            (
                "PDF00002",
                write_pdf(source / "supplement.pdf", "Supplementary measurements."),
                "supplementary",
            ),
        ],
    )
    original = agent.run

    def fail_overview(role, payload):
        if role == "literature" and payload.get("processing_stage", "").startswith("consolidation"):
            raise VaultError("Overview temporarily failed")
        return original(role, payload)

    agent.run = fail_overview
    result = pipeline.sync()
    assert "consolidation" in result["partial"]["PAPER001"]
    assert pipeline.state()["papers"]["PAPER001"]["completeness"] == "partial"
    note = (root / "Literature/PAPER001.md").read_text()
    assert "Parent consolidation incomplete" in note and "Supplementary measurements." in note
    assert "PDF00001, page 1" in note and "PDF00002, page 1" in note
    agent.run = original
    assert pipeline.sync()["complete"]
    assert sum(r == "literature" and bool(p.get("item_key")) for r, p in agent.calls) == 2


def test_reviewed_relationships_only_enter_registry_after_apply(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    agent.links = [link()]
    assert pipeline.sync()["complete"]
    assert not pipeline.state()["concept_registry"]["reviewed_relationships"]
    pid = next(iter(proposals(pipeline, "link")))
    decide(root, pipeline.state(), pid)
    pipeline.apply()
    assert pid in pipeline.state()["concept_registry"]["reviewed_relationships"]
    assert (root / "Concepts/mechanism.md").exists()


def test_dry_run_keeps_main_processable_when_supplement_extraction_fails(vault):
    root, source, z, agent, pipeline = vault
    main_pdf = write_pdf(source / "main.pdf")
    broken = source / "bad.pdf"
    broken.write_bytes(b"Invalid PDF")
    z.paper("PAPER001", [("PDF00001", main_pdf, "main"), ("PDF00002", broken, "supplementary")])
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = pipeline.sync(dry_run=True)
    assert result["processable"] == ["PAPER001"] and not result["skipped"]
    assert "PDF00002" in result["partial_candidates"]["PAPER001"]
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert not agent.calls


def test_startup_missing_entries_do_not_starve_available_documents(vault):
    _, source, z, agent, pipeline = vault
    pipeline.config.data["processing"] = {"startup_max_documents": 1}
    z.paper("PAPER000")
    add_paper(vault)
    result = pipeline.sync(startup=True)
    assert "PAPER000" in result["skipped"]
    state = pipeline.state()
    assert state["papers"]["PAPER000"]["completeness"] == "metadata_only"
    assert state["papers"]["PAPER001"]["completeness"] == "complete"
    assert [r for r, _ in agent.calls] == ["literature"]


def test_unique_identifiers_do_not_displace_shared_scientific_query_terms(vault):
    from research_vault.processing import Processor

    config = vault[-1].config
    config.data["processing"] = {"candidate_terms": 1, "candidate_pool": 4}
    processor = Processor(config, vault[3], vault[-1].recipe(), lambda *a, **k: None)
    registry = Registry()
    old, new = "My Notes/old.md", "My Notes/new.md"
    notes = {
        old: "Transport bifurcation with explicit assumptions.",
        new: " ".join(f"identifier{i:05d}" for i in range(100))
        + " Transport bifurcation with explicit assumptions.",
    }
    for name, text in notes.items():
        registry.update(config, name, text, {}, processor)
    requests = list(registry.batches(config, notes, {new}, [], {}, {}))
    assert any(old in payload["notes"] for payload in requests)
