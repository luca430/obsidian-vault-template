from copy import deepcopy

import pytest
from conftest import decide, link
from test_pipeline import add_paper, proposals

from research_vault.consolidations import register_consolidation
from research_vault.notes import new_note, read_notes
from research_vault.review_state import candidate_groups, dependency_text
from research_vault.storage import VaultError, frontmatter


def snapshot(root):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def two_links(vault, same_target=False):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    first = link()
    first["justification"] = "Mechanistic relation, contrasted with — method: parameter estimation."
    second = link(target=first["target"] if same_target else "Concepts/other.md")
    if not same_target:
        second["target_concept"]["title"] = "Independent scientific construct"
    second["relationship"] = "method"
    agent.links = [first, second]
    pipeline.sync()
    ids = list(proposals(pipeline, "link"))
    assert len(ids) == 2
    for pid in ids:
        decide(root, pipeline.state(), pid)
    return ids


@pytest.mark.parametrize("same_target", [False, True])
def test_applying_another_link_preserves_other_approval_and_relationship_type(vault, same_target):
    root, _, _, _, pipeline = vault
    first, second = two_links(vault, same_target)
    pipeline.apply(proposal_ids=[first])
    report = pipeline.review_status()
    assert second in report["ready_ids"]
    preview = pipeline.apply(dry_run=True, proposal_ids=[second])
    pipeline.apply(proposal_ids=[second], plan_token=preview["plan_token"])
    text = (root / "Literature/Synthetic item — PAPER001.md").read_text(encoding="utf-8")
    assert " — method:" in text and " — mechanism:" in text


def test_scientific_change_shows_diff_and_refresh_creates_pending_without_inference(vault):
    root, _, _, agent, pipeline = vault
    pid, _ = two_links(vault)
    path = root / "Literature/Synthetic item — PAPER001.md"
    path.write_text(path.read_text() + "\nScientific limitation: only condition A was measured.\n")
    before = snapshot(root)
    item = next(i for i in pipeline.review_status()["items"] if i["id"] == pid)
    assert item["state"] == "needs-revalidation"
    assert "+Scientific limitation" in item["differences"][0]["diff"]
    assert snapshot(root) == before
    calls = deepcopy(agent.calls)
    refreshed = pipeline.refresh_review(pid)
    assert agent.calls == calls
    assert refreshed["status"] == "pending"
    assert refreshed["replacement"] != pid
    assert pipeline.state()["proposals"][pid]["superseded_by"] == refreshed["replacement"]
    assert frontmatter((root / refreshed["path"]).read_text())[0]["status"] == "pending"
    assert refreshed["replacement"] not in pipeline.review_status()["ready_ids"]
    decide(root, pipeline.state(), refreshed["replacement"])
    pipeline.apply(proposal_ids=[refreshed["replacement"]])
    assert "only condition A" in path.read_text()


def test_legacy_stale_review_is_not_silently_rebased(vault):
    root, _, _, _, pipeline = vault
    pid, _ = two_links(vault)
    import json

    state = pipeline.state()
    del state["proposals"][pid]["expected_notes"]
    (root / ".pipeline/state.json").write_text(json.dumps(state))
    source = root / "Literature/Synthetic item — PAPER001.md"
    source.write_text(source.read_text() + "\nChanged scientific conclusion.\n")
    report = pipeline.review_status()
    item = next(i for i in report["items"] if i["id"] == pid)
    assert item["state"] == "needs-revalidation"
    assert item["differences"][0]["diff"] is None
    assert "historical snapshot" in item["differences"][0]["reason"]


def test_yaml_presentation_and_display_settings_do_not_invalidate_science():
    original = new_note("paper:1", "literature", "Title", "Equation: $x  + y$.")
    meta, body = frontmatter(original)
    from research_vault.storage import markdown

    meta["cssclasses"] = ["wide"]
    changed = markdown(dict(reversed(list(meta.items()))), body)
    assert dependency_text(original) == dependency_text(changed)
    assert dependency_text(original) != dependency_text(changed.replace("x  + y", "x - y"))
    assert dependency_text(original) != dependency_text(
        original.replace("Write here.", "New hypothesis.")
    )
    assert dependency_text(original) != dependency_text(
        original.replace(
            "<!-- links:start -->", "<!-- links:start -->\nUnrecognized scientific content."
        )
    )


def test_selected_apply_does_not_absorb_a_new_approval(vault):
    root, _, _, _, pipeline = vault
    first, second = two_links(vault)
    decide(root, pipeline.state(), second, "pending")
    preview = pipeline.apply(dry_run=True, proposal_ids=[first])
    decide(root, pipeline.state(), second)
    pipeline.apply(proposal_ids=[first], plan_token=preview["plan_token"])
    assert pipeline.state()["proposals"][first]["applied"]
    assert not pipeline.state()["proposals"][second]["applied"]


def test_changed_preview_requires_another_preview_even_when_science_is_unchanged(vault):
    _, _, _, _, pipeline = vault
    first, second = two_links(vault)
    preview = pipeline.apply(dry_run=True, proposal_ids=[first])
    pipeline.apply(proposal_ids=[second])
    with pytest.raises(VaultError, match="preview changed"):
        pipeline.apply(proposal_ids=[first], plan_token=preview["plan_token"])


def test_conflicting_concepts_do_not_prevent_explicit_independent_apply(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    first, second, independent = link(), link(), link(target="Concepts/independent.md")
    independent["target_concept"]["title"] = "Independent scientific construct"
    second["relationship"] = "method"
    second["target_concept"]["body"] = "A different definition."
    agent.links = [first, second, independent]
    pipeline.sync()
    for pid in proposals(pipeline, "link"):
        decide(root, pipeline.state(), pid)
    before = snapshot(root)
    preview = pipeline.apply(dry_run=True)
    assert len(preview["readiness"]["blocked"]) == 2
    assert len(preview["readiness"]["ready_ids"]) == 1
    assert snapshot(root) == before
    with pytest.raises(VaultError, match="Conflicting definitions"):
        pipeline.apply()
    result = pipeline.apply(ready_only=True)
    assert len(result["selected_ids"]) == 1
    assert len(result["skipped"]) == 2
    assert not (root / "Concepts/mechanism.md").exists()
    assert (root / "Concepts/independent.md").exists()


def prepare_merge(vault, operation="merge"):
    root, _, _, _, pipeline = vault
    a, b = "Concepts/Feedback control.md", "Concepts/Feedback controls.md"
    (root / a).write_text(
        new_note("concept:stable-a", "concept", "Feedback control", "A measured loop with delay.")
    )
    (root / b).write_text(
        new_note(
            "concept:stable-b", "concept", "Feedback controls", "A loop limited to condition B."
        )
        + "\nPersonal qualification remains.\n"
    )
    source = "Literature/Synthetic item — PAPER001.md"
    (root / source).write_text(
        new_note("zotero:PAPER001", "literature", "Synthetic", "Synthetic scientific evidence.")
    )
    from research_vault.consolidations import append_link

    (root / source).write_text(
        append_link(
            (root / source).read_text(encoding="utf-8"), b, "method", "Measured under B.", "a" * 24
        ),
        encoding="utf-8",
    )
    personal = root / "My Notes/idea.md"
    personal.write_text("A personal hypothesis refers to [[Concepts/Feedback controls]].")
    state = pipeline.state()
    notes = read_notes(root)
    groups = candidate_groups(notes, state["proposals"], {})
    group = next(g for g in groups if {m["path"] for m in g["members"]} == {a, b})
    evidence = [
        {"note": a, "quote": "A measured loop with delay."},
        {"note": b, "quote": "A loop limited to condition B."},
    ]
    suggestion = {
        "operation": operation,
        "canonical": {
            "path": a,
            "title": "Feedback control",
            "body": "A measured loop with delay. A loop limited to condition B. Scope remains conditional.",
            "aliases": ["Feedback controls"],
        }
        if operation == "merge"
        else None,
        "explanation": "Synthetic consolidation for review.",
        "distinctions": "Condition B remains explicit.",
        "evidence": evidence,
        "links": [],
    }
    if operation == "relate":
        relation = link(source=a, target=b)
        relation.update(relationship="special-case", target_concept=None, evidence=evidence)
        suggestion["links"] = [relation]
    changes = {}
    pid = register_consolidation(suggestion, group, state, notes, changes)
    pipeline.baseline(pipeline.state())
    pipeline.save(state, changes)
    return a, b, source, pid


def test_merge_preview_preserves_history_and_redirects_with_exact_link_impact(vault):
    root, _, _, _, pipeline = vault
    a, b, source, pid = prepare_merge(vault)
    before = snapshot(root)
    assert not pipeline.review_status()["ready_ids"]
    assert snapshot(root) == before
    decide(root, pipeline.state(), pid)
    preview = pipeline.apply(dry_run=True, proposal_ids=[pid])
    assert a in preview["diffs"] and source in preview["diffs"]
    assert "Feedback controls" in preview["diffs"][source]
    before = snapshot(root)
    assert pipeline.apply(dry_run=True, proposal_ids=[pid])["plan_token"] == preview["plan_token"]
    assert snapshot(root) == before
    personal = (root / "My Notes/idea.md").read_bytes()
    pipeline.apply(proposal_ids=[pid], plan_token=preview["plan_token"])
    meta, _ = frontmatter((root / a).read_text())
    assert meta["id"] == "concept:stable-a"
    assert "Feedback controls" in meta["aliases"]
    meta, _ = frontmatter((root / b).read_text())
    assert meta["kind"] == "concept-redirect" and meta["redirect_target"] == a
    assert "A loop limited to condition B." in (root / b).read_text()
    assert "Personal qualification remains." in (root / b).read_text()
    assert "[[Concepts/Feedback control]]" in (root / source).read_text()
    assert (root / "My Notes/idea.md").read_bytes() == personal
    assert not pipeline.review_status()["concept_candidates"]


@pytest.mark.parametrize("operation", ["keep-separate", "relate"])
def test_separate_or_related_concepts_keep_their_definitions(vault, operation):
    root, _, _, _, pipeline = vault
    a, b, _, pid = prepare_merge(vault, operation)
    before = {p: dependency_text((root / p).read_text(encoding="utf-8")) for p in [a, b]}
    decide(root, pipeline.state(), pid)
    pipeline.apply(proposal_ids=[pid])
    assert all(dependency_text((root / p).read_text(encoding="utf-8")) == before[p] for p in [a, b])
    assert not pipeline.review_status()["concept_candidates"]
    if operation == "relate":
        assert " — special-case:" in (root / a).read_text(encoding="utf-8")


def test_changed_merge_member_blocks_old_merge_and_exposes_diff(vault):
    root, _, _, _, pipeline = vault
    a, _, _, pid = prepare_merge(vault)
    decide(root, pipeline.state(), pid)
    path = root / a
    path.write_text(path.read_text() + "\nNew uncertainty changes scope.\n")
    preview = pipeline.apply(dry_run=True, proposal_ids=[pid])
    assert not preview["can_apply"]
    assert "+New uncertainty" in preview["readiness"]["blocked"][0]["differences"][0]["diff"]
    with pytest.raises(VaultError, match="Stale approval"):
        pipeline.apply(proposal_ids=[pid])


def test_suggested_merge_is_pending_and_preserves_both_relationship_types(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    first, second = link(), link()
    second["relationship"] = "assumption"
    second["target_concept"]["body"] = "A second description with an explicit assumption."
    agent.links = [first, second]
    pipeline.sync()
    for pid in proposals(pipeline, "link"):
        decide(root, pipeline.state(), pid)
    group = next(
        g for g in pipeline.review_status()["concept_candidates"] if len(g["members"]) == 1
    )
    original_run = agent.run

    def run(role, payload):
        if role != "consolidation" or "consolidation_request" not in payload:
            return original_run(role, payload)
        evidence = [{"note": first["source"], "quote": "Synthetic scientific evidence."}]
        links = []
        for item in [first, second]:
            item = deepcopy(item)
            item["target_concept"] = None
            item["evidence"] += [
                {"note": item["target"], "quote": "Combined conditional definition."}
            ]
            links.append(item)
        return {
            "consolidations": [
                {
                    "operation": "merge",
                    "canonical": {
                        "path": first["target"],
                        "title": "Mechanism",
                        "body": "Combined conditional definition.",
                        "aliases": [],
                    },
                    "explanation": "Combine complementary descriptions.",
                    "distinctions": "The assumption remains conditional.",
                    "evidence": evidence,
                    "links": links,
                }
            ],
        }

    agent.run = run
    response = pipeline.suggest_consolidation(group["id"])
    assert response["status"] == "pending"
    pid = response["proposal"]
    assert not (root / first["target"]).exists()
    assert frontmatter((root / response["path"]).read_text())[0]["status"] == "pending"
    decide(root, pipeline.state(), pid)
    preview = pipeline.apply(dry_run=True, proposal_ids=[pid])
    assert preview["can_apply"]
    pipeline.apply(proposal_ids=[pid], plan_token=preview["plan_token"])
    text = (root / first["source"]).read_text(encoding="utf-8")
    assert " — mechanism:" in text and " — assumption:" in text
    for old, record in proposals(pipeline, "link").items():
        assert not record["applied"] and record["superseded_by"] == pid
        assert frontmatter((root / record["path"]).read_text())[0]["superseded_by"] == pid


def test_keep_separate_pending_concepts_stays_valid_as_notes_are_created(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    first, second = link(), link(target="Concepts/other-mechanism.md")
    agent.links = [first, second]
    pipeline.sync()
    ids = list(proposals(pipeline, "link"))
    for pid in ids:
        decide(root, pipeline.state(), pid)
    assert not pipeline.review_status()["ready_ids"]
    group = next(
        g for g in pipeline.review_status()["concept_candidates"] if len(g["members"]) == 2
    )
    state = pipeline.state()
    suggestion = {
        "operation": "keep-separate",
        "canonical": None,
        "explanation": "Different experimental scopes.",
        "distinctions": "Conditions A and B must stay distinct.",
        "evidence": [{"note": first["source"], "quote": "Synthetic scientific evidence."}],
        "links": [],
    }
    changes = {}
    pid = register_consolidation(suggestion, group, state, read_notes(root), changes)
    pipeline.baseline(pipeline.state())
    pipeline.save(state, changes)
    decide(root, pipeline.state(), pid)
    pipeline.apply(proposal_ids=[pid])
    pipeline.apply(proposal_ids=[ids[0]])
    assert ids[1] in pipeline.review_status()["ready_ids"]
    pipeline.apply(proposal_ids=[ids[1]])
    assert not pipeline.review_status()["concept_candidates"]


def test_refresh_does_not_erase_other_stale_approvals(vault):
    root, _, _, _, pipeline = vault
    first, second = two_links(vault)
    source = root / "Literature/Synthetic item — PAPER001.md"
    source.write_text(source.read_text() + "\nChanged scope for both proposals.\n")
    old_baseline = deepcopy(pipeline.state()["proposals"][second]["expected"])
    pipeline.refresh_review(first)
    assert pipeline.state()["proposals"][second]["expected"] == old_baseline
    assert second not in pipeline.review_status()["ready_ids"]


def test_corrupt_dependency_snapshot_is_detected(vault):
    root, _, _, _, pipeline = vault
    first, _ = two_links(vault)
    import json

    state = pipeline.state()
    state["proposals"][first]["expected_notes"]["Literature/Synthetic item — PAPER001.md"] = (
        "Wrong historical content."
    )
    (root / ".pipeline/state.json").write_text(json.dumps(state))
    with pytest.raises(VaultError, match="snapshot integrity"):
        pipeline.review_status()


def test_review_cli_status_is_read_only_and_refresh_never_approves(vault, capsys):
    import json

    from research_vault.cli import main

    root, _, _, agent, pipeline = vault
    first, _ = two_links(vault)
    before = snapshot(root)
    assert main(["--vault", str(root), "review", "status"]) == 0
    assert first in json.loads(capsys.readouterr().out)["ready_ids"]
    assert snapshot(root) == before
    calls = deepcopy(agent.calls)
    assert main(["--vault", str(root), "review", "refresh", first]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "pending"
    assert agent.calls == calls
    assert not pipeline.state()["proposals"][result["replacement"]]["applied"]


def test_consolidation_inherits_linking_profile_and_has_a_strict_output_schema(vault):
    from research_vault.agents import SCHEMAS

    config = vault[-1].config
    config.data["llm"] = {"agents": {"linking": {"provider": "claude", "model": "synthetic-model"}}}
    assert config.runtime("consolidation") == config.runtime("linking")
    config.data["llm"]["agents"]["consolidation"] = {"provider": "codex"}
    assert config.runtime("consolidation")[0] == "codex"

    def strict(schema):
        if schema.get("type") == "object":
            assert set(schema["required"]) == set(schema["properties"])
            assert schema["additionalProperties"] is False
            for child in schema["properties"].values():
                strict(child)
        if schema.get("type") == "array":
            strict(schema["items"])
        for child in schema.get("anyOf", []):
            strict(child)

    strict(SCHEMAS["consolidation"])
