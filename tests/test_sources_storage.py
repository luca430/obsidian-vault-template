from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import write_pdf

from research_vault.agents import Codex, validate_output, validate_summary
from research_vault.config import Config
from research_vault.sources import latex, pdf
from research_vault.storage import (
    VaultError,
    commit,
    inside,
    lock,
    recover,
    replace_section,
)


def test_pdf_page_provenance_and_blank_page_failure(tmp_path):
    source = write_pdf(tmp_path / "test.pdf", "Page with scientific content.")
    pages = pdf(source, "PDF00001")
    assert pages[0]["source"] == "PDF00001"
    assert pages[0]["locator"] == "page 1"
    import fitz

    doc = fitz.open()
    doc.new_page()
    empty = tmp_path / "blank.pdf"
    doc.save(empty)
    doc.close()
    with pytest.raises(VaultError, match="OCR"):
        pdf(empty, "EMPTY001")
    with pytest.raises(VaultError, match="Unreadable PDF"):
        pdf(tmp_path / "missing.pdf", "MISSING1")


def test_latex_include_cannot_escape_authorized_roots(vault):
    _, source, _, _, pipeline = vault
    outside = source.parent / "private.tex"
    outside.write_text("Private source.")
    main = source / "main.tex"
    main.write_text("\\input{../private}")
    with pytest.raises(VaultError, match="outside configured"):
        latex(pipeline.config, main)
    assert outside.read_text() == "Private source."


def test_latex_cycle_and_dynamic_includes_fail_explicitly(vault):
    _, source, _, _, pipeline = vault
    main = source / "main.tex"
    main.write_text("\\input{child}")
    (source / "child.tex").write_text("\\include{main}")
    with pytest.raises(VaultError, match="Cyclic"):
        latex(pipeline.config, main)
    main.write_text("\\input{\\someMacro}")
    with pytest.raises(VaultError, match="Dynamic"):
        latex(pipeline.config, main)
    main.write_text("\\input child.tex")
    with pytest.raises(VaultError, match="literal braced"):
        latex(pipeline.config, main)


def test_lock_exclusion_and_release(vault):
    root, *_ = vault
    with lock(root):
        with pytest.raises(VaultError, match="Another pipeline"):
            with lock(root):
                pytest.fail("Concurrent lock must fail")
    with lock(root):
        assert True


def test_transaction_rollback_on_write_failure(vault, monkeypatch):
    root, *_ = vault
    original = root / "Literature/original.md"
    original.write_text("Old bytes")
    import research_vault.storage as storage

    real = storage.atomic

    def fail(path, value):
        if path.name == "fail.md":
            raise OSError("Synthetic storage failure")
        real(path, value)

    monkeypatch.setattr(storage, "atomic", fail)
    with pytest.raises(OSError):
        commit(root, {"Literature/original.md": "Changed bytes", "Literature/fail.md": "New bytes"})
    assert original.read_text() == "Old bytes"
    assert not (root / ".pipeline/transaction.json").exists()


def test_recovery_after_abrupt_interruption(vault):
    root, *_ = vault
    backup = root / ".pipeline/rollback/0"
    backup.parent.mkdir(parents=True)
    backup.write_text("Before interruption")
    note = root / "Literature/test.md"
    note.write_text("Partial write")
    (root / "Concepts/new.md").write_text("Uncommitted creation")
    (root / ".pipeline/transaction.json").write_text(
        json.dumps({"Literature/test.md": ".pipeline/rollback/0", "Concepts/new.md": None})
    )
    assert recover(root)
    assert note.read_text() == "Before interruption"
    assert not (root / "Concepts/new.md").exists()
    assert not recover(root)


def test_paths_symlinks_and_marker_ownership(vault, tmp_path):
    root, *_ = vault
    with pytest.raises(VaultError):
        inside(root, "../escape.md")
    try:
        (root / "Concepts/escape.md").symlink_to(tmp_path / "elsewhere.md")
    except OSError:
        pytest.skip("Symlink creation is not permitted on this host")
    with pytest.raises(VaultError, match="Symlink"):
        inside(root, "Concepts/escape.md")
    with pytest.raises(VaultError, match="markers"):
        replace_section("User note without markers", "managed", "Do not replace")


def test_evidence_must_match_actual_source():
    result = {
        "title": "Title",
        "summary": "Actual text only.",
        "evidence": [{"source": "PDF00001", "locator": "page 2", "quote": "Invented"}],
    }
    with pytest.raises(VaultError, match="absent from the processed"):
        validate_summary(
            result, [{"source": "PDF00001", "locator": "page 1", "text": "Actual text."}]
        )
    with pytest.raises(VaultError, match="Invalid linking output"):
        validate_output("linking", {"links": [], "status": "approved"})


def test_codex_is_noninteractive_staged_and_read_only(vault, monkeypatch):
    root, *_ = vault
    codex = Codex(vault[-1].config)
    monkeypatch.setattr(codex, "available", lambda: "codex")
    observed = {}

    def run(command, **kwargs):
        observed.update(command=command, **kwargs)
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(json.dumps({"links": []}))

        class Result:
            returncode = 0
            stderr = ""

        return Result()

    monkeypatch.setattr("research_vault.agents.subprocess.run", run)
    assert codex.run("linking", {"notes": {}}) == {"links": []}
    command = observed["command"]
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "--ephemeral" in command
    assert "--disable" in command and "shell_tool" in command
    assert "UNTRUSTED SOURCE DATA" in observed["input"]
    assert (root / "agents/linking.md").read_text() in observed["input"]
    assert (root / "AGENTS.md").read_text() in observed["input"]
    assert list((root / ".pipeline/staging").rglob("schema.json"))


def test_input_limit_fails_without_truncation(vault):
    config = vault[-1].config
    config.data["codex"] = {"max_input_chars": 1000}
    with pytest.raises(VaultError, match="no truncation"):
        Codex(config).run("linking", {"notes": {"n": "x" * 1001}})


def test_standard_npm_batch_shim_uses_node_without_shell(vault, monkeypatch, tmp_path):
    prefix = tmp_path / "npm"
    entry = prefix / "node_modules/@openai/codex/bin/codex.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("// Synthetic entry only, never executed.")
    codex = Codex(vault[-1].config)
    monkeypatch.setattr(codex, "available", lambda: str(prefix / "codex.cmd"))
    monkeypatch.setattr("research_vault.agents.shutil.which", lambda name: "node.exe")
    assert codex.command_prefix() == ["node.exe", str(entry)]


def test_codex_failure_and_invalid_json_are_explicit(vault, monkeypatch):
    codex = Codex(vault[-1].config)
    monkeypatch.setattr(codex, "available", lambda: "codex")

    class Result:
        returncode = 1
        stderr = "Synthetic failure"

    monkeypatch.setattr("research_vault.agents.subprocess.run", lambda *a, **kw: Result())
    with pytest.raises(VaultError, match=r"failed \(exit 1\)"):
        codex.run("linking", {})

    def invalid(command, **kwargs):
        Path(command[command.index("--output-last-message") + 1]).write_text("Not JSON")
        result = Result()
        result.returncode = 0
        return result

    monkeypatch.setattr("research_vault.agents.subprocess.run", invalid)
    with pytest.raises(VaultError, match="invalid JSON"):
        codex.run("linking", {})


def test_restore_verified_deletion_backup(vault):
    import importlib.util

    from conftest import REPO, decide
    from test_pipeline import add_paper, git_init, proposals

    root, _, z, _, pipeline = vault
    add_paper(vault)
    pipeline.sync()
    before = (root / "Literature/PAPER001.md").read_bytes()
    z.items.clear()
    pipeline.sync()
    git_init(root)
    decide(root, pipeline.state(), next(iter(proposals(pipeline, "removal"))), decision="delete")
    result = pipeline.apply()
    spec = importlib.util.spec_from_file_location(
        "restore_backup", REPO / "scripts/restore_backup.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.restore(root, root / result["backup"])
    assert (root / "Literature/PAPER001.md").read_bytes() == before
    assert pipeline.state()["papers"]["PAPER001"]["disposition"] == "absent"


def test_config_rejects_invalid_types_and_remote_zotero(vault):
    import yaml

    root = vault[0]
    for value in [
        {"zotero": []},
        {"zotero": {"collection_key": "COLLECT1", "base_url": "https://remote.example/api/"}},
        {"zotero": {"collection_key": "COLLECT1"}, "attachments": {"overrides": []}},
    ]:
        (root / "config.yaml").write_text(yaml.safe_dump(value))
        with pytest.raises(VaultError):
            Config.load(root)


def test_internal_markdown_links_in_removal_impact_and_diagnostics():
    from research_vault.notes import diagnostics, impact

    notes = {
        "Literature/PAPER001.md": "Paper.",
        "My Notes/idea.md": "Hypothesis [paper](../Literature/PAPER001.md).",
        "Concepts/idea.md": "A [missing](missing.md) and [external](https://example.invalid/).",
    }
    assert impact("Literature/PAPER001.md", notes)["incoming"] == ["My Notes/idea.md"]
    problems = diagnostics(notes)
    assert any("missing.md" in r["reason"] for r in problems)
    assert not any("example.invalid" in r["reason"] for r in problems)
