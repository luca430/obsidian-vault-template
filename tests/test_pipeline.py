from __future__ import annotations

import subprocess
from copy import deepcopy

import pytest
import yaml
from conftest import decide, link, write_pdf

from research_vault.config import Config
from research_vault.pipeline import Pipeline
from research_vault.storage import VaultError, frontmatter


def add_paper(vault, key="PAPER001", version=1):
    root, sources, z, agent, pipeline = vault
    path = sources / (key + ".pdf")
    if not path.exists():
        write_pdf(path)
    z.paper(key, [("PDF00001", path, "main")], version)
    return path


def proposals(pipeline, kind):
    return {pid: r for pid, r in pipeline.state()["proposals"].items() if r["kind"] == kind}


def test_first_empty_collection_and_unchanged_avoid_agents(vault):
    root, _, _, agent, pipeline = vault
    pipeline.sync()
    assert pipeline.state()["membership"] == []
    assert not agent.calls
    pipeline.sync()
    assert not agent.calls
    assert "Processed papers: 0" in (root / "Home.md").read_text()


def test_add_update_stable_id_and_user_additions(vault):
    root, _, z, agent, pipeline = vault
    add_paper(vault)
    pipeline.sync()
    note = root / "Literature/Synthetic item — PAPER001.md"
    note.write_text(note.read_text() + "\nUSER ADDITION\n")
    add_paper(vault, version=2)
    pipeline.sync()
    assert "USER ADDITION" in note.read_text()
    assert frontmatter(note.read_text())[0]["id"] == "zotero:PAPER001"
    assert len(list((root / "Literature").glob("*.md"))) == 1
    assert sum(r == "literature" for r, _ in agent.calls) == 1  # Zotero version alone is metadata.
    before = len(agent.calls)
    pipeline.sync()
    assert len(agent.calls) == before


def test_multiple_pdfs_and_supplementary_byte_change(vault):
    root, sources, z, agent, pipeline = vault
    main = write_pdf(sources / "main.pdf", "Main scientific content.")
    supplement = write_pdf(sources / "supplement.pdf", "Supplementary measurements.")
    z.paper("PAPER001", [("PDF00001", main, "main"), ("PDF00002", supplement, "supplementary")])
    pipeline.sync()
    payloads = [p for r, p in agent.calls if r == "literature" and p.get("item_key")]
    assert {page["source"] for p in payloads for page in p["pages"]} == {"PDF00001", "PDF00002"}
    assert {page["classification"] for p in payloads for page in p["pages"]} == {
        "main",
        "supplementary",
    }
    before = len(payloads)
    supplement.unlink()
    write_pdf(supplement, "Updated supplementary measurements.")
    z.paper("PAPER001", [("PDF00001", main, "main"), ("PDF00002", supplement, "supplementary")])
    pipeline.sync()
    assert sum(r == "literature" and bool(p.get("item_key")) for r, p in agent.calls) == before + 1
    assert len(list((root / "Literature").glob("*.md"))) == 1


def test_failed_api_does_not_trigger_removals_or_snapshot_update(vault):
    _, _, z, _, pipeline = vault
    add_paper(vault)
    pipeline.sync()
    before = pipeline.state()
    z.items = {}
    z.fail = True
    with pytest.raises(VaultError, match="connectivity"):
        pipeline.sync()
    assert pipeline.state() == before
    assert not proposals(pipeline, "removal")


def test_removed_member_review_and_restoration(vault):
    root, _, z, _, pipeline = vault
    add_paper(vault)
    pipeline.sync()
    original = z.items.copy()
    z.items.clear()
    pipeline.sync()
    pending = proposals(pipeline, "removal")
    assert len(pending) == 1
    assert (root / "Literature/Synthetic item — PAPER001.md").exists()
    pipeline.sync()
    assert len(proposals(pipeline, "removal")) == 1
    decide(root, pipeline.state(), next(iter(pending)), decision="flag")
    pipeline.apply()
    assert (
        "removed from the active collection"
        in (root / "Literature/Synthetic item — PAPER001.md").read_text()
    )
    z.items = original
    pipeline.sync()
    assert (
        "removed from the active collection"
        not in (root / "Literature/Synthetic item — PAPER001.md").read_text()
    )
    assert pipeline.state()["papers"]["PAPER001"]["disposition"] == "active"
    assert len(list((root / "Literature").glob("*.md"))) == 1


def test_links_require_approval_and_apply_idempotently(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    agent.links = [link()]
    pipeline.sync()
    assert not (root / "Concepts/mechanism.md").exists()
    pending = proposals(pipeline, "link")
    assert len(pending) == 1
    path = root / next(iter(pending.values()))["path"]
    assert "[[" not in path.read_text()
    assert "[[" not in (root / "Literature/Synthetic item — PAPER001.md").read_text()
    decide(root, pipeline.state(), next(iter(pending)))
    pipeline.apply()
    pipeline.apply()
    assert (root / "Concepts/mechanism.md").exists()
    assert (root / "Literature/Synthetic item — PAPER001.md").read_text().count(
        "[[Concepts/mechanism]]"
    ) == 1


def test_rejected_links_not_repeated_without_evidence_change(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    agent.links = [link()]
    pipeline.sync()
    pid = next(iter(proposals(pipeline, "link")))
    decide(root, pipeline.state(), pid, "rejected")
    (root / "My Notes/question.md").write_text("A new scientific question.")
    agent.links[0]["justification"] = "Different wording; same evidence."
    pipeline.sync()
    assert len(proposals(pipeline, "link")) == 1
    assert not (root / "Concepts/mechanism.md").exists()
    pipeline.apply()
    assert not (root / "Concepts/mechanism.md").exists()


def test_changed_evidence_requires_explicit_reconsideration(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    agent.links = [link()]
    pipeline.sync()
    pid = next(iter(proposals(pipeline, "link")))
    decide(root, pipeline.state(), pid, "rejected")
    source = root / "Literature/Synthetic item — PAPER001.md"
    source.write_text(source.read_text() + "\nAdditional scientific evidence.\n")
    agent.links[0]["evidence"][0]["quote"] = "Additional scientific evidence."
    pipeline.sync()
    assert len(proposals(pipeline, "link")) == 1
    replacement = pipeline.reconsider_review(
        pid, agent.links[0]["source"], "Additional scientific evidence."
    )
    assert replacement["status"] == "pending"
    assert len(proposals(pipeline, "link")) == 2


def test_personal_note_changes_trigger_reasoning_but_are_preserved(vault):
    root, _, _, agent, pipeline = vault
    path = root / "My Notes/idea.md"
    path.write_text("My hypothesis.")
    pipeline.sync()
    before = len(agent.calls)
    pipeline.sync()
    assert len(agent.calls) == before
    path.write_text("My revised hypothesis.")
    pipeline.sync()
    assert len(agent.calls) > before
    assert path.read_text() == "My revised hypothesis."


def test_manuscript_incremental_includes_math_and_missing_source(vault):
    root, source, z, agent, pipeline = vault
    main = source / "main.tex"
    child = source / "methods.tex"
    main.write_text("Objective: synthetic study.\\input{methods}")
    child.write_text("Mathematics: $E=mc^2$.")
    work = {
        "id": "draft1",
        "title": "Draft",
        "document_type": "manuscript",
        "status": "draft",
        "source_type": "latex",
        "source_path": str(main),
    }
    (root / "works.yaml").write_text(yaml.safe_dump({"works": [work]}))
    pipeline.sync()
    assert "\\input" in main.read_text()
    assert "$E=mc^2$" in (root / "My Work/draft1.md").read_text()
    before = len(agent.calls)
    pipeline.sync()
    assert len(agent.calls) == before
    child.write_text("Mathematics: $E=mc^2$. Additional term.")
    pipeline.sync()
    assert sum(r == "manuscript" for r, _ in agent.calls) == 2
    main.unlink()
    pipeline.sync()
    assert (root / "My Work/draft1.md").exists()
    assert any("disappeared" in w for w in pipeline.state()["warnings"])


def test_failed_reasoning_preserves_documents_and_retries(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    agent.fail_role = "linking"
    result = pipeline.sync()
    assert not result["complete"]
    assert "agent failure" in result["graph_error"]
    assert pipeline.state()["membership"] == ["PAPER001"]
    assert (root / "Literature/Synthetic item — PAPER001.md").exists()
    assert pipeline.state()["last_success"] is None
    agent.fail_role = None
    pipeline.sync()
    assert sum(r == "literature" for r, _ in agent.calls) == 1
    assert pipeline.state()["last_success"]


def test_no_citation_based_links(vault):
    root, sources, z, agent, pipeline = vault
    main = write_pdf(sources / "main.pdf", "This text cites PAPER002 for attribution only.")
    z.paper("PAPER001", [("PDF00001", main, "main")], relations={"dc:relation": "PAPER002"})
    pipeline.sync()
    assert not proposals(pipeline, "link")
    assert "[[" not in (root / "Literature/Synthetic item — PAPER001.md").read_text()


def test_dry_run_no_writes_or_llm_calls(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert pipeline.sync(dry_run=True)["papers"] == ["PAPER001"]
    after = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert before == after
    assert not agent.calls


def test_full_resync_reprocesses_without_duplicate_identities(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    agent.links = [link()]
    pipeline.sync()
    ids = set(pipeline.state()["proposals"])
    pipeline.sync(full=True)
    assert set(pipeline.state()["proposals"]) == ids
    assert len(list((root / "Literature").glob("*.md"))) == 1
    assert sum(r == "literature" for r, _ in agent.calls) == 2


def test_user_edit_during_agent_run_aborts_integration(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    personal = root / "My Notes/idea.md"
    personal.write_text("Original hypothesis.")

    def edit(role, payload):
        if role == "linking":
            personal.write_text("User edited during inference.")

    agent.callback = edit
    with pytest.raises(VaultError, match="Notes changed during processing"):
        pipeline.sync()
    assert personal.read_text() == "User edited during inference."
    assert pipeline.state()["membership"] == ["PAPER001"]
    assert pipeline.state()["papers"]["PAPER001"]["status"] == "successful"


def git_init(root):
    for command in [
        ["git", "init"],
        ["git", "config", "user.email", "test@example.invalid"],
        ["git", "config", "user.name", "Synthetic Test"],
        ["git", "add", "AGENTS.md"],
        ["git", "commit", "-m", "Synthetic checkpoint"],
    ]:
        subprocess.run(command, cwd=root, check=True, capture_output=True)


def test_deletion_backup_cleanup_and_personal_note_preserved(vault):
    root, _, z, _, pipeline = vault
    add_paper(vault)
    pipeline.sync()
    personal = root / "My Notes/idea.md"
    personal.write_text("User hypothesis refers to [[Literature/Synthetic item — PAPER001]].")
    # An approved relationship is in a Python-owned links region.
    from research_vault.notes import new_note

    concept = root / "Concepts/existing.md"
    concept.write_text(
        new_note("concept:existing", "concept", "Existing", "Synthetic concept.").replace(
            "<!-- links:start -->",
            "<!-- links:start -->\n- [[Literature/Synthetic item — PAPER001]]",
        )
    )
    git_init(root)
    z.items.clear()
    pipeline.sync()
    removal = next(iter(proposals(pipeline, "removal")))
    decide(root, pipeline.state(), removal, decision="delete")
    before = personal.read_bytes()
    preview = pipeline.apply(dry_run=True)
    assert "My Notes/idea.md" in preview["impact_previews"][0]["incoming"]
    assert (root / "Literature/Synthetic item — PAPER001.md").exists()
    result = pipeline.apply()
    assert result["destructive"]
    assert (root / result["backup"]).exists()
    assert not (root / "Literature/Synthetic item — PAPER001.md").exists()
    assert personal.read_bytes() == before
    assert "[[Literature/Synthetic item — PAPER001]]" not in concept.read_text()
    assert any(
        "Source removed" in r["payload"].get("reason", "")
        for r in proposals(pipeline, "revision").values()
    )
    import zipfile

    with zipfile.ZipFile(root / result["backup"]) as archive:
        assert "Literature/Synthetic item — PAPER001.md" in archive.namelist()
    pipeline.apply()  # Applied removal is idempotent.


def test_deletion_refused_without_git_or_dirty_tree(vault):
    root, _, z, _, pipeline = vault
    add_paper(vault)
    pipeline.sync()
    z.items.clear()
    pipeline.sync()
    decide(root, pipeline.state(), next(iter(proposals(pipeline, "removal"))), decision="delete")
    with pytest.raises(VaultError, match="requires Git"):
        pipeline.apply()
    assert (root / "Literature/Synthetic item — PAPER001.md").exists()
    git_init(root)
    (root / "AGENTS.md").write_text("Tracked change")
    with pytest.raises(VaultError, match="Commit or stash"):
        pipeline.apply()
    assert (root / "Literature/Synthetic item — PAPER001.md").exists()


def test_tampered_and_stale_approval_rejected(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    agent.links = [link()]
    pipeline.sync()
    pid = next(iter(proposals(pipeline, "link")))
    decide(root, pipeline.state(), pid)
    source = root / "Literature/Synthetic item — PAPER001.md"
    source.write_text(source.read_text() + "\nChanged user material.\n")
    with pytest.raises(VaultError, match="Stale approval"):
        pipeline.apply()
    decide(root, pipeline.state(), pid, "pending")
    pipeline.sync()
    decide(root, pipeline.state(), pid)
    path = root / pipeline.state()["proposals"][pid]["path"]
    path.write_text(path.read_text().replace("Concepts/mechanism.md", "Concepts/evil.md"))
    with pytest.raises(VaultError, match="payload was changed"):
        pipeline.apply()


def test_invalid_llm_cannot_self_approve_or_create_links(vault):
    _, _, _, agent, pipeline = vault
    add_paper(vault)
    original = agent.run

    def invalid(role, payload):
        result = original(role, payload)
        if role == "literature":
            result["summary"] += "\n[[Concepts/unapproved]]"
        return result

    agent.run = invalid
    result = pipeline.sync()
    assert "contains links" in result["failed"]["PAPER001"]
    assert pipeline.state()["membership"] == ["PAPER001"]
    assert not pipeline.state()["papers"]["PAPER001"]["processed"]
    assert not pipeline.state()["proposals"]


def test_pdf_only_manuscript_and_registered_metadata_change(vault):
    root, sources, _, agent, pipeline = vault
    document = write_pdf(sources / "report.pdf", "Synthetic report findings.")
    work = {
        "id": "report",
        "title": "Report",
        "document_type": "report",
        "status": "draft",
        "source_type": "pdf",
        "source_path": str(document),
    }
    registry = root / "works.yaml"
    registry.write_text(yaml.safe_dump({"works": [work]}))
    pipeline.sync()
    before = len(agent.calls)
    pipeline.sync()
    assert len(agent.calls) == before
    work["status"] = "submitted"
    registry.write_text(yaml.safe_dump({"works": [work]}))
    pipeline.sync()
    assert sum(r == "manuscript" for r, _ in agent.calls) == 2
    assert frontmatter((root / "My Work/report.md").read_text())[0]["id"] == "work:report"


def test_multiple_parents_are_distinct_even_with_same_filename(vault):
    root, sources, z, _, pipeline = vault
    for key, attachment in [("PAPER001", "PDF00001"), ("PAPER002", "PDF00002")]:
        directory = sources / key
        directory.mkdir()
        path = write_pdf(directory / "same-name.pdf")
        z.paper(key, [(attachment, path, "main")])
    pipeline.sync()
    assert set(p.name for p in (root / "Literature").glob("*.md")) == {
        "Synthetic item — PAPER001.md",
        "Synthetic item — PAPER002.md",
    }


def test_unknown_summary_ownership_prevents_overwrite(vault):
    root, _, _, _, pipeline = vault
    add_paper(vault)
    note = root / "Literature/Synthetic item — PAPER001.md"
    note.write_text("User text without generated ownership markers.")
    with pytest.raises(VaultError, match="frontmatter"):
        pipeline.sync()
    assert note.read_text() == "User text without generated ownership markers."


def test_keep_removal_and_explicit_approval_required(vault):
    root, _, z, _, pipeline = vault
    add_paper(vault)
    pipeline.sync()
    original = (root / "Literature/Synthetic item — PAPER001.md").read_bytes()
    z.items.clear()
    pipeline.sync()
    pid = next(iter(proposals(pipeline, "removal")))
    decide(root, pipeline.state(), pid, "pending", decision="delete")
    pipeline.apply()
    assert (root / "Literature/Synthetic item — PAPER001.md").read_bytes() == original
    decide(root, pipeline.state(), pid, decision="keep")
    pipeline.apply()
    assert pipeline.state()["papers"]["PAPER001"]["disposition"] == "kept"
    assert (root / "Literature/Synthetic item — PAPER001.md").read_bytes() == original


def test_user_approved_personal_link_only_appends(vault):
    root, _, _, agent, pipeline = vault
    personal = root / "My Notes/hypothesis.md"
    original = "Synthetic scientific evidence. This is a personal hypothesis.\n"
    personal.write_text(original)
    agent.links = [link(source="My Notes/hypothesis.md")]
    pipeline.sync()
    assert personal.read_text() == original
    pid = next(iter(proposals(pipeline, "link")))
    decide(root, pipeline.state(), pid)
    pipeline.apply()
    assert personal.read_text().startswith(original)
    assert "[[Concepts/mechanism]]" in personal.read_text()


def test_revisions_are_advisory(vault):
    root, _, _, _, pipeline = vault
    personal = root / "My Notes/idea.md"
    personal.write_text("My uncertain hypothesis.")
    pipeline.sync()
    revision = next(iter(proposals(pipeline, "revision")))
    decide(root, pipeline.state(), revision)
    pipeline.apply()
    assert personal.read_text() == "My uncertain hypothesis."
    assert pipeline.state()["proposals"][revision]["applied"]


def test_malformed_review_fails_before_any_approved_link_is_applied(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    agent.links = [link()]
    pipeline.sync()
    pid = next(iter(proposals(pipeline, "link")))
    decide(root, pipeline.state(), pid)
    path = root / pipeline.state()["proposals"][pid]["path"]
    path.write_text(path.read_text().replace("status: approved", "status: [approved]"))
    with pytest.raises(VaultError, match="Invalid review status"):
        pipeline.apply()
    assert not (root / "Concepts/mechanism.md").exists()


def test_missing_pdf_no_fabricated_summary(vault):
    root, _, z, agent, pipeline = vault
    z.paper("PAPER001")
    assert any("no processable PDF" in w for w in pipeline.sync(dry_run=True)["warnings"])
    result = pipeline.sync()
    assert not result["complete"]
    assert "no readable" in result["skipped"]["PAPER001"]
    assert not agent.calls
    assert not (root / "Literature/Synthetic item — PAPER001.md").exists()
    assert pipeline.state()["membership"] == ["PAPER001"]
    assert pipeline.state()["papers"]["PAPER001"]["status"] == "skipped"


def test_revised_summary_title_is_managed_and_user_area_preserved(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    pipeline.sync()
    note = root / "Literature/Synthetic item — PAPER001.md"
    note.write_text(note.read_text() + "\nUser-authored continuation.\n")
    original = agent.run

    def rename(role, payload):
        output = original(role, payload)
        if role == "literature":
            output["title"] = "Updated scientific title"
        return output

    agent.run = rename
    pipeline.sync(full=True)
    assert "# Synthetic item" in note.read_text()  # Heading comes from Zotero, never inference.
    assert "User-authored continuation." in note.read_text()


def test_cached_manuscript_includes_obey_revoked_authorization(vault):
    root, source, _, _, pipeline = vault
    second = source.parent / "second-project"
    second.mkdir()
    (second / "external.tex").write_text("Authorized at first.")
    main = source / "main.tex"
    main.write_text("Synthetic study.\\input{../second-project/external}")
    config = yaml.safe_load((root / "config.yaml").read_text())
    config["source_roots"].append(str(second))
    (root / "config.yaml").write_text(yaml.safe_dump(config))
    work = {
        "id": "draft",
        "title": "Draft",
        "document_type": "manuscript",
        "status": "draft",
        "source_type": "latex",
        "source_path": str(main),
    }
    (root / "works.yaml").write_text(yaml.safe_dump({"works": [work]}))
    pipeline = Pipeline(Config.load(root), vault[2], vault[3])
    pipeline.sync()
    config["source_roots"] = [str(source)]
    (root / "config.yaml").write_text(yaml.safe_dump(config))
    pipeline = Pipeline(Config.load(root), vault[2], vault[3])
    with pytest.raises(VaultError, match="outside configured"):
        pipeline.sync()
    assert (root / "My Work/draft.md").exists()


def test_manual_prose_revision_can_be_acknowledged_after_edit(vault):
    root, _, _, _, pipeline = vault
    note = root / "My Notes/idea.md"
    note.write_text("Initial hypothesis.")
    pipeline.sync()
    pid = next(iter(proposals(pipeline, "revision")))
    note.write_text("Manually clarified hypothesis.")
    decide(root, pipeline.state(), pid)
    pipeline.apply()
    assert note.read_text() == "Manually clarified hypothesis."
    assert pipeline.state()["proposals"][pid]["applied"]


def test_rejection_identity_ignores_evidence_order_and_whitespace(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    candidate = link()
    candidate["evidence"].append(
        {
            "note": candidate["source"],
            "quote": "Text extraction only; figures were not interpreted.",
        }
    )
    agent.links = [candidate]
    pipeline.sync()
    pid = next(iter(proposals(pipeline, "link")))
    decide(root, pipeline.state(), pid, "rejected")
    candidate["evidence"].reverse()
    candidate["evidence"][1]["quote"] = "Synthetic  scientific evidence."
    (root / "My Notes/new.md").write_text("A new personal question.")
    pipeline.sync()
    assert len(proposals(pipeline, "link")) == 1


def test_appeared_concept_invalidates_unseen_target_approval(vault):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    agent.links = [link()]
    pipeline.sync()
    pid = next(iter(proposals(pipeline, "link")))
    decide(root, pipeline.state(), pid)
    target = root / "Concepts/mechanism.md"
    target.write_text("# User-defined concept with different scientific scope.\n")
    with pytest.raises(VaultError, match="already appeared") as exc:
        pipeline.apply()
    assert "Concepts/mechanism.md" in str(exc.value)
    assert proposals(pipeline, "link")[pid]["path"] in str(exc.value)
    assert "Literature/Synthetic item — PAPER001.md" in str(exc.value)
    assert (
        "[[Concepts/mechanism]]"
        not in (root / "Literature/Synthetic item — PAPER001.md").read_text()
    )
    assert "User-defined concept" in target.read_text()


@pytest.mark.parametrize("dry_run", [True, False])
def test_concept_conflict_lists_all_reviews_and_notes_without_writes(vault, dry_run):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    add_paper(vault, "PAPER002")
    first = link()
    second = link(source="Literature/Synthetic item — PAPER002.md")
    second["target_concept"]["body"] = "A different definition requiring a user decision."
    third = deepcopy(first)
    third["relationship"] = "method"
    other_first = link(target="Concepts/another.md")
    other_second = link(
        source="Literature/Synthetic item — PAPER002.md", target="Concepts/another.md"
    )
    other_second["target_concept"]["title"] = "A different title"
    agent.links = [first, second, third, other_first, other_second]
    pipeline.sync()
    registered = proposals(pipeline, "link")
    assert len(registered) == 5
    for pid in registered:
        decide(root, pipeline.state(), pid)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    if dry_run:
        preview = pipeline.apply(dry_run=True)
        assert not preview["can_apply"]
        message = "\n".join(item["reason"] for item in preview["readiness"]["blocked"])
    else:
        with pytest.raises(VaultError, match="Conflicting definitions") as exc:
            pipeline.apply()
        message = str(exc.value)
    for record in registered.values():
        assert record["path"] in message
        assert record["payload"]["source"] in message
        assert record["payload"]["target"] in message
        assert f"relationship: {record['payload']['relationship']}" in message
    assert "status: pending" in message
    assert "revision field" in message
    after = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert after == before


@pytest.mark.parametrize("other_review", ["identical", "pending", "rejected"])
def test_concept_conflict_check_allows_matching_or_deferred_definitions(vault, other_review):
    root, _, _, agent, pipeline = vault
    add_paper(vault)
    add_paper(vault, "PAPER002")
    first = link()
    second = link(source="Literature/Synthetic item — PAPER002.md")
    if other_review != "identical":
        second["target_concept"]["body"] = "A different definition awaiting a decision."
    agent.links = [first, second]
    pipeline.sync()
    registered = proposals(pipeline, "link")
    assert len(registered) == 2
    for pid, record in registered.items():
        status = (
            other_review
            if record["payload"]["source"] == second["source"] and other_review != "identical"
            else "approved"
        )
        decide(root, pipeline.state(), pid, status)

    assert "Concepts/mechanism.md" in pipeline.apply(dry_run=True)["changes"]
    pipeline.apply()
    assert (root / "Concepts/mechanism.md").exists()
    assert sum(r["applied"] for r in proposals(pipeline, "link").values()) == (
        2 if other_review == "identical" else 1
    )
