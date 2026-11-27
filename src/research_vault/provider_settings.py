"""Credential-free picker metadata and finite CLI login checks; never performs inference."""

from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import EFFORTS, PROVIDERS, ROLES
from .runtimes import ClaudeCode, Codex
from .storage import VaultError

LABELS = {"codex": "Codex", "claude": "Claude Code"}
ADAPTERS = {"codex": Codex, "claude": ClaudeCode}


def codex_models():
    path = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "models_cache.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        models = data.get("models", []) if isinstance(data, dict) else []
        if not isinstance(models, list):
            return {}
        return {
            row["slug"]: {
                "label": row.get("display_name") or row["slug"],
                "reasoning_efforts": [
                    level["effort"]
                    for level in row.get("supported_reasoning_levels", [])
                    if isinstance(level, dict) and level.get("effort") in EFFORTS["codex"]
                ],
                "source": "local Codex model metadata",
            }
            for row in models
            if isinstance(row, dict)
            and isinstance(row.get("slug"), str)
            and row.get("visibility", "list") == "list"
        }
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def model_choices(config, provider):
    catalog = config.data.get("llm", {}).get("model_catalog", {}).get(provider)
    if catalog is not None:
        models = {
            name: {
                "label": name,
                "reasoning_efforts": caps.get("reasoning_efforts", []),
                "source": "configured model catalog",
            }
            for name, caps in catalog.items()
        }
    elif provider == "codex":
        models = codex_models()
    else:
        # CLI aliases are not an account-entitlement API. Offer conservative common
        # effort levels; exact configured catalogs can specify additional capabilities.
        # https://code.claude.com/docs/en/model-config
        models = {
            "sonnet": {
                "label": "Sonnet",
                "reasoning_efforts": ["low", "medium", "high"],
                "source": "Claude Code alias",
            },
            "opus": {
                "label": "Opus",
                "reasoning_efforts": ["low", "medium", "high"],
                "source": "Claude Code alias",
            },
            "haiku": {
                "label": "Haiku",
                "reasoning_efforts": [],
                "source": "Claude Code alias; effort depends on resolved version",
            },
        }
    if catalog is None:
        configured = [config.data.get(provider, {}).get("model")]
        configured.extend(
            settings.get("model")
            for role in ROLES
            for selected, settings in [config.runtime(role)]
            if selected == provider
        )
        for model in configured:
            if model and model not in models:
                models[model] = {
                    "label": model,
                    "reasoning_efforts": [],
                    "source": "configured model; capabilities unknown",
                }
    return [{"id": name, **caps} for name, caps in models.items()]


def provider_choices(config):
    def check(provider):
        adapter = ADAPTERS[provider](config)
        try:
            adapter.authenticate()
            authenticated = True
            message = "Signed in"
        except (VaultError, OSError, subprocess.TimeoutExpired):
            authenticated = False
            # Never expose raw auth output, account identity, tokens or environment.
            message = "Login unavailable. Install the CLI and sign in in the plugin's execution environment."
        return provider, {
            "label": LABELS[provider],
            "authenticated": authenticated,
            "message": message,
            "login_command": "codex login" if provider == "codex" else "claude auth login",
            "models": model_choices(config, provider),
            "notice": "Login status does not guarantee access to every model. Model access is checked on actual requests; no inference is used to populate this list.",
        }

    # These are bounded status checks, joined before returning; no worker survives
    # this command and there is no polling or automatic login.
    with ThreadPoolExecutor(max_workers=2) as pool:
        return dict(pool.map(check, PROVIDERS))


def validate_changed_profiles(previous, proposed, providers):
    for role in ROLES:
        provider, settings = proposed.runtime(role)
        if previous.runtime(role) == (provider, settings):
            continue
        if not providers[provider]["authenticated"]:
            raise VaultError(
                f"{LABELS[provider]} login check failed; sign in before changing {role} settings"
            )
        ADAPTERS[provider](proposed).validate_model(role)
