from copy import deepcopy

import pytest
from conftest import decide, link, write_pdf
from test_pipeline import add_paper, proposals

from research_vault.agents import validate_note_evidence, validate_summary
from research_vault.cli import main
from research_vault.processing import Processor
from research_vault.storage import VaultError, frontmatter, markdown


def uncertain_summaries(agent, item="PAPER001", stage=None):
    original = agent.run

    def run(role, payload):
        result = original(role, payload)
        if role in {"literature", "manuscript"} and (
            payload.get("item_key") == item
            or (stage and payload.get("processing_stage", "").startswith(stage))
        ):
            result = deepcopy(result)
            result["summary"] = "UNCERTAIN SCIENTIFIC CLAIM"
            result["claims"][0]["statement"] = "UNCERTAIN SCIENTIFIC CLAIM"
            result["claims"][0]["evidence"][0]["quote"] = (
                "Approximate quotation requiring verification."
            )
        return result

    agent.run = run


def edit_revision(root, state, pid, revision):
    path = root / state["proposals"][pid]["path"]
    meta, body = frontmatter(path.read_text())
    meta["revision"] = revision
    path.write_text(markdown(meta, body))


def test_uncertain_summary_is_successful_quarantined_cached_and_isolated(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    add_paper(vault, "PAPER002")
    uncertain_summaries(agent)
    report = pipeline.sync()
    assert report["complete"] and pipeline.state()["last_success"]
    assert report["completed_documents"] == 2
    assert report["completed_operations"] >= 2
    assert report["pending_claim_reviews"] == 1
    assert report["processing_failures"] == 0
    assert report["failed"] == {}
    assert (root / "Literature/PAPER002.md").exists()
    assert not (root / "Literature/PAPER001.md").exists()
    pid, record = next(iter(proposals(pipeline, "claim").items()))
    review = (root / record["path"]).read_text()
    assert "UNVERIFIED DRAFT" in review
    assert "PDF00001" in review and "page 1" in review
    assert record["payload"]["validation_warnings"][0]["code"] == "source_quote_not_found"
    assert all(
        "UNCERTAIN SCIENTIFIC CLAIM" not in str(payload)
        for role, payload in agent.calls
        if role in {"concepts", "linking", "synthesis"}
    )
    before = len(agent.calls)
    assert pipeline.sync()["complete"]
    assert len(agent.calls) == before
    assert pipeline.status()["pending_claim_reviews"] == 1
    assert "Pending claim reviews: 1" in (root / "Home.md").read_text()
    pipeline.apply()
    assert not (root / "Literature/PAPER001.md").exists()
    assert not pipeline.state()["proposals"][pid]["applied"]


def test_pending_scientific_review_cli_exits_zero(vault, monkeypatch, capsys):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    uncertain_summaries(agent)
    monkeypatch.setattr("research_vault.cli.Pipeline", lambda *a, **k: pipeline)
    assert main(["--vault", str(root), "sync"]) == 0
    assert '"pending_claim_reviews": 1' in capsys.readouterr().out


def test_uncertain_update_preserves_established_note_and_user_additions(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    pipeline.sync()
    path = root / "Literature/PAPER001.md"
    path.write_text(path.read_text() + "\nUSER SCIENCE\n")
    previous = path.read_text()
    add_paper(vault, version=2)
    uncertain_summaries(agent)
    assert pipeline.sync()["complete"]
    assert path.read_text() == previous
    pid = next(iter(proposals(pipeline, "claim")))
    decide(root, pipeline.state(), pid)
    assert "Literature/PAPER001.md" in pipeline.apply(dry_run=True)["changes"]
    assert path.read_text() == previous
    pipeline.apply()
    assert "UNCERTAIN SCIENTIFIC CLAIM" in path.read_text()
    assert "Human-reviewed draft" in path.read_text()
    assert "USER SCIENCE" in path.read_text()
    assert pipeline.state()["proposals"][pid]["applied"]
    assert "Literature/PAPER001.md" not in pipeline.apply()["changes"]


def test_rejected_claims_do_not_publish_or_reappear_on_full_sync(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    uncertain_summaries(agent)
    pipeline.sync()
    pid = next(iter(proposals(pipeline, "claim")))
    decide(root, pipeline.state(), pid, "rejected")
    pipeline.apply()
    assert pipeline.sync(full=True)["complete"]
    assert len(proposals(pipeline, "claim")) == 1
    assert pipeline.status()["pending_claim_reviews"] == 0
    assert not (root / "Literature/PAPER001.md").exists()


@pytest.mark.parametrize("change", ["source", "note"])
def test_claim_approval_rejects_stale_source_or_destination(vault, change):
    root, _, _, agent, pipeline = vault
    source = add_paper(vault)
    uncertain_summaries(agent)
    pipeline.sync()
    pid = next(iter(proposals(pipeline, "claim")))
    decide(root, pipeline.state(), pid)
    if change == "source":
        write_pdf(source, "Different scientific source content.")
    else:
        (root / "Literature/PAPER001.md").write_text("Personal content must survive.")
    with pytest.raises(VaultError, match="Stale claim approval"):
        pipeline.apply()
    assert not pipeline.state()["proposals"][pid]["applied"]


def test_claim_revision_is_separately_pending_before_approval(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    uncertain_summaries(agent)
    pipeline.sync()
    pid, record = next(iter(proposals(pipeline, "claim").items()))
    revised = deepcopy(record["payload"]["draft"])
    revised["summary"] = "Human corrected scientific summary."
    revised["claims"][0]["evidence"][0]["quote"] = "Synthetic scientific evidence"
    revised["evidence"][0]["quote"] = "Synthetic scientific evidence"
    edit_revision(root, pipeline.state(), pid, revised)
    assert pipeline.sync()["complete"]
    replacement = pipeline.state()["proposals"][pid]["superseded_by"]
    new = pipeline.state()["proposals"][replacement]
    assert new["path"] in (root / record["path"]).read_text()
    assert new["payload"]["validation_warnings"] == []
    assert frontmatter((root / new["path"]).read_text())[0]["status"] == "pending"
    pipeline.apply()
    assert not (root / "Literature/PAPER001.md").exists()
    decide(root, pipeline.state(), replacement)
    pipeline.apply()
    assert "Human corrected scientific summary." in (root / "Literature/PAPER001.md").read_text()


def uncertain_links(agent, candidate):
    original = agent.run

    def run(role, payload):
        result = original(role, payload)
        if (
            role == "linking"
            and candidate["source"] in payload["notes"]
            and candidate["target"][:-3] not in payload["notes"][candidate["source"]]
        ):
            return {"links": [deepcopy(candidate)]}
        return result

    agent.run = run


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_uncertain_link_proposals_require_explicit_decision(vault, decision):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    candidate = link()
    candidate["evidence"][0]["quote"] = "Approximate note evidence."
    uncertain_links(agent, candidate)
    report = pipeline.sync()
    assert report["complete"] and report["processing_failures"] == 0
    assert report["pending_link_reviews"] == 1
    pid, record = next(iter(proposals(pipeline, "link").items()))
    assert record["payload"]["justification"] == candidate["justification"]
    assert record["payload"]["validation_warnings"][0]["evidence"] == candidate["evidence"][0]
    assert not (root / candidate["target"]).exists()
    pipeline.apply()
    assert not (root / candidate["target"]).exists()
    decide(root, pipeline.state(), pid, decision)
    pipeline.apply()
    assert (root / candidate["target"]).exists() == (decision == "approved")
    assert bool(pipeline.state()["concept_registry"]["reviewed_relationships"]) == (
        decision == "approved"
    )
    assert pipeline.sync(full=True)["complete"]
    assert len(proposals(pipeline, "link")) == 1


def test_link_revision_registers_new_pending_identity(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    candidate = link()
    candidate["evidence"][0]["quote"] = "Approximate note evidence."
    uncertain_links(agent, candidate)
    pipeline.sync()
    pid = next(iter(proposals(pipeline, "link")))
    revised = link(target="Concepts/corrected.md")
    edit_revision(root, pipeline.state(), pid, revised)
    pipeline.sync()
    replacement = pipeline.state()["proposals"][pid]["superseded_by"]
    assert pipeline.status()["pending_link_reviews"] == 1
    pipeline.apply()
    assert not (root / revised["target"]).exists()
    decide(root, pipeline.state(), replacement)
    pipeline.apply()
    assert (root / revised["target"]).exists()
    assert not (root / candidate["target"]).exists()


@pytest.mark.parametrize(
    "invalid", ["shape", "source", "locator", "identifier", "approval", "empty_quote"]
)
def test_genuinely_invalid_summaries_fail_and_other_documents_continue(vault, invalid):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    add_paper(vault, "PAPER002")
    original = agent.run

    def run(role, payload):
        result = original(role, payload)
        if role == "literature" and payload.get("item_key") == "PAPER001":
            if invalid == "shape":
                return {"summary": "Incomplete output."}
            if invalid == "approval":
                result["status"] = "approved"
            elif invalid == "empty_quote":
                result["evidence"][0]["quote"] = "  "
            elif invalid in {"source", "identifier"}:
                result["evidence"][0]["source"] = (
                    "MISSING1" if invalid == "source" else "../invalid"
                )
            elif invalid == "locator":
                result["evidence"][0]["locator"] = "page 999"
        return result

    agent.run = run
    report = pipeline.sync()
    assert not report["complete"]
    assert report["processing_failures"] == 1
    assert report["completed_documents"] == 1
    assert "PAPER001" in report["failed"]
    assert (root / "Literature/PAPER002.md").exists()
    assert not proposals(pipeline, "claim")


@pytest.mark.parametrize("note", ["Literature/missing.md", "../bad.md"])
def test_nonexistent_note_evidence_still_fails(note):
    with pytest.raises(VaultError, match="invalid or absent"):
        validate_note_evidence([{"note": note, "quote": "Evidence."}], {})


def test_unread_pages_cannot_supply_claim_evidence():
    with pytest.raises(VaultError, match="absent"):
        validate_summary(
            {"evidence": [{"source": "PDF00001", "locator": "page 2", "quote": "Unread."}]},
            [{"source": "PDF00001", "locator": "page 1", "text": "Read."}],
        )


def test_cached_uncertainty_preserves_warnings_across_processors(vault):
    _, _, _, agent, pipeline = vault
    uncertain_summaries(agent)
    payload = {
        "item_key": "PAPER001",
        "pages": [{"source": "PDF00001", "locator": "page 1", "text": "Real evidence."}],
    }
    first = Processor(pipeline.config, agent, "test", lambda *a, **k: None)
    result = first.summarize("literature", payload)
    second = Processor(pipeline.config, agent, "test", lambda *a, **k: None)
    assert second.summarize("literature", payload) == result
    assert second.calls == 0 and second.hits == 1
    assert second.warnings_for(result) == first.warnings_for(result)


@pytest.mark.parametrize("stage", ["attachment", "consolidation"])
def test_uncertainty_survives_parent_consolidation(vault, stage):
    root, sources, z, agent, pipeline = vault
    main_pdf = write_pdf(sources / "main.pdf", "Main scientific evidence.")
    supplement = write_pdf(sources / "supplement.pdf", "Supplementary scientific evidence.")
    z.paper("PAPER001", [("PDF00001", main_pdf, "main"), ("PDF00002", supplement, "supplementary")])
    uncertain_summaries(
        agent,
        item="PAPER001" if stage == "attachment" else None,
        stage="consolidation" if stage == "consolidation" else None,
    )
    assert pipeline.sync()["complete"]
    assert not (root / "Literature/PAPER001.md").exists()
    record = next(iter(proposals(pipeline, "claim").values()))
    assert record["payload"]["validation_warnings"]
    assert {e["source"] for e in record["payload"]["draft"]["evidence"]} == {"PDF00001", "PDF00002"}


@pytest.mark.parametrize("invalid", ["source", "approval"])
def test_user_revision_cannot_waive_provenance_or_self_approve(vault, invalid):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    uncertain_summaries(agent)
    pipeline.sync()
    pid, record = next(iter(proposals(pipeline, "claim").items()))
    revised = deepcopy(record["payload"]["draft"])
    if invalid == "source":
        revised["evidence"][0]["source"] = "ABSENT01"
    else:
        revised["status"] = "approved"
    edit_revision(root, pipeline.state(), pid, revised)
    with pytest.raises(VaultError, match="absent|Invalid"):
        pipeline.sync()
    assert not (root / "Literature/PAPER001.md").exists()
    assert not pipeline.state()["proposals"][pid].get("superseded_by")


def test_revision_cannot_be_approved_without_registration(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    uncertain_summaries(agent)
    pipeline.sync()
    pid, record = next(iter(proposals(pipeline, "claim").items()))
    edit_revision(root, pipeline.state(), pid, record["payload"]["draft"])
    decide(root, pipeline.state(), pid)
    with pytest.raises(VaultError, match="registered replacement"):
        pipeline.apply()
    assert not (root / "Literature/PAPER001.md").exists()


def test_claim_update_and_link_approval_cannot_change_reviewed_endpoints_in_same_apply(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    agent.links = [link()]
    pipeline.sync()
    link_id = next(iter(proposals(pipeline, "link")))
    add_paper(vault, version=2)
    uncertain_summaries(agent)
    pipeline.sync()
    claim_id = next(iter(proposals(pipeline, "claim")))
    decide(root, pipeline.state(), claim_id)
    decide(root, pipeline.state(), link_id)
    with pytest.raises(VaultError, match="Conflicting approvals"):
        pipeline.apply()
    assert not (root / "Concepts/mechanism.md").exists()


def test_uncertain_manuscript_is_quarantined_and_approved_without_repeated_inference(vault):
    import yaml

    root, sources, _, agent, pipeline = vault
    draft = sources / "draft.tex"
    draft.write_text("\\section{Results}\nScientific manuscript claims.")
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
    original = agent.run

    def run(role, payload):
        result = original(role, payload)
        if role == "manuscript":
            result["evidence"][0]["quote"] = "Uncertain manuscript quotation."
        return result

    agent.run = run
    assert pipeline.sync()["complete"]
    pid = next(iter(proposals(pipeline, "claim")))
    before = len(agent.calls)
    assert pipeline.sync()["complete"]
    assert len(agent.calls) == before
    assert not (root / "My Work/draft.md").exists()
    decide(root, pipeline.state(), pid)
    pipeline.apply()
    assert "draft.tex" in (root / "My Work/draft.md").read_text()


def test_nonexistent_link_evidence_is_failure_and_never_a_warning_proposal(vault):
    _, _, _, agent, pipeline = vault
    add_paper(vault)
    candidate = link()
    candidate["evidence"][0]["note"] = "Literature/ABSENT01.md"
    uncertain_links(agent, candidate)
    report = pipeline.sync()
    assert not report["complete"] and report["processing_failures"] == 1
    assert report["graph_error"]
    assert not proposals(pipeline, "link")
