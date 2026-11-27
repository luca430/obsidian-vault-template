"""Explicit configuration editing shared by CLI and Obsidian; no inference or research edits."""

from copy import deepcopy

import yaml

from .config import EFFORTS, ROLES, RUNTIME_FIELDS, Config
from .provider_settings import provider_choices, validate_changed_profiles
from .storage import VaultError, atomic, digest, inside


def inspect_settings(config, *, check_providers=False):
    text = inside(config.root, "config.yaml").read_text()
    llm = config.data.get("llm", {})
    return {
        "default_provider": llm.get("default_provider", "codex"),
        "plan_token": digest(text),
        "defaults": llm.get("defaults", {}),
        "agents": llm.get("agents", {}),
        "provider_defaults": {
            p: {k: v for k, v in config.data.get(p, {}).items() if k in RUNTIME_FIELDS}
            for p in ("codex", "claude")
        },
        **({"providers": provider_choices(config)} if check_providers else {}),
        "resolved_agents": {
            r: {
                "provider": config.runtime(r)[0],
                **{k: v for k, v in config.runtime(r)[1].items() if k in RUNTIME_FIELDS},
            }
            for r in ROLES
        },
        "efforts": {p: sorted(v) for p, v in EFFORTS.items()},
        "notice": "Changes apply to future tasks and explicit reprocessing. Completed scientific notes are retained. Model access is verified by provider responses; configured catalogs can enforce availability before requests.",
    }


def save_settings(config, request):
    if not isinstance(request, dict) or set(request) - {
        "plan_token",
        "defaults",
        "agents",
        "default_provider",
    }:
        raise VaultError("Invalid settings request")
    path = inside(config.root, "config.yaml")
    before = path.read_text()
    if request.get("plan_token") != digest(before):
        raise VaultError("Configuration changed; reload agent settings before saving")
    data = deepcopy(config.data)
    llm = data.setdefault("llm", {})
    for key in ("default_provider", "defaults", "agents"):
        if key in request:
            llm[key] = request[key]
    proposed = Config.load(config.root, data_override=data)
    providers = provider_choices(proposed)
    validate_changed_profiles(config, proposed, providers)
    # Change only llm's YAML value; retain all other configuration bytes/comments.
    head = yaml.compose(before)
    entries = [value for key, value in head.value if key.value == "llm"]
    replacement = yaml.safe_dump(llm, sort_keys=False, allow_unicode=True).rstrip()
    if len(entries) > 1:
        raise VaultError("Duplicate llm configuration sections")
    if entries:
        node = entries[0]
        # Value starts at the first nested key; replace the whole value including its indent.
        start = before.rfind("\n", 0, node.start_mark.index) + 1
        end = node.end_mark.index
        if start == 0 or before[start : node.start_mark.index].strip():
            # Flow-style or inline mappings need only their exact node span replaced.
            replacement = yaml.safe_dump(llm, default_flow_style=True).strip()
            start = node.start_mark.index
        else:
            replacement = "".join("  " + line + "\n" for line in replacement.splitlines())
        updated = before[:start] + replacement + before[end:]
    else:
        updated = (
            before.rstrip()
            + "\n\nllm:\n"
            + "".join("  " + line + "\n" for line in replacement.splitlines())
        )
    # Validate the exact serialization too, before any file is replaced.
    actual = yaml.safe_load(updated)
    if actual != data:
        raise VaultError("Configuration serialization mismatch; save refused")
    if path.read_text() != before:
        raise VaultError("Concurrent configuration edit; save refused")
    atomic(path, updated)
    return {**inspect_settings(Config.load(config.root)), "providers": providers}
