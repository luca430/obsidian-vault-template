"""Login-aware, provider-specific picker behavior without live services or inference."""

import json

import pytest
from test_runtimes import mock_runtimes

from research_vault.cli import main
from research_vault.config import Config
from research_vault.provider_settings import model_choices, provider_choices
from research_vault.settings_service import inspect_settings, save_settings
from research_vault.storage import VaultError


@pytest.fixture
def catalog(vault, tmp_path, monkeypatch):
    home = tmp_path / "codex-metadata"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    (home / "models_cache.json").write_text(
        json.dumps(
            {
                "models": [
                    {
                        "slug": "precise-model",
                        "display_name": "Precise",
                        "visibility": "list",
                        "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}],
                    },
                    {
                        "slug": "light-model",
                        "visibility": "list",
                        "supported_reasoning_levels": [{"effort": "low"}],
                    },
                    {
                        "slug": "hidden-model",
                        "visibility": "hide",
                        "supported_reasoning_levels": [{"effort": "high"}],
                    },
                ]
            }
        )
    )
    return vault[-1].config


def test_provider_choices_check_login_without_inference(vault, catalog, monkeypatch):
    _, calls = mock_runtimes(vault, monkeypatch, unauthenticated=("claude",))
    before = {p.relative_to(vault[0]): p.read_bytes() for p in vault[0].rglob("*") if p.is_file()}
    report = inspect_settings(catalog, check_providers=True)
    assert report["providers"]["codex"]["authenticated"] is True
    assert report["providers"]["claude"]["authenticated"] is False
    assert "claude auth login" == report["providers"]["claude"]["login_command"]
    assert all(command[1:3] in (["login", "status"], ["auth", "status"]) for _, command, _ in calls)
    assert all("input" not in kwargs and not kwargs.get("shell") for _, _, kwargs in calls)
    assert before == {
        p.relative_to(vault[0]): p.read_bytes() for p in vault[0].rglob("*") if p.is_file()
    }


def test_codex_model_choices_have_exact_ordered_model_efforts(catalog):
    models = model_choices(catalog, "codex")
    assert [model["id"] for model in models] == ["precise-model", "light-model"]
    assert models[0]["reasoning_efforts"] == ["low", "high"]
    assert models[1]["reasoning_efforts"] == ["low"]


def test_explicit_catalog_overrides_provider_aliases(catalog):
    catalog.data["llm"] = {
        "model_catalog": {"claude": {"custom": {"reasoning_efforts": ["high", "max"]}}}
    }
    assert model_choices(catalog, "claude") == [
        {
            "id": "custom",
            "label": "custom",
            "reasoning_efforts": ["high", "max"],
            "source": "configured model catalog",
        }
    ]


def test_claude_aliases_do_not_claim_account_model_availability(catalog):
    models = model_choices(catalog, "claude")
    assert {m["id"] for m in models} == {"sonnet", "opus", "haiku"}
    assert all(m["source"].startswith("Claude Code alias") for m in models)
    assert next(m for m in models if m["id"] == "haiku")["reasoning_efforts"] == []


def test_existing_custom_models_survive_missing_local_metadata(catalog, monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "missing"))
    catalog.data["codex"] = {"model": "my-custom-model"}
    models = model_choices(catalog, "codex")
    assert models[0]["id"] == "my-custom-model" and models[0]["reasoning_efforts"] == []


def test_auth_errors_never_expose_credentials(vault, catalog, monkeypatch):
    from research_vault.provider_settings import ADAPTERS

    def fail(self):
        raise VaultError("secret auth payload api_key=do-not-display")

    for adapter in ADAPTERS.values():
        monkeypatch.setattr(adapter, "authenticate", fail)
    report = provider_choices(catalog)
    assert all(not p["authenticated"] for p in report.values())
    assert "secret" not in json.dumps(report) and "do-not-display" not in json.dumps(report)


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_save_rechecks_login_and_refuses_signed_out_provider(vault, catalog, monkeypatch, provider):
    mock_runtimes(vault, monkeypatch, unauthenticated=(provider,))
    before = (vault[0] / "config.yaml").read_bytes()
    request = {
        "plan_token": inspect_settings(catalog)["plan_token"],
        "agents": {
            "linking": {
                "provider": provider,
                "model": "precise-model" if provider == "codex" else "sonnet",
            }
        },
    }
    with pytest.raises(VaultError, match="login check failed"):
        save_settings(catalog, request)
    assert (vault[0] / "config.yaml").read_bytes() == before


def test_save_rejects_effort_unsupported_by_known_codex_model(vault, catalog, monkeypatch):
    mock_runtimes(vault, monkeypatch)
    before = (vault[0] / "config.yaml").read_bytes()
    request = {
        "plan_token": inspect_settings(catalog)["plan_token"],
        "agents": {
            "linking": {"provider": "codex", "model": "light-model", "reasoning_effort": "high"}
        },
    }
    with pytest.raises(VaultError, match="does not support reasoning effort"):
        save_settings(catalog, request)
    assert (vault[0] / "config.yaml").read_bytes() == before


def test_save_healthy_provider_preserves_unchanged_signed_out_profiles(vault, catalog, monkeypatch):
    mock_runtimes(vault, monkeypatch, unauthenticated=("codex",))
    request = {
        "plan_token": inspect_settings(catalog)["plan_token"],
        "agents": {
            "linking": {"provider": "claude", "model": "sonnet", "reasoning_effort": "high"}
        },
    }
    report = save_settings(catalog, request)
    assert report["providers"]["codex"]["authenticated"] is False
    assert Config.load(vault[0]).runtime("linking")[0] == "claude"
    assert Config.load(vault[0]).runtime("literature")[0] == "codex"


def test_settings_cli_returns_auth_and_model_choices(vault, catalog, monkeypatch, capsys):
    _, calls = mock_runtimes(vault, monkeypatch)
    assert main(["--vault", str(vault[0]), "settings", "show"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["providers"]["codex"]["authenticated"]
    assert report["providers"]["codex"]["models"][0]["id"] == "precise-model"
    assert len(calls) == 2


@pytest.mark.parametrize("content", ["[]", "{partial", '{"models": null}'])
def test_missing_or_incomplete_model_cache_keeps_configured_models(
    catalog, tmp_path, monkeypatch, content
):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "models_cache.json").write_text(content)
    catalog.data["codex"] = {"model": "existing-model"}
    assert model_choices(catalog, "codex")[0]["id"] == "existing-model"
