from __future__ import annotations

import json
from copy import deepcopy

import pytest
from conftest import decide, link, write_pdf
from test_pipeline import add_paper, proposals
from test_zotero import Transport, item

from research_vault.agents import AuthenticationError, fits
from research_vault.pipeline import Pipeline
from research_vault.processing import Processor
from research_vault.storage import VaultError
from research_vault.zotero import Zotero


def processor(vault, **settings):
    config = vault[-1].config
    config.data["codex"] = {"max_input_chars": 24000, "output_reserve_chars": 2000}
    config.data["processing"] = {"chunk_chars": 4000, **settings}
    return Processor(config, vault[3], vault[-1].recipe(), lambda *a, **k: None)


def test_oversized_individual_document_covers_every_character_and_caches(vault):
    p = processor(vault)
    text = "Mathematical result α: E=mc²; assumption β; limitation γ.\n" * 1500
    pages = [{"source": "PDF00001", "locator": "page 1", "text": text}]
    payload = {"item_key": "PAPER001", "pages": pages}
    assert not fits(p.config, "literature", payload)
    result = p.summarize("literature", payload)
    calls = [payload for role, payload in vault[3].calls if role == "literature"]
    chunks = [call for call in calls if call.get("processing_stage", "").startswith("chunk:")]
    assert "".join(r["text"] for call in chunks for r in call["pages"]) == text
    assert all(fits(p.config, "literature", call) for call in calls)
    assert all(r["locator"] == "page 1" for call in chunks for r in call["pages"])
    assert "all chunks retained" in result["summary"]
    before = len(vault[3].calls)
    assert p.summarize("literature", payload) == result
    assert len(vault[3].calls) == before
    assert p.hits


def test_chunk_interruption_reuses_completed_operations(vault):
    p = processor(vault)
    pages = [{"source": "PDF00001", "locator": "page 1", "text": "Scientific content. " * 500}]
    original = vault[3].run
    calls = 0

    def interrupt(role, payload):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt()
        return original(role, payload)

    vault[3].run = interrupt
    with pytest.raises(KeyboardInterrupt):
        p.summarize("literature", {"pages": pages})
    first = deepcopy(vault[3].calls[0])
    vault[3].run = original
    p.summarize("literature", {"pages": pages})
    assert vault[3].calls.count(first) == 1
    assert p.hits >= 1


def test_cache_invalidation_for_text_instructions_and_settings(vault):
    p = processor(vault)
    payload = {"pages": [{"source": "PDF00001", "locator": "page 1", "text": "Scientific text."}]}
    p.summarize("literature", payload)
    p.summarize("literature", payload)
    assert p.calls == 1
    payload["pages"][0]["text"] += " Changed."
    p.summarize("literature", payload)
    assert p.calls == 2
    root = vault[0]
    with (root / "agents/literature.md").open("a") as stream:
        stream.write("\nPreserve explicit assumptions.\n")
    newer = Processor(p.config, vault[3], vault[-1].recipe(), lambda *a, **k: None)
    newer.summarize("literature", payload)
    assert newer.calls == 1
    p.config.data["processing"]["chunk_chars"] = 5000
    assert vault[-1].recipe() != newer.recipe


def test_hundreds_of_papers_exceed_collection_limit_without_large_requests(vault):
    root, source, z, agent, pipeline = vault
    pipeline.config.data["codex"] = {"max_input_chars": 30000, "output_reserve_chars": 2000}
    pipeline.config.data["processing"] = {"graph_neighbors": 2, "graph_fragment_chars": 1800}
    document = write_pdf(source / "shared.pdf")
    for number in range(200):
        z.paper(
            f"P{number:07d}",
            [(f"A{number:07d}", document, "main")],
            abstractNote="Synthetic metadata. " * 120,
        )
    result = pipeline.sync()
    assert result["complete"]
    assert len(pipeline.state()["papers"]) == 200
    assert sum(len(p.read_text()) for p in (root / "Literature").glob("*.md")) > 500000
    assert all(fits(pipeline.config, role, payload) for role, payload in agent.calls)
    before = len(agent.calls)
    pipeline.sync()
    assert len(agent.calls) == before


def test_new_document_retrieves_old_and_original_passages(vault):
    root, source, z, agent, pipeline = vault
    add_paper(vault)
    pipeline.sync()
    old = (root / "Literature/PAPER001.md").read_bytes()
    before = len(agent.calls)
    path = write_pdf(source / "new.pdf", "Synthetic scientific evidence about a shared mechanism.")
    z.paper("PAPER002", [("PDF00002", path, "main")])
    pipeline.sync()
    assert (root / "Literature/PAPER001.md").read_bytes() == old
    recent = agent.calls[before:]
    assert sum(role == "literature" for role, _ in recent) == 1
    graph = [p for r, p in recent if r == "linking"]
    assert any(
        {"Literature/PAPER001.md", "Literature/PAPER002.md"} <= p["notes"].keys() for p in graph
    )
    assert any(r["source"] == "PDF00001" for p in graph for r in p["source_passages"])


def test_failed_paper_does_not_lose_successful_paper_or_approval(vault):
    root, source, z, agent, pipeline = vault
    add_paper(vault)
    agent.links = [link()]
    pipeline.sync()
    pid = next(iter(proposals(pipeline, "link")))
    decide(root, pipeline.state(), pid)
    review = root / pipeline.state()["proposals"][pid]["path"]
    approval = review.read_bytes()
    original = (root / "Literature/PAPER001.md").read_bytes()
    path = write_pdf(source / "new.pdf")
    z.paper("PAPER002", [("PDF00002", path, "main")])
    agent.links = []
    agent.fail_role = "literature"
    result = pipeline.sync()
    assert not result["complete"]
    assert pipeline.state()["papers"]["PAPER002"]["status"] == "failed"
    assert pipeline.state()["papers"]["PAPER001"]["status"] == "successful"
    assert review.read_bytes() == approval
    assert (root / "Literature/PAPER001.md").read_bytes() == original
    agent.fail_role = None
    assert pipeline.sync()["complete"]


def test_interruption_after_first_document_resumes_only_pending(vault):
    root, source, z, agent, pipeline = vault
    add_paper(vault)
    path = write_pdf(source / "second.pdf")
    z.paper("PAPER002", [("PDF00002", path, "main")])
    original = agent.run

    def interrupted(role, payload):
        if payload.get("item_key") == "PAPER002":
            raise KeyboardInterrupt()
        return original(role, payload)

    agent.run = interrupted
    with pytest.raises(KeyboardInterrupt):
        pipeline.sync()
    assert pipeline.state()["papers"]["PAPER001"]["status"] == "successful"
    assert pipeline.state()["papers"]["PAPER002"]["status"] == "pending"
    assert pipeline.status()["reasoning_pending"]
    agent.run = original
    assert pipeline.sync()["complete"]
    assert sum(r == "literature" and p["item_key"] == "PAPER001" for r, p in agent.calls) == 1


def test_attachment_inaccessibility_retains_note_and_never_removes_member(vault):
    root, _, z, _, pipeline = vault
    add_paper(vault)
    pipeline.sync()
    original = (root / "Literature/PAPER001.md").read_bytes()
    saved = deepcopy(z.items["PAPER001"])
    z.items["PAPER001"]["attachments"][0]["error"] = "temporarily inaccessible OneDrive file"
    z.items["PAPER001"]["fingerprint"] = "inaccessible"
    result = pipeline.sync()
    assert not result["complete"] and "PAPER001" in result["skipped"]
    assert (root / "Literature/PAPER001.md").read_bytes() == original
    assert not proposals(pipeline, "removal")
    assert pipeline.state()["membership"] == ["PAPER001"]
    z.items["PAPER001"] = saved
    assert pipeline.sync()["complete"]


@pytest.mark.parametrize("mode", ["imported_file", "linked_file"])
def test_real_adapter_recovers_from_unavailable_managed_and_linked_files(vault, mode):
    root, source, _, agent, pipeline = vault
    path = source / "café paper.pdf"
    parent = item("PAPER001")
    transport = Transport(
        {
            "collections/COLLECT1": {"key": "COLLECT1"},
            "collections/COLLECT1/items": [parent],
            "items/PAPER001/children": [
                item("PDF00001", "attachment", contentType="application/pdf", linkMode=mode)
            ],
            "items/PDF00001/file/view/url": path.as_uri(),
            "items/PDF00001/children": [],
        }
    )
    client = Zotero(pipeline.config)
    client.opener = transport
    pipeline = Pipeline(pipeline.config, client, agent)
    assert pipeline.sync()["skipped"]
    diagnostic = client.attachment_diagnostics[0]
    assert diagnostic["original_url"] == path.as_uri()
    assert diagnostic["resolved_path"] == str(path)
    write_pdf(path)
    assert pipeline.sync()["complete"]
    assert (root / "Literature/PAPER001.md").exists()


def test_invalid_evidence_is_not_cached_as_success(vault):
    p = processor(vault)
    original = vault[3].run

    def invalid(role, payload):
        result = original(role, payload)
        result["evidence"][0]["quote"] = "Fabricated evidence."
        return result

    vault[3].run = invalid
    payload = {"pages": [{"source": "PDF00001", "locator": "page 1", "text": "Real evidence."}]}
    with pytest.raises(VaultError, match="absent"):
        p.summarize("literature", payload)
    vault[3].run = original
    p.summarize("literature", payload)
    assert p.calls == 2


def test_authentication_failure_is_fatal(vault):
    add_paper(vault)
    agent = vault[3]

    def unavailable():
        raise AuthenticationError("Synthetic invalid authentication")

    agent.authenticate = unavailable
    with pytest.raises(AuthenticationError):
        vault[-1].sync()
    assert not agent.calls
    assert vault[-1].state()["membership"] == []


def test_dry_run_diagnoses_unreadable_pdf_without_writes(vault):
    root, source, z, agent, pipeline = vault
    path = source / "broken.pdf"
    path.write_text("not a PDF")
    z.paper("PAPER001", [("PDF00001", path, "main")])
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    plan = pipeline.sync(dry_run=True)
    assert "Unreadable PDF" in plan["skipped"]["PAPER001"]
    assert not plan["processable"]
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert not agent.calls


def test_failed_document_does_not_block_later_new_document(vault):
    root, source, z, agent, pipeline = vault
    add_paper(vault)
    path = write_pdf(source / "later.pdf")
    z.paper("PAPER002", [("PDF00002", path, "main")])
    original = agent.run

    def fail_first(role, payload):
        if payload.get("item_key") == "PAPER001":
            raise VaultError("Individual model failure")
        return original(role, payload)

    agent.run = fail_first
    assert not pipeline.sync()["complete"]
    assert not (root / "Literature/PAPER001.md").exists()
    assert (root / "Literature/PAPER002.md").exists()
    assert pipeline.status()["documents"]["papers"]["failed"] == ["PAPER001"]
    assert pipeline.status()["documents"]["papers"]["successful"] == ["PAPER002"]
    agent.run = original
    assert pipeline.sync()["complete"]
    assert sum(r == "literature" and p["item_key"] == "PAPER002" for r, p in agent.calls) == 1


def test_transaction_failure_rolls_back_only_current_document(vault, monkeypatch):
    import research_vault.storage as storage

    root, source, z, agent, pipeline = vault
    add_paper(vault)
    path = write_pdf(source / "second.pdf")
    z.paper("PAPER002", [("PDF00002", path, "main")])
    atomic = storage.atomic
    failed = False

    def fail_second(path, content):
        nonlocal failed
        if path.name == "PAPER002.md" and not failed:
            failed = True
            raise OSError("Synthetic transaction I/O failure")
        return atomic(path, content)

    monkeypatch.setattr(storage, "atomic", fail_second)
    with pytest.raises(VaultError, match="transaction I/O failure"):
        pipeline.sync()
    assert (root / "Literature/PAPER001.md").exists()
    assert not (root / "Literature/PAPER002.md").exists()
    assert pipeline.state()["papers"]["PAPER001"]["status"] == "successful"
    assert pipeline.state()["papers"]["PAPER002"]["status"] == "pending"
    assert not pipeline.status()["transaction_requires_recovery"]
    assert pipeline.sync()["complete"]
    assert sum(r == "literature" for r, _ in agent.calls) == 2  # Second summary reused from cache.


def test_interrupted_reasoning_preserves_pending_proposals_and_cache(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    agent.links = [link()]
    original = agent.run

    def interrupted(role, payload):
        if role == "synthesis":
            raise KeyboardInterrupt()
        return original(role, payload)

    agent.run = interrupted
    with pytest.raises(KeyboardInterrupt):
        pipeline.sync()
    pid = next(iter(proposals(pipeline, "link")))
    review = root / pipeline.state()["proposals"][pid]["path"]
    before = review.read_bytes()
    assert pipeline.status()["reasoning_pending"]
    agent.run = original
    assert pipeline.sync()["complete"]
    assert review.read_bytes() == before
    assert sum(r == "linking" for r, _ in agent.calls) == 1
    assert sum(r == "literature" for r, _ in agent.calls) == 1


def test_doctor_reports_warnings_separately_from_auth_and_api_blockers(vault, monkeypatch):
    import subprocess

    _, _, z, agent, pipeline = vault
    z.paper("PAPER001")
    agent.command_prefix = lambda: ["mock-codex"]
    agent.authenticate = lambda: None
    monkeypatch.setattr(
        "research_vault.pipeline.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(
            a, 0, "--ignore-user-config --ignore-rules --ephemeral --output-schema", ""
        ),
    )
    result = pipeline.doctor()
    assert not result["blocking_errors"]
    assert any("no processable PDF" in message for message in result["warnings"])
    assert result["environment"] in {"Linux", "Windows", "WSL", "Darwin"}
    assert result["pymupdf"] == "ok"
    assert result["codex_auth"] == "ok"
    z.fail = True
    assert pipeline.doctor()["blocking_errors"]


def test_old_state_records_migrate_without_removing_decisions(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    agent.links = [link()]
    pipeline.sync()
    state = pipeline.state()
    pid = next(iter(proposals(pipeline, "link")))
    decide(root, state, pid, "rejected")
    review = root / state["proposals"][pid]["path"]
    before = review.read_bytes()
    state["papers"]["PAPER001"].pop("status")
    for field in ("graph_notes", "graph_recipe", "graph_membership", "last_attempt", "graph_error"):
        state.pop(field, None)
    (root / ".pipeline/state.json").write_text(json.dumps(state))
    pipeline.sync()
    assert pipeline.state()["schema"] == 1
    assert review.read_bytes() == before
    assert pipeline.status()["documents"]["papers"]["successful"] == ["PAPER001"]


def test_large_personal_note_all_fragments_remain_within_graph_budget(vault):
    from research_vault.processing import graph_batches

    config = vault[-1].config
    config.data["codex"] = {"max_input_chars": 16000, "output_reserve_chars": 2000}
    config.data["processing"] = {"graph_neighbors": 1, "graph_fragment_chars": 4000}
    text = "".join(
        f"Mathematical hypothesis α {i} with assumptions and limitations.\n" for i in range(1000)
    )
    name = "My Notes/large.md"
    requests = list(graph_batches(config, {name: text}, {name}, [], {}))
    assert "".join(p["notes"][name] for p in requests) == text
    assert all(
        fits(config, role, payload)
        for payload in requests
        for role in ("linking", "synthesis", "maintenance")
    )


def test_approval_edited_during_snapshot_is_not_absorbed_into_new_baseline(vault):
    root, _, z, agent, pipeline = vault
    add_paper(vault)
    agent.links = [link()]
    pipeline.sync()
    pid = next(iter(proposals(pipeline, "link")))
    original = z.snapshot

    def snapshot_with_user_decision():
        decide(root, pipeline.state(), pid)
        return original()

    z.snapshot = snapshot_with_user_decision
    with pytest.raises(VaultError, match="File changed during processing"):
        pipeline.sync()
    review = root / pipeline.state()["proposals"][pid]["path"]
    assert "status: approved" in review.read_text()


def test_dry_run_reports_budget_blocker_without_accepting_snapshot(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    pipeline.config.data["codex"] = {"max_input_chars": 1000}
    before = pipeline.state()
    assert pipeline.sync(dry_run=True)["blocking_errors"]
    with pytest.raises(VaultError, match="Invalid processing configuration"):
        pipeline.sync()
    assert pipeline.state() == before
    assert not agent.calls


def test_changed_recipe_reuses_pdf_extraction_but_reprocesses_summary(vault, monkeypatch):
    import research_vault.processing as processing

    root, _, _, agent, pipeline = vault
    add_paper(vault)
    pipeline.sync()
    with (root / "agents/literature.md").open("a") as stream:
        stream.write("\nClarify limitations.\n")
    monkeypatch.setattr(
        processing, "pdf", lambda *a, **k: pytest.fail("unchanged PDF extraction must be reused")
    )
    assert pipeline.sync()["complete"]
    assert sum(r == "literature" for r, _ in agent.calls) == 2


def test_untrusted_metadata_cannot_inject_links_or_abort_other_documents(vault):
    root, source, z, _, pipeline = vault
    path = write_pdf(source / "safe.pdf")
    z.paper("PAPER001", [("PDF00001", path, "main")], title="Untrusted [[Concepts/injected]]")
    z.paper("PAPER002", [("PDF00002", path, "main")])
    result = pipeline.sync()
    assert "contains links" in result["failed"]["PAPER001"]
    assert not (root / "Literature/PAPER001.md").exists()
    assert (root / "Literature/PAPER002.md").exists()
    assert not (root / "Concepts/injected.md").exists()
