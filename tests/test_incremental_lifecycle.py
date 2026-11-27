import json
import sys
from copy import deepcopy
from pathlib import Path

import pymupdf
import pytest
from conftest import decide, link
from test_pipeline import add_paper, proposals

from research_vault.cancellation import SyncInterrupted, cancellation, run_process
from research_vault.identities import literature_path, relationship_identity
from research_vault.migrations import migrate
from research_vault.notes import new_note, read_notes, references, summary_content
from research_vault.recipes import reasoning_recipe
from research_vault.reviews import link_proposal, proposal, read_reviews
from research_vault.scientific import graph_signature, scientific_text
from research_vault.storage import VaultError, atomic, digest, frontmatter


@pytest.mark.parametrize("decision", [None, "approved", "rejected"])
def test_noop_after_success_or_link_decision_has_zero_inference(vault, decision):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    agent.links = [link()]
    pipeline.sync()
    if decision:
        pid = next(iter(proposals(pipeline, "link")))
        decide(root, pipeline.state(), pid, decision)
        pipeline.apply()
        record = pipeline.state()["proposals"][pid]
        assert record["path"].startswith(f".pipeline/review_history/{decision}/")
        assert (root / record["path"]).exists()
        assert not (root / record["original_path"]).exists()
    before = len(agent.calls)
    report = pipeline.sync()
    assert report["inference_operations"] == report["new_proposals"] == 0
    assert len(agent.calls) == before
    assert "no inference" in report["message"]


def test_relationship_identity_handles_symmetry_inverse_and_explicit_aliases():
    notes = {
        "Concepts/a.md": new_note("a", "concept", "Alpha", "Evidence alpha."),
        "Concepts/b.md": new_note("b", "concept", "Beta", "Evidence beta.", aliases=["other"]),
    }
    one = {
        **link("Concepts/a.md", "Concepts/b.md"),
        "relationship": "shared-concept",
        "target_concept": None,
        "evidence": [{"note": n, "quote": "Evidence"} for n in notes],
    }
    reverse = {
        **one,
        "source": one["target"],
        "target": one["source"],
        "justification": "New wording",
    }
    registry, changes = {}, {}
    first = link_proposal(one, registry, notes, changes)
    assert link_proposal(reverse, registry, notes, changes) == first
    assert len(registry) == 1
    assert relationship_identity(
        {**one, "target": "Concepts/other.md"}, notes
    ) == relationship_identity(one, notes)
    assert relationship_identity(
        {**one, "relationship": "generalization"}, notes
    ) == relationship_identity({**reverse, "relationship": "special-case"}, notes)
    assert relationship_identity(
        {**one, "relationship": "mechanism"}, notes
    ) != relationship_identity({**reverse, "relationship": "mechanism"}, notes)


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_revision_dedup_survives_decision_and_uses_relevant_paragraph(vault, decision):
    root, _, _, _, pipeline = vault
    name = "My Notes/hypothesis.md"
    (root / name).write_text("A scoped assumption needs verification.\n\nUnrelated hypothesis.")
    pipeline.sync()
    state, changes = pipeline.state(), {}
    revision = {
        "note": name,
        "reason": "Assumption needs supporting evidence",
        "suggestion": "Check the assumption",
        "evidence": [{"note": name, "quote": "A scoped assumption"}],
    }
    pid = proposal("revision", revision, state["proposals"], read_notes(root), changes)
    pipeline.baseline(pipeline.state())
    pipeline.save(state, changes)
    decide(root, pipeline.state(), pid, decision)
    pipeline.apply()
    changed = {
        **revision,
        "reason": "Missing evidence for the assumption",
        "suggestion": "Verify it manually",
        "evidence": [{"note": name, "quote": "needs verification."}],
    }
    state, changes = pipeline.state(), {}
    (root / name).write_text(
        (root / name).read_text().replace("Unrelated hypothesis.", "Changed unrelated hypothesis.")
    )
    assert proposal("revision", changed, state["proposals"], read_notes(root), changes) == pid
    assert not changes


def test_persistent_partial_coverage_does_not_repeat_scientific_reasoning(vault):
    _, source, z, agent, pipeline = vault
    path = source / "partial.pdf"
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "Synthetic scientific evidence.")
    document.new_page()
    document.save(path)
    document.close()
    z.paper("PAPER001", [("PDF00001", path, "main")])
    first = pipeline.sync()
    assert first["status"] == "completed_with_limitations"
    before = len(agent.calls)
    second = pipeline.sync()
    assert second["extraction_limitations"]
    assert not second["reasoning_needed"]
    assert second["inference_operations"] == second["new_proposals"] == 0
    assert len(agent.calls) == before
    pipeline.sync(retry=True)
    assert "explicit retry" == pipeline.state()["last_report"]["selection_reasons"]["PAPER001"]


@pytest.mark.parametrize("interrupt_role", ["literature", "synthesis"])
def test_interruption_marks_failed_and_resumes_completed_stages(vault, interrupt_role):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    marker = root / "interrupt"
    agent.callback = lambda role, payload: (
        marker.write_text("stop") if role == interrupt_role else None
    )
    with pytest.raises(SyncInterrupted), cancellation(marker):
        pipeline.sync()
    assert pipeline.status()["status"] == "failed"
    assert pipeline.status()["progress"]["stage"] == "failed"
    assert pipeline.state()["last_report"]["inference_operations"] > 0
    assert not marker.exists()
    before_linking = sum(role == "linking" for role, _ in agent.calls)
    agent.callback = None
    assert pipeline.sync()["complete"]
    if interrupt_role == "synthesis":
        assert before_linking > 0
        assert sum(role == "linking" for role, _ in agent.calls) == before_linking
    assert not pipeline.status()["reasoning_pending"]


def test_cancellation_terminates_waiting_runtime(tmp_path):
    # A synthetic local Python process replaces an LLM CLI; no provider is invoked.
    marker = tmp_path / "cancel"
    program = "from pathlib import Path; import time; Path(__import__('sys').argv[1]).write_text('stop'); time.sleep(30)"
    with pytest.raises(SyncInterrupted), cancellation(marker):
        run_process(
            [sys.executable, "-c", program, str(marker)], capture_output=True, text=True, timeout=60
        )
    assert not marker.exists()


def test_new_paper_and_one_personal_edit_only_reason_about_changed_notes(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    pipeline.sync()
    before = len(agent.calls)
    add_paper(vault, "PAPER002")
    pipeline.sync()
    recent = agent.calls[before:]
    assert [p["item_key"] for role, p in recent if role == "literature"] == ["PAPER002"]
    assert {p["focus_fragment"]["note"] for role, p in recent if role == "linking"} == {
        "Literature/Synthetic item — PAPER002.md"
    }
    one, two = root / "My Notes/one.md", root / "My Notes/two.md"
    one.write_text("A scientific assumption.")
    two.write_text("Another scientific assumption.")
    pipeline.sync()
    before = len(agent.calls)
    one.write_text("A modified scientific assumption.")
    pipeline.sync()
    assert {
        p["focus_fragment"]["note"] for role, p in agent.calls[before:] if role == "linking"
    } == {"My Notes/one.md"}


def legacy_layout(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    agent.links = [link()]
    pipeline.sync()
    current = pipeline.state()["papers"]["PAPER001"]["note"]
    legacy = "Literature/PAPER001.md"
    (root / current).rename(root / legacy)
    state = json.loads(json.dumps(pipeline.state(), ensure_ascii=False).replace(current, legacy))
    # Registered review payloads and their byte snapshots move consistently.
    for record in state["proposals"].values():
        path = root / record["path"]
        path.write_text(path.read_text().replace(current, legacy))
        record["payload_hash"] = digest(record["payload"])
    state["graph_digest"] = graph_signature(read_notes(root), reasoning_recipe(pipeline.config))
    atomic(root / ".pipeline/state.json", json.dumps(state, ensure_ascii=False))
    return legacy, current


def test_title_migration_preserves_pending_reviews_additions_and_alias_backlinks(vault):
    root, _, _, agent, pipeline = vault
    old, new = legacy_layout(vault)
    (root / old).write_text((root / old).read_text() + "\nPERSONAL ADDITION\n")
    personal = root / "My Notes/citation.md"
    personal.write_text("A reference [[Literature/PAPER001|paper]].")
    before_calls = len(agent.calls)
    preview = migrate(pipeline)
    assert preview["renames"] == {old: new}
    assert personal.relative_to(root).as_posix() in preview["user_note_previews"]
    assert (root / old).exists() and not (root / new).exists()
    migrate(pipeline, True, preview["plan_token"])
    assert len(agent.calls) == before_calls
    assert "PERSONAL ADDITION" in (root / new).read_text()
    assert "PAPER001" in frontmatter((root / new).read_text())[0]["aliases"]
    assert personal.read_text() == "A reference [[Literature/PAPER001|paper]]."
    assert (
        references(personal.read_text(), "My Notes/citation.md", read_notes(root))[0].target == new
    )
    record = next(iter(proposals(pipeline, "link").values()))
    assert record["payload"]["source"] == new
    assert record["migration_history"]
    assert read_reviews(root, pipeline.state()["proposals"])


@pytest.mark.parametrize("operation", ["migrate", "apply"])
def test_transaction_failure_rolls_back_migration_and_archive(vault, monkeypatch, operation):
    import research_vault.storage as storage

    root, _, _, agent, pipeline = vault
    if operation == "migrate":
        legacy_layout(vault)
        preview = migrate(pipeline)

        def execute():
            return migrate(pipeline, True, preview["plan_token"])

        fail_prefix = ".pipeline/migration_history/"
    else:
        add_paper(vault)
        agent.links = [link()]
        pipeline.sync()
        decide(root, pipeline.state(), next(iter(proposals(pipeline, "link"))))
        execute = pipeline.apply
        fail_prefix = ".pipeline/review_history/"
    observed = {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and "/cache/" not in p.as_posix() and "/rollback/" not in p.as_posix()
    }
    original = storage.atomic
    failed = False

    def failing(path, content):
        nonlocal failed
        if not failed and path.relative_to(root).as_posix().startswith(fail_prefix):
            failed = True
            raise OSError("synthetic disk failure")
        return original(path, content)

    monkeypatch.setattr(storage, "atomic", failing)
    with pytest.raises((OSError, VaultError), match="synthetic disk failure"):
        execute()
    assert failed
    assert not (root / ".pipeline/transaction.json").exists()
    assert all((root / name).read_bytes() == content for name, content in observed.items())
    assert not list((root / fail_prefix).rglob("*.md" if operation == "apply" else "*.json"))


@pytest.mark.parametrize(
    "title", ["CON", "nul.txt", "A:B/C\\D*?<>|[]#", "é測定" * 120, "same title", "same title."]
)
def test_portable_title_filenames_are_bounded_unique_and_unicode(tmp_path, title):
    metadata = {"data": {"title": title}}
    first = literature_path("PAPER001", metadata, tmp_path)
    assert first == literature_path("PAPER001", deepcopy(metadata), tmp_path)
    assert first != literature_path("PAPER002", metadata, tmp_path)
    assert len(str(tmp_path / first)) < 240
    assert not set('<>:"\\|?*[]#') & set(Path(first).name)
    assert len(Path(first).name.encode("utf-8")) < 255


def test_math_renderer_normalizes_prose_and_preserves_evidence():
    excerpt = r"Original \(x=1\) evidence."
    evidence = {"source": "PDF00001", "locator": "page 1", "quote": excerpt}
    result = {
        "summary": r"Inline \(x=1\), display \[y=x^2\].",
        "evidence": [evidence],
        "claims": [],
    }
    content = summary_content(
        result, [{"source": "PDF00001", "locator": "page 1", "text": excerpt}], {}
    )
    assert "Inline $x=1$, display $$y=x^2$$." in content
    assert excerpt in content
    concept = new_note("concept:a", "concept", "Math", r"\(x=1\) and \[y=2\]")
    assert "$x=1$ and $$y=2$$" in concept


def test_dry_run_is_read_only_and_does_not_authenticate(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    agent.authenticate = lambda: pytest.fail("dry run must not authenticate")
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert pipeline.sync(dry_run=True)["dry_run"]
    assert not agent.calls
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_retry_limit_stops_unchanged_transient_inference_failure(vault):
    _, _, _, agent, pipeline = vault
    add_paper(vault)
    agent.fail_role = "literature"
    for _ in range(pipeline.config.processing("max_retry_attempts")):
        pipeline.sync()
    before = len(agent.calls)
    report = pipeline.sync()
    assert len(agent.calls) == before
    assert report["outstanding_failures"]
    pipeline.sync(retry=True)
    assert len(agent.calls) > before


def test_bibliography_only_edit_updates_zotero_fields_without_inference(vault):
    root, source, zotero, agent, pipeline = vault
    add_paper(vault)
    agent.links = [link()]
    pipeline.sync()
    pid = next(iter(proposals(pipeline, "link")))
    before = len(agent.calls)
    zotero.paper(
        "PAPER001",
        [("PDF00001", source / "PAPER001.pdf", "main")],
        version=2,
        title="An original revised paper title",
        date="2025-09-12",
        creators=[{"creatorType": "author", "firstName": "Ada", "lastName": "Example"}],
        publicationTitle="Synthetic Journal",
        DOI="10.0000/synthetic",
    )
    report = pipeline.sync()
    text = (root / pipeline.state()["papers"]["PAPER001"]["note"]).read_text()
    assert "# An original revised paper title" in text
    assert "Authors: Ada Example" in text and "Year: 2025" in text
    assert "Journal: Synthetic Journal" in text and "DOI: 10.0000/synthetic" in text
    assert "zotero://select/library/items/PAPER001" in text
    assert len(agent.calls) == before and not report["inference_operations"]
    assert not report["reasoning_needed"]
    decide(root, pipeline.state(), pid)
    assert pid in pipeline.review_status()["ready_ids"]


def test_personal_approved_graph_lines_do_not_reenter_scientific_index(vault):
    root, _, _, agent, pipeline = vault
    source = "My Notes/personal.md"
    (root / source).write_text("Synthetic scientific evidence.")
    agent.links = [link(source)]
    pipeline.sync()
    before_science = scientific_text((root / source).read_text())
    pid = next(iter(proposals(pipeline, "link")))
    decide(root, pipeline.state(), pid)
    pipeline.apply()
    assert scientific_text((root / source).read_text()) == before_science
    before = len(agent.calls)
    assert not pipeline.sync()["inference_operations"]
    assert len(agent.calls) == before
    (root / "My Notes/new.md").write_text("Another scientific question.")
    pipeline.sync()
    assert not any(
        role == "concepts" and source in payload["notes"] for role, payload in agent.calls[before:]
    )


def test_migration_moves_approved_review_history_without_losing_original_payload(vault):
    root, _, _, _, pipeline = vault
    old, new = legacy_layout(vault)
    pid = next(iter(proposals(pipeline, "link")))
    decide(root, pipeline.state(), pid)
    pipeline.apply()
    original = deepcopy(pipeline.state()["proposals"][pid]["payload"])
    preview = migrate(pipeline)
    migrate(pipeline, True, preview["plan_token"])
    record = pipeline.state()["proposals"][pid]
    assert record["applied"] and record["decision"] == "approved"
    assert record["payload"]["source"] == new and original["source"] == old
    audit = json.loads((root / record["migration_history"][-1]).read_text())
    assert audit["record"]["payload"] == original
    assert read_reviews(root, pipeline.state()["proposals"])[pid]["status"] == "approved"
    assert "[[Concepts/mechanism]]" in (root / new).read_text()
    assert pipeline.state()["concept_registry"]["relationships"]


def test_authorized_migration_reference_changes_require_unchanged_preview(vault):
    root, _, _, _, pipeline = vault
    old, new = legacy_layout(vault)
    name = "My Notes/reference.md"
    (root / name).write_text("[[Literature/PAPER001|original]]")
    preview = migrate(pipeline, authorize_notes=[name])
    with pytest.raises(VaultError, match="preview changed"):
        migrate(pipeline, True, "0" * 64, [name])
    migrate(pipeline, True, preview["plan_token"], [name])
    assert (root / name).read_text() == "[[" + new[:-3] + "|original]]"
    assert not (root / old).exists()


def test_migration_updates_withheld_legacy_claim_destination_without_processing(vault):
    from test_scientific_reviews import uncertain_summaries

    root, _, _, agent, pipeline = vault
    add_paper(vault)
    uncertain_summaries(agent)
    pipeline.sync()
    pid = next(iter(proposals(pipeline, "claim")))
    new = pipeline.state()["papers"]["PAPER001"]["note"]
    old = "Literature/PAPER001.md"
    state = json.loads(json.dumps(pipeline.state(), ensure_ascii=False).replace(new, old))
    for record in state["proposals"].values():
        path = root / record["path"]
        path.write_text(path.read_text().replace(new, old))
        record["payload_hash"] = digest(record["payload"])
    atomic(root / ".pipeline/state.json", json.dumps(state, ensure_ascii=False))
    before = len(agent.calls)
    preview = migrate(pipeline)
    assert preview["renames"] == {old: new}
    migrate(pipeline, True, preview["plan_token"])
    assert len(agent.calls) == before
    assert not (root / new).exists()
    assert pipeline.state()["proposals"][pid]["claim_context"]["note"] == new
    decide(root, pipeline.state(), pid)
    pipeline.apply()
    assert (root / new).exists() and not (root / old).exists()


@pytest.mark.parametrize(
    "alias_field",
    [
        "",
        "aliases: Original  # keep scalar comment\n",
        "aliases:\n  - Original  # keep block comment\n",
        "aliases: [Original, # keep flow comment\n Other]\n",
    ],
)
def test_alias_migration_preserves_frontmatter_comments_and_existing_values(alias_field):
    from research_vault.notes import append_aliases

    text = (
        "---\n# user metadata comment\nid: zotero:PAPER001\n"
        + alias_field
        + "custom: Scientific value # preserve\n---\nUser additions.\n"
    )
    updated = append_aliases(text, ["Literature/PAPER001", "PAPER001"])
    meta, body = frontmatter(updated)
    assert meta["aliases"][-2:] == ["Literature/PAPER001", "PAPER001"]
    if alias_field:
        assert "Original" in meta["aliases"]
        assert "# keep " in updated
    assert "# user metadata comment" in updated
    assert "custom: Scientific value # preserve" in updated
    assert body == "User additions.\n"


def test_explicit_retry_selects_unfinished_documents_independently(vault):
    _, _, zotero, _, pipeline = vault
    add_paper(vault)
    zotero.paper("PAPER002")
    pipeline.sync()
    report = pipeline.sync(retry=True)
    assert report["papers"] == ["PAPER002"]
    assert report["selection_reasons"] == {"PAPER002": "explicit retry"}
    assert report["retried_operations"] == {"PAPER002": "explicit retry"}
    assert report["inference_operations"] == 0


def test_annotation_order_and_operational_timestamps_do_not_schedule_inference(vault):
    _, _, zotero, agent, pipeline = vault
    add_paper(vault)
    zotero.items["PAPER001"]["annotations"] = [
        {
            "key": "ANN00001",
            "version": 1,
            "data": {"annotationText": "Scientific observation", "dateModified": "old"},
        },
        {"key": "ANN00002", "version": 1, "data": {"annotationComment": "Personal question"}},
    ]
    pipeline.sync()
    before = len(agent.calls)
    annotations = zotero.items["PAPER001"]["annotations"]
    annotations.reverse()
    annotations[1]["version"] = 2
    annotations[1]["data"]["dateModified"] = "new"
    zotero.items["PAPER001"]["fingerprint"] = "operational-change"
    report = pipeline.sync()
    assert report["papers"] == [] and report["inference_operations"] == 0
    assert len(agent.calls) == before


def test_reused_path_with_different_immutable_note_id_does_not_inherit_approval():
    source, target = "Concepts/source.md", "Concepts/target.md"
    notes = {
        source: new_note("original-source", "concept", "Source", "Scientific evidence."),
        target: new_note("stable-target", "concept", "Target", "Scientific evidence."),
    }
    candidate = {
        **link(source, target),
        "target_concept": None,
        "evidence": [{"note": n, "quote": "Scientific evidence."} for n in notes],
    }
    registry, changes = {}, {}
    original = link_proposal(candidate, registry, notes, changes)
    registry[original]["applied"] = True
    registry[original]["decision"] = "approved"
    notes[source] = new_note(
        "different-source", "concept", "Different source", "Scientific evidence."
    )
    replacement = link_proposal(candidate, registry, notes, changes)
    assert replacement != original
    assert not registry[replacement]["applied"]
