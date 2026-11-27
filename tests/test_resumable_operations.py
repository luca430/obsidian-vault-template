import json
from copy import deepcopy

import pytest
from test_pipeline import add_paper

from research_vault.config import Config
from research_vault.inference_errors import (
    AllowanceExhausted,
    AuthenticationError,
    ContextExhausted,
    InvalidModel,
    InvalidOutput,
    OperationDeferred,
    RateLimited,
    provider_error,
)
from research_vault.pipeline import Pipeline
from research_vault.processing import Processor
from research_vault.recipes import content_recipe, reasoning_recipe, stage_recipe
from research_vault.settings_service import inspect_settings, save_settings
from research_vault.storage import VaultError


@pytest.mark.parametrize("startup", [False, True])
def test_allowance_mid_sync_resumes_first_unfinished_without_repeating_success(vault, startup):
    root, _, z, agent, pipeline = vault
    for key in ("PAPER001", "PAPER002", "PAPER003"):
        add_paper(vault, key)
    exhausted = False

    def limit(role, payload):
        nonlocal exhausted
        if role == "literature" and payload.get("item_key") == "PAPER002" and not exhausted:
            exhausted = True
            raise AllowanceExhausted("codex: usage allowance exhausted")

    agent.callback = limit
    first = pipeline.sync()
    assert first["status"] == "incomplete_resumable"
    assert first["provider_interruptions"] == {"codex": "codex: usage allowance exhausted"}
    assert pipeline.state()["papers"]["PAPER001"]["status"] == "successful"
    assert pipeline.state()["papers"]["PAPER002"]["status"] == "deferred"
    assert pipeline.state()["papers"]["PAPER003"]["status"] == "deferred"
    assert first["task_status"]["deferred"] >= 2
    assert len([p for role, p in agent.calls if role == "literature"]) == 2
    agent.callback = None
    resumed = Pipeline(pipeline.config, z, agent).sync(startup=startup)
    assert resumed["complete"]
    assert (
        len(
            [
                p
                for role, p in agent.calls
                if role == "literature" and p.get("item_key") == "PAPER001"
            ]
        )
        == 1
    )
    assert (
        len(
            [
                p
                for role, p in agent.calls
                if role == "literature" and p.get("item_key") == "PAPER002"
            ]
        )
        == 2
    )
    assert (
        len(
            [
                p
                for role, p in agent.calls
                if role == "literature" and p.get("item_key") == "PAPER003"
            ]
        )
        == 1
    )
    before = len(agent.calls)
    assert Pipeline(pipeline.config, z, agent).sync()["inference_operations"] == 0
    assert len(agent.calls) == before


def processor(vault):
    return Processor(
        vault[-1].config, vault[3], reasoning_recipe(vault[-1].config), lambda *a, **k: None
    )


def source():
    return {
        "pages": [
            {"source": "PDF00001", "locator": "page 1", "text": "Synthetic scientific evidence."}
        ]
    }


def test_validated_output_survives_death_before_completion_receipt(vault, monkeypatch):
    p = processor(vault)
    original = p.tasks.update

    def interrupt(key, status, **details):
        if status == "completed":
            raise KeyboardInterrupt()
        return original(key, status, **details)

    monkeypatch.setattr(p.tasks, "update", interrupt)
    with pytest.raises(KeyboardInterrupt):
        p.summarize("literature", source())
    assert len(vault[3].calls) == 1
    resumed = processor(vault)
    assert all(r["status"] != "running" for r in resumed.tasks.records.values())
    resumed.summarize("literature", source())
    assert len(vault[3].calls) == 1
    assert all(r["status"] == "completed" for r in resumed.tasks.records.values())


def test_corrupt_partial_cached_output_recomputes_only_that_task(vault):
    p = processor(vault)
    p.summarize("literature", source())
    record = next(r for r in p.tasks.records.values() if r["kind"] == "inference")
    (vault[0] / record["cache_path"]).write_text("{partial")
    resumed = processor(vault)
    resumed.summarize("literature", source())
    assert resumed.calls == 1
    assert next(iter(resumed.tasks.records.values()))["status"] == "completed"


@pytest.mark.parametrize(
    "detail,kind",
    [
        ("insufficient_quota", AllowanceExhausted),
        ("You have hit your usage limit", AllowanceExhausted),
        ("429 rate_limit_error", RateLimited),
        ("context_length_exceeded", ContextExhausted),
        ("401 unauthorized", AuthenticationError),
        ("model_not_found", InvalidModel),
        ("output truncated max_tokens", InvalidOutput),
    ],
)
def test_provider_failures_are_distinct(detail, kind):
    assert isinstance(provider_error(detail, "codex"), kind)


def test_rate_limit_waits_for_next_eligible_sync_without_polling(vault):
    p = processor(vault)
    vault[3].callback = lambda *a: (_ for _ in ()).throw(RateLimited("temporary", retry_after=3600))
    with pytest.raises(RateLimited):
        p.summarize("literature", source())
    before = len(vault[3].calls)
    vault[3].callback = None
    with pytest.raises(OperationDeferred):
        processor(vault).summarize("literature", source())
    assert len(vault[3].calls) == before
    resumed = Processor(p.config, vault[3], p.recipe, lambda *a, **k: None, retry=True)
    resumed.summarize("literature", source())
    assert len(vault[3].calls) == before + 1


def test_agent_configuration_changes_do_not_regenerate_completed_notes(vault):
    root, _, z, agent, pipeline = vault
    add_paper(vault)
    pipeline.sync()
    before = {n: p.read_bytes() for p in (root / "Literature").glob("*.md") for n in [p.name]}
    old_recipe = stage_recipe(pipeline.config, "literature")
    old_content = content_recipe(pipeline.config, "literature")
    pipeline.config.data["llm"] = {
        "defaults": {"reasoning_effort": "high"},
        "agents": {
            "literature": {
                "provider": "claude",
                "model": "example-model",
                "timeout_seconds": 120,
                "max_input_chars": 120000,
                "output_reserve_chars": 20000,
                "retry_policy": {"max_attempts": 5},
            }
        },
    }
    assert old_recipe != stage_recipe(pipeline.config, "literature")
    assert old_content == content_recipe(pipeline.config, "literature")
    count = len(agent.calls)
    report = Pipeline(pipeline.config, z, agent).sync()
    assert report["inference_operations"] == 0 and len(agent.calls) == count
    assert before == {p.name: p.read_bytes() for p in (root / "Literature").glob("*.md")}
    add_paper(vault, "PAPER002")
    new = Pipeline(pipeline.config, z, agent).sync()
    assert new["papers"] == ["PAPER002"]


def test_global_defaults_override_provider_defaults_and_merge_retry_policy(vault):
    config = vault[-1].config
    config.data["codex"] = {"timeout_seconds": 600, "retry_policy": {"max_attempts": 3}}
    config.data["llm"] = {
        "defaults": {
            "timeout_seconds": 200,
            "reasoning_effort": "high",
            "retry_policy": {"retry_after_seconds": 60},
        },
        "agents": {
            "linking": {"model": "precise-model", "timeout_seconds": 100, "reasoning_effort": "low"}
        },
    }
    provider, settings = config.runtime("linking")
    assert provider == "codex"
    assert settings["timeout_seconds"] == 100 and settings["reasoning_effort"] == "low"
    assert settings["retry_policy"] == {"max_attempts": 3, "retry_after_seconds": 60}
    assert config.runtime("literature")[1]["timeout_seconds"] == 200


@pytest.mark.parametrize(
    "profile",
    [
        {"reasoning_effort": "invented"},
        {"reasoning_effort": []},
        {"timeout_seconds": 0},
        {"max_input_chars": None},
        {"retry_policy": {"max_attempts": 0}},
        {"provider": "claude", "reasoning_effort": "minimal"},
        {"provider": "codex", "max_output_tokens": 100},
        {"model": "model --unsafe"},
    ],
)
def test_invalid_agent_settings_rejected(vault, profile):
    root = vault[0]
    data = deepcopy(vault[-1].config.data)
    data["llm"] = {"agents": {"linking": profile}}
    with pytest.raises(VaultError):
        Config.load(root, data_override=data)


def test_catalog_checks_model_availability_and_effort(vault):
    data = deepcopy(vault[-1].config.data)
    data["llm"] = {
        "model_catalog": {"codex": {"known": {"reasoning_efforts": ["low", "high"]}}},
        "agents": {"linking": {"model": "missing"}},
    }
    with pytest.raises(VaultError, match="unavailable"):
        Config.load(vault[0], data_override=data)
    data["llm"]["agents"]["linking"] = {"model": "known", "reasoning_effort": "max"}
    with pytest.raises(VaultError, match="does not support"):
        Config.load(vault[0], data_override=data)


def test_settings_service_validates_and_preserves_unrelated_configuration(vault, monkeypatch):
    from test_runtimes import mock_runtimes

    mock_runtimes(vault, monkeypatch)
    root = vault[0]
    path = root / "config.yaml"
    before = path.read_text() + "\n# Keep personal config comments\n"
    path.write_text(before)
    config = Config.load(root)
    report = inspect_settings(config)
    request = {
        "plan_token": report["plan_token"],
        "defaults": {"reasoning_effort": "high"},
        "agents": {"linking": {"provider": "claude", "model": "sonnet", "reasoning_effort": "low"}},
    }
    saved = save_settings(config, request)
    assert saved["resolved_agents"]["linking"]["provider"] == "claude"
    assert "# Keep personal config comments" in path.read_text()
    with pytest.raises(VaultError, match="Configuration changed"):
        save_settings(config, request)
    current = Config.load(root)
    bad = {**request, "plan_token": saved["plan_token"], "defaults": {"timeout_seconds": -1}}
    unchanged = path.read_bytes()
    with pytest.raises(VaultError):
        save_settings(current, bad)
    assert path.read_bytes() == unchanged


def test_completed_task_retains_recorded_configuration_and_usage_on_resume(vault):
    p = processor(vault)
    p.summarize("literature", source())
    records = deepcopy(p.tasks.records)
    vault[-1].config.data["llm"] = {
        "agents": {"literature": {"model": "new-model", "reasoning_effort": "high"}}
    }
    resumed = processor(vault)
    resumed.summarize("literature", source())
    assert resumed.calls == 0 and resumed.hits == 1
    assert (
        next(iter(resumed.tasks.records.values()))["recipe"]
        == next(iter(records.values()))["recipe"]
    )


def test_other_provider_continues_after_allowance_exhaustion(vault):
    from research_vault.inference_errors import AllowanceExhausted

    root, _, _, agent, pipeline = vault
    pipeline.config.data["llm"] = {"agents": {"synthesis": "claude"}}

    class Routed:
        def run(self, role, payload):
            if role == "linking":
                raise AllowanceExhausted("codex allowance exhausted")
            return agent.run(role, payload)

        def run_provider(self, role, payload, provider):
            return self.run(role, payload)

    p = Processor(
        pipeline.config, Routed(), reasoning_recipe(pipeline.config), lambda *a, **k: None
    )
    payload = {"notes": {"My Notes/a.md": "Scientific evidence."}}
    with pytest.raises(AllowanceExhausted):
        p.run("linking", payload)
    assert p.run("synthesis", payload) == {"revisions": []}
    with pytest.raises(OperationDeferred):
        p.run("maintenance", payload)
    assert [role for role, _ in agent.calls] == ["synthesis"]


def test_startup_inference_budget_is_deferred_and_resumes(vault):
    _, _, _, agent, pipeline = vault
    add_paper(vault)
    pipeline.config.data["processing"] = {"startup_max_operations": 1}
    first = pipeline.sync(startup=True)
    assert first["status"] == "incomplete_resumable"
    assert first["inference_operations"] <= 1
    assert pipeline.sync()["complete"]
    assert sum(role == "literature" for role, _ in agent.calls) == 1


def test_provider_adapters_translate_effort_timeout_and_limits(vault, monkeypatch):
    from test_runtimes import mock_runtimes

    config = vault[-1].config
    config.data["llm"] = {
        "agents": {
            "linking": {
                "provider": "codex",
                "model": "test-specific",
                "reasoning_effort": "high",
                "timeout_seconds": 123,
            },
            "synthesis": {
                "provider": "claude",
                "model": "claude-specific",
                "reasoning_effort": "low",
                "max_output_tokens": 2048,
                "timeout_seconds": 234,
            },
        }
    }
    runner, calls = mock_runtimes(vault, monkeypatch)
    runner.run("linking", {"notes": {}})
    runner.run("synthesis", {"notes": {}})
    inference = [(provider, args, kwargs) for provider, args, kwargs in calls if "input" in kwargs]
    assert 'model_reasoning_effort="high"' in inference[0][1]
    assert inference[0][2]["timeout"] == 123
    args = inference[1][1]
    env = inference[1][2]["env"]
    assert args[args.index("--effort") + 1] == "low"
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "2048"
    assert env["CLAUDE_CODE_MAX_RETRIES"] == "0" and env["MAX_STRUCTURED_OUTPUT_RETRIES"] == "1"
    assert inference[1][2]["timeout"] == 234
    settings = json.loads(args[args.index("--settings") + 1])
    assert settings["fallbackModel"] == [] and settings["autoContinueAtUsageLimit"] is False
    assert "--fallback-model" not in args


def test_model_substitution_is_rejected_before_output_is_cached(vault, monkeypatch):
    import subprocess

    from test_runtimes import mock_runtimes

    import research_vault.runtimes as runtimes

    config = vault[-1].config
    config.data["llm"] = {"agents": {"linking": {"provider": "claude", "model": "claude-exact"}}}
    runner, _ = mock_runtimes(vault, monkeypatch)
    original = runtimes.run_process

    def substitute(*args, **kwargs):
        result = original(*args, **kwargs)
        if "input" in kwargs:
            envelope = json.loads(result.stdout)
            envelope["modelUsage"] = {"claude-other": {"inputTokens": 1}}
            return subprocess.CompletedProcess(
                result.args, result.returncode, json.dumps(envelope), result.stderr
            )
        return result

    monkeypatch.setattr(runtimes, "run_process", substitute)
    with pytest.raises(InvalidModel, match="different model"):
        Processor(config, runner, reasoning_recipe(config), lambda *a, **k: None).run(
            "linking", {"notes": {}}
        )
    assert not list((vault[0] / ".pipeline/cache/relationship_proposals").glob("*.json"))


def test_repeated_bounded_startups_eventually_finish_deferred_graph(vault):
    _, _, _, agent, pipeline = vault
    add_paper(vault)
    pipeline.config.data["processing"] = {"startup_max_operations": 1}
    for _ in range(12):
        report = pipeline.sync(startup=True)
        assert report["inference_operations"] <= 1
        if report["complete"]:
            break
    assert report["complete"]
    assert sum(role == "literature" for role, _ in agent.calls) == 1
    assert sum(role == "linking" for role, _ in agent.calls) == 1


def test_agent_retry_policy_controls_document_retries(vault):
    _, _, _, agent, pipeline = vault
    add_paper(vault)
    pipeline.config.data["llm"] = {"agents": {"literature": {"retry_policy": {"max_attempts": 4}}}}
    agent.fail_role = "literature"
    for _ in range(4):
        pipeline.sync()
    assert sum(role == "literature" for role, _ in agent.calls) == 4
    pipeline.sync()
    assert sum(role == "literature" for role, _ in agent.calls) == 4


def test_corrected_profile_resumes_failed_task_without_full(vault):
    _, _, _, agent, pipeline = vault
    add_paper(vault)
    pipeline.config.data["llm"] = {
        "agents": {"literature": {"model": "bad-model", "retry_policy": {"max_attempts": 1}}}
    }
    agent.fail_role = "literature"
    pipeline.sync()
    pipeline.sync()
    assert sum(role == "literature" for role, _ in agent.calls) == 1
    agent.fail_role = None
    pipeline.config.data["llm"]["agents"]["literature"]["model"] = "corrected-model"
    report = pipeline.sync()
    assert report["complete"]
    assert sum(role == "literature" for role, _ in agent.calls) == 2


def test_context_budget_failure_has_durable_typed_receipt_without_request(vault):
    vault[-1].config.data["codex"] = {"max_input_chars": 1000}
    p = processor(vault)
    with pytest.raises(ContextExhausted):
        p.run("literature", source())
    record = next(iter(p.tasks.records.values()))
    assert record["status"] == "failed" and record["error_type"] == "context_exhausted"
    assert p.usage_by_agent()["literature"]["requests"] == 0
    assert not vault[3].calls


def test_catalog_rejects_unsupported_output_token_cap(vault):
    data = deepcopy(vault[-1].config.data)
    data["llm"] = {
        "model_catalog": {
            "claude": {"known": {"reasoning_efforts": ["high"], "max_output_tokens": 1000}}
        },
        "agents": {"linking": {"provider": "claude", "model": "known", "max_output_tokens": 2000}},
    }
    with pytest.raises(VaultError, match="output-token cap"):
        Config.load(vault[0], data_override=data)


def test_legacy_recipe_upgrade_retains_notes_but_detects_changed_instructions(vault):
    from research_vault.storage import digest

    root, _, _, agent, pipeline = vault
    add_paper(vault)
    pipeline.sync()
    state = pipeline.state()
    state.pop("recipe_policy")
    state["papers"]["PAPER001"]["summary_recipe"] = stage_recipe(
        pipeline.config, "literature", legacy=True
    )
    for record in state["concept_registry"]["documents"].values():
        record["recipe"] = stage_recipe(pipeline.config, "concepts", legacy=True)
    state["graph_recipe"] = digest(
        [
            stage_recipe(pipeline.config, role, legacy=True)
            for role in ("concepts", "linking", "synthesis", "maintenance", "retrieval")
        ]
    )
    pipeline.save(state)
    assert pipeline.sync()["inference_operations"] == 0
    state = pipeline.state()
    state.pop("recipe_policy")
    state["papers"]["PAPER001"]["summary_recipe"] = stage_recipe(
        pipeline.config, "literature", legacy=True
    )
    pipeline.save(state)
    instructions = root / "agents/literature.md"
    instructions.write_text(instructions.read_text() + "\nNew scientific extraction instruction.\n")
    previous = len(agent.calls)
    assert pipeline.sync()["inference_operations"] > 0
    assert any(role == "literature" for role, _ in agent.calls[previous:])


def test_rate_limit_cooldown_resumes_even_with_one_attempt_policy(vault):
    _, _, _, agent, pipeline = vault
    add_paper(vault)
    pipeline.config.data["llm"] = {"agents": {"literature": {"retry_policy": {"max_attempts": 1}}}}

    def limit(role, payload):
        if role == "literature":
            raise RateLimited("temporary rate limit", retry_after=3600)

    agent.callback = limit
    first = pipeline.sync()
    assert first["complete"] is False
    assert pipeline.state()["papers"]["PAPER001"]["status"] == "deferred"
    agent.callback = None
    assert pipeline.sync()["inference_operations"] == 0
    assert pipeline.sync(retry=True)["complete"]


@pytest.mark.parametrize(
    "detail,error",
    [
        ("You've hit your limit; resets tomorrow", AllowanceExhausted),
        ("error_max_structured_output_retries", InvalidOutput),
    ],
)
def test_claude_error_array_is_classified_without_result_text(vault, monkeypatch, detail, error):
    import subprocess

    from test_runtimes import mock_runtimes

    import research_vault.runtimes as runtimes

    config = vault[-1].config
    config.data["llm"] = {"agents": {"linking": "claude"}}
    runner, _ = mock_runtimes(vault, monkeypatch)
    original = runtimes.run_process

    def failure(*args, **kwargs):
        if "input" in kwargs:
            return subprocess.CompletedProcess(
                args[0],
                1,
                json.dumps(
                    {"is_error": True, "subtype": "error_during_execution", "errors": [detail]}
                ),
                "",
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(runtimes, "run_process", failure)
    with pytest.raises(error):
        Processor(config, runner, reasoning_recipe(config), lambda *a, **k: None).run(
            "linking", {"notes": {}}
        )
