from copy import deepcopy

import pytest
from conftest import decide, link
from test_pipeline import add_paper, proposals

from research_vault.consolidations import append_link
from research_vault.notes import new_note, read_notes
from research_vault.reviews import link_proposal, read_reviews
from research_vault.storage import VaultError, frontmatter, markdown


def pending_link(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    candidate = link()
    candidate["relationship"] = "method"
    candidate["target_concept"] = None
    candidate["evidence"].append(
        {"note": candidate["target"], "quote": "Measured parameter transfer."}
    )
    (root / candidate["target"]).write_text(
        new_note("concept:existing", "concept", "Mechanism", "Measured parameter transfer.")
    )
    agent.links = [candidate]
    pipeline.sync()
    pid = next(iter(proposals(pipeline, "link")))
    return pid, candidate


@pytest.mark.parametrize("decision", ["pending", "approved"])
def test_refresh_closes_existing_typed_relationship_without_applying_or_approving(vault, decision):
    root, _, _, _, pipeline = vault
    pid, candidate = pending_link(vault)
    decide(root, pipeline.state(), pid, decision)
    source = root / candidate["source"]
    source.write_text(
        append_link(
            source.read_text(encoding="utf-8"),
            candidate["target"],
            candidate["relationship"],
            "An existing assertion.",
            "a" * 24,
        ),
        encoding="utf-8",
    )
    scientific_before = {path: text for path, text in read_notes(root).items()}
    payload = deepcopy(pipeline.state()["proposals"][pid]["payload"])
    result = pipeline.refresh_review(pid)
    assert result["outcome"] == "already_represented"
    assert result["path"] == candidate["source"]
    assert not result["applied"] and result["status"] == decision
    state = pipeline.state()
    record = state["proposals"][pid]
    assert record["payload"] == payload
    assert not record["applied"]
    meta = read_reviews(root, state["proposals"])[pid]
    assert meta["status"] == decision and meta["resolved"] == "already_represented"
    assert pid not in {item["id"] for item in pipeline.review_status()["items"]}
    assert pipeline.status()["pending"]["link"] == 0
    assert not pipeline.apply(dry_run=True)["selected_ids"]
    assert read_notes(root) == scientific_before
    before = (root / ".pipeline/state.json").read_bytes()
    assert pipeline.refresh_review(pid)["outcome"] == "already_closed"
    assert (root / ".pipeline/state.json").read_bytes() == before


@pytest.mark.parametrize("existing", ["untyped", "different-type"])
def test_refresh_does_not_close_a_reference_or_a_different_relationship_type(vault, existing):
    root, _, _, _, pipeline = vault
    pid, candidate = pending_link(vault)
    source = root / candidate["source"]
    if existing == "untyped":
        source.write_text(
            source.read_text(encoding="utf-8") + "\nA reference: [[Concepts/mechanism]].\n",
            encoding="utf-8",
        )
    else:
        source.write_text(
            append_link(
                source.read_text(encoding="utf-8"),
                candidate["target"],
                "assumption",
                "A separate assumption.",
                "a" * 24,
            ),
            encoding="utf-8",
        )
    result = pipeline.refresh_review(pid)
    assert result["status"] == "pending" and "replacement" in result
    assert "resolution" not in pipeline.state()["proposals"][pid]
    decide(root, pipeline.state(), result["replacement"])
    pipeline.apply(proposal_ids=[result["replacement"]])
    assert " — method:" in source.read_text(encoding="utf-8")
    if existing == "different-type":
        assert " — assumption:" in source.read_text(encoding="utf-8")


def test_removing_an_existing_edge_allows_a_new_pending_proposal_after_closure(vault):
    root, _, _, _, pipeline = vault
    pid, candidate = pending_link(vault)
    source = root / candidate["source"]
    before = source.read_text(encoding="utf-8")
    source.write_text(
        append_link(
            before,
            candidate["target"],
            candidate["relationship"],
            "An existing assertion.",
            "a" * 24,
        ),
        encoding="utf-8",
    )
    pipeline.refresh_review(pid)
    source.write_text(before, encoding="utf-8")
    state, changes = pipeline.state(), {}
    replacement = link_proposal(candidate, state["proposals"], read_notes(root), changes)
    assert replacement and replacement != pid
    pipeline.baseline(pipeline.state())
    pipeline.save(state, changes)
    assert read_reviews(root, pipeline.state()["proposals"])[replacement]["status"] == "pending"
    assert pipeline.state()["proposals"][pid]["resolution"]["code"] == "already_represented"


def test_resolution_marker_cannot_be_edited_to_hide_a_registered_review(vault):
    root, _, _, _, pipeline = vault
    pid, _ = pending_link(vault)
    path = root / pipeline.state()["proposals"][pid]["path"]
    meta, body = frontmatter(path.read_text(encoding="utf-8"))
    meta["resolved"] = "already_represented"
    path.write_text(markdown(meta, body), encoding="utf-8", newline="\n")
    with pytest.raises(VaultError, match="payload was changed"):
        pipeline.review_status()


def test_an_alternative_concept_definition_stays_active_for_comparison(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    candidate = link()
    agent.links = [candidate]
    pipeline.sync()
    pid = next(iter(proposals(pipeline, "link")))
    (root / candidate["target"]).write_text(
        new_note("concept:existing", "concept", "Mechanism", "An existing, different definition.")
    )
    source = root / candidate["source"]
    source.write_text(
        append_link(
            source.read_text(encoding="utf-8"),
            candidate["target"],
            candidate["relationship"],
            "An existing assertion.",
            "a" * 24,
        ),
        encoding="utf-8",
    )
    before = (root / ".pipeline/state.json").read_bytes()
    result = pipeline.refresh_review(pid)
    assert result["outcome"] == "concept_comparison_needed"
    assert (root / ".pipeline/state.json").read_bytes() == before
    assert "resolution" not in pipeline.state()["proposals"][pid]
    assert any(
        pid in member["proposals"]
        for group in pipeline.review_status()["concept_candidates"]
        for member in group["members"]
    )
