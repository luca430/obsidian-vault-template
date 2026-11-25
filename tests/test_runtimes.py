from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from conftest import decide, link
from test_pipeline import add_paper, proposals

from research_vault.agents import PROMPT_SUFFIX, SCHEMAS, AuthenticationError, Codex
from research_vault.cli import main
from research_vault.config import ROLES, Config
from research_vault.execution import launch
from research_vault.pipeline import Pipeline
from research_vault.processing import Processor
from research_vault.recipes import VERSIONS, stage_recipe
from research_vault.runtimes import ClaudeCode, LLMRunner
from research_vault.storage import VaultError, digest, file_hash


def mock_runtimes(vault, monkeypatch, transform=None, missing=(), unauthenticated=()):
    root, _, _, scientific_agent, pipeline = vault
    calls = []
    monkeypatch.setattr(
        "research_vault.agents.shutil.which",
        lambda name: (
            None if name in missing or name not in {"codex", "claude"} else f"/synthetic/bin/{name}"
        ),
    )

    def run(command, **kwargs):
        provider = Path(command[0]).stem
        calls.append((provider, command, kwargs))
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, provider + "-cli synthetic-1", "")
        if "--help" in command:
            adapter = Codex if provider == "codex" else ClaudeCode
            return subprocess.CompletedProcess(command, 0, " ".join(adapter.required_flags), "")
        if command[1:3] in (["login", "status"], ["auth", "status"]):
            return subprocess.CompletedProcess(
                command, int(provider in unauthenticated), '{"loggedIn":true}', ""
            )
        if provider == "codex":
            instructions, raw = kwargs["input"].split(PROMPT_SUFFIX, 1)
            payload = json.loads(raw)
        else:
            instructions = Path(command[command.index("--system-prompt-file") + 1]).read_text()
            payload = json.loads(kwargs["input"])
        role = next(
            role for role in ROLES if (root / f"agents/{role}.md").read_text() in instructions
        )
        result = scientific_agent.run(role, payload)
        if transform:
            result = transform(provider, role, result, payload)
        if provider == "codex":
            Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps(result))
            stdout = json.dumps(
                {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}
            )
        else:
            stdout = json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "structured_output": result,
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                    "modelUsage": {"synthetic-model": {"inputTokens": 10}},
                    "total_cost_usd": 0.01,
                }
            )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr("research_vault.agents.subprocess.run", run)
    runner = LLMRunner(pipeline.config)
    return runner, calls


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_both_runtimes_preserve_scientific_schemas_and_approval_boundary(
    vault, monkeypatch, provider
):
    root, _, z, agent, old = vault
    old.config.data["llm"] = {"default_provider": provider}
    runner, calls = mock_runtimes(vault, monkeypatch)
    pipeline = Pipeline(old.config, z, runner)
    add_paper(vault)
    agent.links = [link()]
    report = pipeline.sync()
    assert report["complete"] and report["inference_failures"] == 0
    assert {r["provider"] for r in report["inference_metadata"]} == {provider}
    assert all(r["instructions"]["role"] and r["cli_version"] for r in report["inference_metadata"])
    assert all(r["usage"]["input_tokens"] == 10 for r in report["inference_metadata"])
    pid = next(iter(proposals(pipeline, "link")))
    assert not (root / "Concepts/mechanism.md").exists()
    pipeline.apply()
    assert not (root / "Concepts/mechanism.md").exists()
    decide(root, pipeline.state(), pid)
    pipeline.apply()
    assert (root / "Concepts/mechanism.md").exists()
    assert calls and all(not options.get("shell") for _, _, options in calls)


def test_claude_runs_without_tools_discovery_hooks_mcp_or_resumable_sessions(vault, monkeypatch):
    runner, calls = mock_runtimes(vault, monkeypatch)
    adapter = runner.adapters["claude"]
    assert adapter.run("linking", {"notes": {}}) == {"links": []}
    _, command, options = calls[-1]
    for flag in (
        "--safe-mode",
        "--restricted",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--disable-slash-commands",
    ):
        assert flag in command
    assert command[command.index("--tools") + 1] == ""
    assert command[command.index("--disallowedTools") + 1] == "mcp__*"
    assert json.loads(command[command.index("--mcp-config") + 1]) == {"mcpServers": {}}
    assert json.loads(command[command.index("--settings") + 1])["disableAllHooks"]
    assert command[command.index("--setting-sources") + 1] == ""
    assert command[command.index("--permission-mode") + 1] == "dontAsk"
    assert options["cwd"].is_relative_to(vault[0] / ".pipeline/staging")
    assert "--add-dir" not in command and "--dangerously-skip-permissions" not in command
    assert "minLength" not in command[command.index("--json-schema") + 1]
    assert adapter.last_metadata["resolved_models"] == ["synthetic-model"]
    assert adapter.last_metadata["total_cost_usd"] == 0.01


def test_mixed_provider_roles_and_model_overrides_use_shared_instructions(vault, monkeypatch):
    root, _, z, _, previous = vault
    previous.config.data["codex"] = {"model": "codex-test"}
    previous.config.data["claude"] = {"model": "claude-default"}
    previous.config.data["llm"] = {
        "agents": {
            "linking": {"provider": "claude", "model": "claude-link-test"},
            "maintenance": "claude",
        }
    }
    runner, calls = mock_runtimes(vault, monkeypatch)
    pipeline = Pipeline(previous.config, z, runner)
    add_paper(vault)
    report = pipeline.sync()
    identities = {(r["role"], r["provider"], r["model"]) for r in report["inference_metadata"]}
    assert ("literature", "codex", "codex-test") in identities
    assert ("linking", "claude", "claude-link-test") in identities
    assert ("maintenance", "claude", "claude-default") in identities
    assert report["complete"]
    before = len(calls)
    assert pipeline.sync()["complete"] and len(calls) == before
    assert (root / "agents/linking.md").is_file()
    assert not (root / "agents/claude").exists()


def legacy_recipe(config, role):
    settings = config.data.get("codex", {})
    return digest(
        {
            "algorithm": VERSIONS[role],
            "schema": SCHEMAS[role],
            "global_instructions": file_hash(config.root / "AGENTS.md"),
            "role_instructions": file_hash(config.root / f"agents/{role}.md"),
            "model": {k: settings.get(k) for k in ("model", "executable")},
            "chunking": {k: config.processing(k) for k in ("chunk_chars", "chunk_context_chars")}
            if role in {"literature", "manuscript"}
            else {},
            "upstream_extraction": stage_recipe(config, "extraction")
            if role in {"literature", "manuscript"}
            else None,
        }
    )


def test_codex_legacy_cache_keys_and_envelopes_remain_compatible(vault):
    root, _, _, agent, pipeline = vault
    for role in ROLES:
        assert stage_recipe(pipeline.config, role) == legacy_recipe(pipeline.config, role)
    role, payload, result = "linking", {"notes": {}}, {"links": []}
    key = digest([legacy_recipe(pipeline.config, role), role, payload])
    path = root / f".pipeline/cache/relationship_proposals/{key}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"key": key, "value_hash": digest(result), "value": result}))
    processor = Processor(pipeline.config, agent, "test", lambda *a, **k: None)
    assert processor.run(role, payload) == result
    assert not agent.calls and processor.hits == 1
    assert processor.operations[0]["legacy_cache"]
    assert processor.operations[0]["provider"] == "codex"


def test_provider_switch_reuses_extraction_and_switch_back_reuses_codex_inference(
    vault, monkeypatch
):
    _, _, z, agent, old = vault
    runner, _ = mock_runtimes(vault, monkeypatch)
    import research_vault.processing as processing

    original = processing.pdf
    extracted = []

    def extract(*args, **kwargs):
        extracted.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(processing, "pdf", extract)
    add_paper(vault)
    Pipeline(old.config, z, runner).sync()
    extraction_recipe = stage_recipe(old.config, "extraction")
    next_config = old.config.with_provider("claude")
    second = Pipeline(next_config, z, LLMRunner(next_config)).sync()
    assert second["complete"] and extracted == ["PDF00001"]
    assert stage_recipe(next_config, "extraction") == extraction_recipe
    assert any(r["provider"] == "claude" and not r["cached"] for r in second["inference_metadata"])
    before = len(agent.calls)
    third = Pipeline(old.config, z, LLMRunner(old.config)).sync()
    assert third["complete"] and len(agent.calls) == before
    assert third["cache_hits"] and extracted == ["PDF00001"]


def test_changing_link_provider_does_not_resummarize_sources(vault, monkeypatch):
    _, _, z, agent, old = vault
    runner, _ = mock_runtimes(vault, monkeypatch)
    add_paper(vault)
    pipeline = Pipeline(old.config, z, runner)
    pipeline.sync()
    previous = stage_recipe(old.config, "literature")
    old.config.data["llm"] = {"agents": {"linking": "claude"}}
    before = sum(role == "literature" for role, _ in agent.calls)
    report = pipeline.sync()
    assert report["papers"] == []
    assert sum(role == "literature" for role, _ in agent.calls) == before
    assert stage_recipe(old.config, "literature") == previous
    assert any(
        r["role"] == "linking" and r["provider"] == "claude" for r in report["inference_metadata"]
    )


def test_cli_override_is_run_only_and_overrides_all_role_providers(vault, monkeypatch, capsys):
    root, _, z, _, old = vault
    mock_runtimes(vault, monkeypatch)
    captured = []

    def create(config, progress):
        captured.append(config)
        return Pipeline(config, z, progress=progress)

    monkeypatch.setattr("research_vault.cli.Pipeline", create)
    before = (root / "config.yaml").read_bytes()
    add_paper(vault)
    assert main(["--vault", str(root), "sync", "--provider", "claude"]) == 0
    assert captured[0].provider_override == "claude"
    assert (root / "config.yaml").read_bytes() == before
    assert all(v == "claude" for v in json.loads(capsys.readouterr().out)["providers"].values())


def test_background_launch_forwards_provider_override(vault, monkeypatch):
    commands = []

    def spawn(command, **kwargs):
        commands.append(command)
        return type("Child", (), {"pid": 123})()

    monkeypatch.setattr("research_vault.execution.subprocess.Popen", spawn)
    result = launch(vault[-1].config.with_provider("claude"))
    assert commands[0][-2:] == ["--provider", "claude"]
    assert result["provider_override"] == "claude"


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_unverifiable_evidence_remains_review_required_for_both_providers(
    vault, monkeypatch, provider
):
    root, _, z, _, old = vault
    old.config.data["llm"] = {"default_provider": provider}

    def uncertain(_provider, role, result, payload):
        if role == "literature":
            result["evidence"][0]["quote"] = "Approximate quotation."
        return result

    runner, _ = mock_runtimes(vault, monkeypatch, uncertain)
    add_paper(vault)
    pipeline = Pipeline(old.config, z, runner)
    report = pipeline.sync()
    assert report["complete"] and report["pending_claim_reviews"] == 1
    assert not (root / "Literature/PAPER001.md").exists()
    event = next(r for r in report["inference_metadata"] if r["role"] == "literature")
    assert event["validation_status"] == "review_required"
    assert event["warnings"][0]["evidence"]["source"] == "PDF00001"
    cached = json.loads(
        next((root / ".pipeline/cache/document_summaries").glob("*.json")).read_text()
    )
    assert cached["metadata"]["validation_status"] == "review_required"


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_provider_cannot_self_approve_or_emit_protected_note_syntax(vault, monkeypatch, provider):
    _, _, z, _, old = vault
    old.config.data["llm"] = {"default_provider": provider}

    def malicious(_provider, role, result, payload):
        if role == "linking":
            result["status"] = "approved"
        return result

    runner, _ = mock_runtimes(vault, monkeypatch, malicious)
    add_paper(vault)
    pipeline = Pipeline(old.config, z, runner)
    result = pipeline.sync()
    assert not result["complete"] and result["inference_failures"] == 1
    assert not proposals(pipeline, "link")
    assert any(r["validation_status"] == "failed" for r in result["inference_metadata"])


@pytest.mark.parametrize("fallback", [False, True])
def test_claude_normalizes_structured_object_and_json_text_fallback(vault, monkeypatch, fallback):
    runner, _ = mock_runtimes(vault, monkeypatch)
    envelope = (
        {"result": json.dumps({"links": []})} if fallback else {"structured_output": {"links": []}}
    )
    monkeypatch.setattr(
        "research_vault.runtimes.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(envelope), ""),
    )
    assert runner.adapters["claude"].run("linking", {"notes": {}}) == {"links": []}


@pytest.mark.parametrize(
    "envelope",
    [
        {"is_error": True, "result": "Authentication failed"},
        {"is_error": True, "result": "Execution failed"},
        {"result": "No answer"},
        {"structured_output": {"links": [], "status": "approved"}},
        ["invalid envelope"],
    ],
)
def test_claude_errors_refusals_and_invalid_outputs_fail_closed(vault, monkeypatch, envelope):
    runner, _ = mock_runtimes(vault, monkeypatch)
    monkeypatch.setattr(
        "research_vault.runtimes.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(envelope), ""),
    )
    with pytest.raises(VaultError):
        runner.adapters["claude"].run("linking", {"notes": {}})
    assert runner.adapters["claude"].last_metadata["validation_status"] == "failed"


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_timeout_reports_provider_and_keeps_failure_metadata(vault, monkeypatch, provider):
    runner, _ = mock_runtimes(vault, monkeypatch)

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 1)

    monkeypatch.setattr("research_vault.runtimes.subprocess.run", timeout)
    with pytest.raises(VaultError, match="execution failed"):
        runner.run_provider("linking", {"notes": {}}, provider)
    assert runner.last_metadata["provider"] == provider
    assert runner.last_metadata["validation_status"] == "failed"


def test_doctor_optional_missing_provider_is_not_blocking_but_required_one_is(vault, monkeypatch):
    runner, _ = mock_runtimes(vault, monkeypatch, missing={"claude"})
    pipeline = Pipeline(vault[-1].config, vault[2], runner)
    report = pipeline.doctor()
    assert not report["blocking_errors"]
    assert report["providers"]["codex"]["version"] == "codex-cli synthetic-1"
    assert report["providers"]["codex"]["authentication"] == "ok"
    assert not report["providers"]["claude"]["available"]
    pipeline.config.data["llm"] = {"agents": {"linking": "claude"}}
    assert pipeline.doctor()["blocking_errors"]


def test_provider_authentication_failure_is_explicit(vault, monkeypatch):
    runner, _ = mock_runtimes(vault, monkeypatch, unauthenticated={"claude"})
    vault[-1].config.data["llm"] = {"default_provider": "claude"}
    with pytest.raises(AuthenticationError, match="Claude Code authentication"):
        runner.authenticate(["literature"])
    assert runner.diagnostics()["claude"]["authentication"] == "failed"


def test_doctor_reports_unsafe_or_obsolete_cli_flags(vault, monkeypatch):
    runner, _ = mock_runtimes(vault, monkeypatch)
    original = subprocess.run

    def obsolete(command, **kwargs):
        if Path(command[0]).name == "claude" and "--help" in command:
            return subprocess.CompletedProcess(command, 0, "--print --tools", "")
        return original(command, **kwargs)

    monkeypatch.setattr("research_vault.runtimes.subprocess.run", obsolete)
    record = runner.diagnostics()["claude"]
    assert "--safe-mode" in record["configuration_error"]


@pytest.mark.parametrize(
    "provider,entry",
    [("codex", "@openai/codex/bin/codex.js"), ("claude", "@anthropic-ai/claude-code/cli.js")],
)
def test_windows_npm_shims_use_node_and_never_a_shell(
    vault, monkeypatch, tmp_path, provider, entry
):
    monkeypatch.setattr("research_vault.agents.is_wsl", lambda: False)
    target = tmp_path / "node_modules" / entry
    target.parent.mkdir(parents=True)
    target.write_text("Synthetic entry")
    adapter = Codex(vault[-1].config) if provider == "codex" else ClaudeCode(vault[-1].config)
    monkeypatch.setattr(adapter, "available", lambda: str(tmp_path / f"{provider}.cmd"))
    monkeypatch.setattr("research_vault.agents.shutil.which", lambda name: "node.exe")
    assert adapter.command_prefix() == ["node.exe", str(target)]


def test_wsl_requires_linux_claude_even_when_windows_cli_is_on_path(vault, monkeypatch):
    adapter = ClaudeCode(vault[-1].config)
    monkeypatch.setattr("research_vault.agents.is_wsl", lambda: True)
    monkeypatch.setattr(adapter, "available", lambda: "/mnt/c/npm/claude.exe")
    with pytest.raises(VaultError, match="Linux Claude Code"):
        adapter.command_prefix()


@pytest.mark.parametrize(
    "llm",
    [
        {"default_provider": "missing"},
        {"agents": {"missing-role": "claude"}},
        {"agents": {"literature": {"provider": "missing"}}},
        {"agents": {"literature": {"model": 42}}},
        {"evaluation": {"enabled": "yes"}},
        {"evaluation": {"enabled": True}},
    ],
)
def test_invalid_llm_configuration_is_rejected(vault, llm):
    root = vault[0]
    data = deepcopy(vault[-1].config.data)
    data["llm"] = llm
    (root / "config.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(VaultError):
        Config.load(root)


def test_evaluation_is_opt_in_private_and_cannot_create_secondary_links(vault, monkeypatch):
    root, _, z, _, old = vault

    def comparison(provider, role, result, payload):
        if provider == "claude" and role == "linking":
            return {"links": [link()]}
        return result

    runner, calls = mock_runtimes(vault, monkeypatch, comparison)
    add_paper(vault)
    pipeline = Pipeline(old.config, z, runner)
    assert pipeline.sync()["complete"]
    assert not any(provider == "claude" for provider, _, _ in calls)
    old.config.data["llm"] = {"evaluation": {"enabled": True, "provider": "claude"}}
    # Evaluation applies to scheduled jobs; --full explicitly reconsiders unchanged documents.
    result = pipeline.sync(full=True)
    assert result["complete"] and not result["evaluation_failures"]
    assert any(r.get("evaluation_only") for r in result["inference_metadata"])
    assert list((root / ".pipeline/cache/evaluations").glob("*.json"))
    assert not proposals(pipeline, "link")
    assert not (root / "Concepts/mechanism.md").exists()


def test_optional_evaluation_failure_does_not_fail_primary_science(vault, monkeypatch):
    _, _, z, _, old = vault
    old.config.data["llm"] = {"evaluation": {"enabled": True, "provider": "claude"}}

    def broken(provider, role, result, payload):
        return {"invalid": "comparison"} if provider == "claude" else result

    runner, _ = mock_runtimes(vault, monkeypatch, broken)
    add_paper(vault)
    report = Pipeline(old.config, z, runner).sync()
    assert report["complete"] and report["evaluation_failures"]
    assert report["inference_failures"] == 0


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_fabricated_provenance_is_not_cached_as_valid_for_either_runtime(
    vault, monkeypatch, provider
):
    _, _, z, _, old = vault
    old.config.data["llm"] = {"default_provider": provider}

    def missing(_provider, role, result, payload):
        if role == "literature":
            result["evidence"][0]["source"] = "ABSENT01"
        return result

    runner, _ = mock_runtimes(vault, monkeypatch, missing)
    add_paper(vault)
    pipeline = Pipeline(old.config, z, runner)
    report = pipeline.sync()
    assert not report["complete"] and report["inference_failures"] == 1
    assert "PAPER001" in report["failed"]
    assert not list((vault[0] / ".pipeline/cache/document_summaries").glob("*.json"))
    assert not proposals(pipeline, "claim")
    event = next(r for r in report["inference_metadata"] if r["validation_status"] == "failed")
    assert event["provider"] == provider and event["instructions"]["role"]


def test_cache_metadata_is_integrity_checked_and_reused_usage_is_historical(vault, monkeypatch):
    runner, _ = mock_runtimes(vault, monkeypatch)
    config = vault[-1].config
    first = Processor(config, runner, "test", lambda *a, **k: None)
    first.run("linking", {"notes": {}})
    second = Processor(config, runner, "test", lambda *a, **k: None)
    second.run("linking", {"notes": {}})
    assert second.calls == 0 and second.operations[0]["cached"]
    assert second.operations[0]["usage"] == first.operations[0]["usage"]
    path = next((vault[0] / ".pipeline/cache/relationship_proposals").glob("*.json"))
    cached = json.loads(path.read_text())
    cached["metadata"]["provider"] = "claude"
    path.write_text(json.dumps(cached))
    with pytest.raises(VaultError, match="cache is damaged"):
        second.run("linking", {"notes": {}})


def test_optional_evaluation_with_legacy_injected_runner_reports_limitation(vault):
    _, _, z, agent, pipeline = vault
    pipeline.config.data["llm"] = {"evaluation": {"enabled": True, "provider": "claude"}}
    add_paper(vault)
    report = Pipeline(pipeline.config, z, agent).sync()
    assert report["complete"] and report["evaluation_failures"]
    assert "secondary provider execution" in report["evaluation_failures"][0]["error"]
