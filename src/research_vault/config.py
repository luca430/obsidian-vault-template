"""Project-local configuration; no code paths depend on research subject."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlparse

import yaml

from .paths import local_path
from .storage import VaultError, digest, inside

PROCESSING_DEFAULTS = {
    "chunk_chars": 60000,
    "graph_neighbors": 8,
    "graph_fragment_chars": 12000,
    "retrieval_passages": 4,
    "passage_chars": 2000,
    "chunk_context_chars": 600,
    "candidate_terms": 32,
    "candidate_pool": 128,
    "startup_max_documents": 5,
    "max_retry_attempts": 3,
    "startup_max_operations": 20,
}

OCR_DEFAULTS = {
    "enabled": False,
    "executable": "tesseract",
    "language": "eng",
    "dpi": 150,
    "timeout_seconds": 60,
    "min_text_chars": 10,
}

KEY = re.compile(r"^[A-Z0-9]{8}$")
ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")
RUNTIME_FIELDS = {
    "model",
    "reasoning_effort",
    "timeout_seconds",
    "max_input_chars",
    "output_reserve_chars",
    "max_output_tokens",
    "retry_policy",
}
EFFORTS = {
    "codex": {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"},
    "claude": {"low", "medium", "high", "xhigh", "max"},
}
PROVIDERS = ("codex", "claude")
ROLES = (
    "literature",
    "manuscript",
    "concepts",
    "linking",
    "synthesis",
    "maintenance",
    "consolidation",
)


def load_yaml(path: Path) -> dict:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise VaultError(f"Cannot read {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise VaultError(f"{path.name} must contain a YAML mapping")
    return value


@dataclass(frozen=True)
class Config:
    root: Path
    data: dict
    provider_override: str | None = None

    @classmethod
    def load(cls, root: Path, *, data_override=None) -> Config:
        root = root.resolve()
        data = load_yaml(inside(root, "config.yaml")) if data_override is None else data_override
        for field in ("zotero", "codex", "claude", "llm", "attachments", "processing", "ocr"):
            if not isinstance(data.get(field, {}), dict):
                raise VaultError(f"{field} must be a mapping")
        z = data.get("zotero", {})
        if not isinstance(z.get("base_url", "http://localhost:23119/api/"), str):
            raise VaultError("Zotero base_url must be a string")
        if not isinstance(z.get("library", "users/0"), str):
            raise VaultError("Zotero library must be a string")
        url = urlparse(z.get("base_url", "http://localhost:23119/api/"))
        if (
            url.scheme != "http"
            or url.hostname not in {"localhost", "127.0.0.1", "::1"}
            or url.path != "/api/"
            or url.username
            or url.query
            or url.fragment
        ):
            raise VaultError("Zotero base_url must be a loopback HTTP /api/ URL")
        if not KEY.fullmatch(str(z.get("collection_key", ""))):
            raise VaultError("Set zotero.collection_key to an eight-character Zotero key")
        if not re.fullmatch(r"(?:users|groups)/\d+", z.get("library", "users/0")):
            raise VaultError("library must be users/0, users/<id>, or groups/<id>")
        if not isinstance(z.get("include_subcollections", False), bool):
            raise VaultError("include_subcollections must be true or false")
        if not isinstance(data.get("attachments", {}).get("overrides", {}), dict):
            raise VaultError("attachments.overrides must be a mapping")
        for key, value in data.get("attachments", {}).get("overrides", {}).items():
            if not KEY.fullmatch(str(key)) or value not in ("main", "supplementary", "ignore"):
                raise VaultError(
                    "Attachment overrides map attachment keys to main/supplementary/ignore"
                )
        if not isinstance(data.get("source_roots", []), list) or any(
            not isinstance(p, str) for p in data.get("source_roots", [])
        ):
            raise VaultError("source_roots must be a list")
        c = data.get("codex", {})
        if not isinstance(c.get("executable", "codex"), str) or not c.get("executable", "codex"):
            raise VaultError("codex.executable must be a nonempty string")
        if c.get("model") is not None and not isinstance(c["model"], str):
            raise VaultError("codex.model must be a string or null")
        if not isinstance(c.get("timeout_seconds", 600), int) or c.get("timeout_seconds", 600) <= 0:
            raise VaultError("timeout_seconds must be a positive integer")
        if (
            not isinstance(c.get("max_input_chars", 500000), int)
            or c.get("max_input_chars", 500000) < 1000
        ):
            raise VaultError("max_input_chars must be at least 1000")
        for field, default in {"output_reserve_chars": 16000}.items():
            if type(c.get(field, default)) is not int or c.get(field, default) < 0:
                raise VaultError(f"codex.{field} must be a nonnegative integer")
        cls.validate_llm(data)
        for field, default in PROCESSING_DEFAULTS.items():
            if (
                type(data.get("processing", {}).get(field, default)) is not int
                or data.get("processing", {}).get(field, default) <= 0
            ):
                raise VaultError(f"processing.{field} must be a positive integer")
        ocr = {**OCR_DEFAULTS, **data.get("ocr", {})}
        if type(ocr["enabled"]) is not bool:
            raise VaultError("ocr.enabled must be true or false")
        for field in ("executable", "language"):
            if not isinstance(ocr[field], str) or not ocr[field].strip():
                raise VaultError(f"ocr.{field} must be a nonempty string")
        if not re.fullmatch(r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*", ocr["language"]):
            raise VaultError("ocr.language must contain Tesseract language names joined by +")
        for field in ("dpi", "timeout_seconds", "min_text_chars"):
            if type(ocr[field]) is not int or ocr[field] <= 0:
                raise VaultError(f"ocr.{field} must be a positive integer")
        if ocr["dpi"] > 600:
            raise VaultError("ocr.dpi must be at most 600")
        config = cls(root, data)
        for role in ROLES:
            config.runtime(role)
        return config

    @staticmethod
    def validate_llm(data: dict) -> None:
        llm = data.get("llm", {})
        if set(llm) - {"default_provider", "defaults", "agents", "evaluation", "model_catalog"}:
            raise VaultError("Unknown llm configuration field")
        if llm.get("default_provider", "codex") not in PROVIDERS:
            raise VaultError("llm.default_provider must be codex or claude")
        roles = llm.get("agents", {})
        if not isinstance(roles, dict) or set(roles) - set(ROLES):
            raise VaultError("llm.agents must map supported agent roles to runtime settings")
        for role, override in roles.items():
            if isinstance(override, str):
                override = {"provider": override}
            if not isinstance(override, dict) or set(override) - {"provider", *RUNTIME_FIELDS}:
                raise VaultError(
                    f"llm.agents.{role} accepts provider and supported runtime settings"
                )
            if override.get("provider", llm.get("default_provider", "codex")) not in PROVIDERS:
                raise VaultError(f"Unknown provider for {role}")
            if override.get("model") is not None and (
                not isinstance(override["model"], str) or not override["model"].strip()
            ):
                raise VaultError(f"Invalid model for {role}")
            Config.validate_runtime(override, f"llm.agents.{role}")
        defaults = llm.get("defaults", {})
        Config.validate_runtime(defaults, "llm.defaults")
        if "provider" in defaults:
            raise VaultError("Use llm.default_provider for the global provider")
        catalog = llm.get("model_catalog", {})
        if not isinstance(catalog, dict) or set(catalog) - set(PROVIDERS):
            raise VaultError("llm.model_catalog must map providers to model capabilities")
        for provider, models in catalog.items():
            if not isinstance(models, dict):
                raise VaultError("Model catalog must be a mapping")
            for model, capabilities in models.items():
                if (
                    not isinstance(model, str)
                    or not isinstance(capabilities, dict)
                    or set(capabilities) - {"reasoning_efforts", "max_output_tokens"}
                ):
                    raise VaultError("Invalid model capability record")
                efforts = capabilities.get("reasoning_efforts", [])
                if not isinstance(efforts, list) or any(
                    not isinstance(e, str) or e not in EFFORTS[provider] for e in efforts
                ):
                    raise VaultError("Invalid model reasoning effort capability")
                cap = capabilities.get("max_output_tokens")
                if cap is not None and (type(cap) is not int or cap < 1):
                    raise VaultError("Invalid model output-token capability")
        evaluation = llm.get("evaluation", {})
        if not isinstance(evaluation, dict) or set(evaluation) - {"enabled", "provider"}:
            raise VaultError("llm.evaluation accepts enabled and provider")
        if type(evaluation.get("enabled", False)) is not bool:
            raise VaultError("llm.evaluation.enabled must be true or false")
        if (evaluation.get("enabled") or "provider" in evaluation) and evaluation.get(
            "provider"
        ) not in PROVIDERS:
            raise VaultError("Enabled evaluation requires a codex or claude provider")
        for provider in PROVIDERS:
            settings = data.get(provider, {})
            if not isinstance(settings, dict):
                raise VaultError(f"{provider} must be a mapping")
            if set(settings) - {
                "executable",
                "model",
                "timeout_seconds",
                "max_input_chars",
                "output_reserve_chars",
                "reasoning_effort",
                "max_output_tokens",
                "retry_policy",
            }:
                raise VaultError(f"Unknown {provider} configuration field")
            Config.validate_runtime(
                {k: v for k, v in settings.items() if k != "executable"}, provider
            )
            for key in ("executable", "model"):
                value = settings.get(key)
                if (key == "executable" and key in settings or value is not None) and (
                    not isinstance(value, str) or not value.strip()
                ):
                    raise VaultError(f"{provider}.{key} must be a nonempty string")
            for key, default, minimum in (
                ("timeout_seconds", 600, 1),
                ("max_input_chars", 500000, 1000),
                ("output_reserve_chars", 16000, 0),
            ):
                if (
                    type(settings.get(key, default)) is not int
                    or settings.get(key, default) < minimum
                ):
                    raise VaultError(f"{provider}.{key} must be an integer >= {minimum}")

    @staticmethod
    def validate_runtime(settings, label):
        if not isinstance(settings, dict) or set(settings) - {"provider", *RUNTIME_FIELDS}:
            raise VaultError(f"Invalid runtime settings in {label}")
        model = settings.get("model")
        if model is not None and (
            not isinstance(model, str)
            or not model.strip()
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/\[\]-]{0,159}", model)
        ):
            raise VaultError(f"Invalid model in {label}")
        effort = settings.get("reasoning_effort")
        if effort is not None and (
            not isinstance(effort, str) or effort not in set.union(*EFFORTS.values())
        ):
            raise VaultError(f"Invalid reasoning_effort in {label}")
        for key, minimum in (
            ("max_input_chars", 1000),
            ("output_reserve_chars", 0),
            ("max_output_tokens", 1),
            ("timeout_seconds", 1),
        ):
            if key in settings and (type(settings[key]) is not int or settings[key] < minimum):
                raise VaultError(f"Invalid {label}.{key}")
        policy = settings.get("retry_policy", {})
        if not isinstance(policy, dict) or set(policy) - {"max_attempts", "retry_after_seconds"}:
            raise VaultError(f"Invalid {label}.retry_policy")
        for key, value in policy.items():
            if type(value) is not int or value < (1 if key == "max_attempts" else 0):
                raise VaultError(f"Invalid {label}.retry_policy.{key}")

    def with_provider(self, provider: str | None) -> Config:
        if provider is not None and provider not in PROVIDERS:
            raise VaultError("Provider must be codex or claude")
        return replace(self, provider_override=provider)

    def runtime(self, role: str, provider: str | None = None) -> tuple[str, dict]:
        if role not in ROLES:
            raise VaultError(f"Unknown agent role: {role}")
        llm = self.data.get("llm", {})
        override = llm.get("agents", {}).get(role, {})
        if role == "consolidation" and role not in llm.get("agents", {}):
            override = llm.get("agents", {}).get("linking", {})
        if isinstance(override, str):
            override = {"provider": override}
        configured = override.get("provider", llm.get("default_provider", "codex"))
        selected = provider or self.provider_override or configured
        if selected not in PROVIDERS:
            raise VaultError(f"Unknown LLM provider: {selected}")
        settings = dict(self.data.get(selected, {}))
        for layer in (
            self.data.get("llm", {}).get("defaults", {}),
            {k: v for k, v in override.items() if k != "provider"}
            if selected == configured
            else {},
        ):
            policy = {**settings.get("retry_policy", {}), **layer.get("retry_policy", {})}
            settings.update(layer)
            if policy:
                settings["retry_policy"] = policy
        self.validate_runtime({k: v for k, v in settings.items() if k != "executable"}, role)
        effort = settings.get("reasoning_effort")
        if effort is not None and effort not in EFFORTS[selected]:
            raise VaultError(f"{selected} does not support reasoning effort {effort}")
        capabilities = self.data.get("llm", {}).get("model_catalog", {}).get(selected)
        model = settings.get("model")
        if capabilities is not None and model:
            if model not in capabilities:
                raise VaultError(
                    f"Model {model} is unavailable in the configured {selected} catalog"
                )
            if effort is not None and effort not in capabilities[model].get(
                "reasoning_efforts", []
            ):
                raise VaultError(f"Model {model} does not support effort {effort}")
            cap = capabilities[model].get("max_output_tokens")
            if cap and settings.get("max_output_tokens", 0) > cap:
                raise VaultError(f"Model {model} does not support the requested output-token cap")
        if selected == "codex" and settings.get("max_output_tokens") is not None:
            raise VaultError(
                "Codex exec cannot enforce a native max_output_tokens cap; use output_reserve_chars for request budgeting"
            )
        return selected, settings

    def processing(self, key: str) -> int:
        return self.data.get("processing", {}).get(key, PROCESSING_DEFAULTS[key])

    @property
    def ocr(self) -> dict:
        return {**OCR_DEFAULTS, **self.data.get("ocr", {})}

    @property
    def zotero(self) -> dict:
        return self.data["zotero"]

    @property
    def identity(self) -> str:
        return digest(
            {
                k: self.zotero.get(k, default)
                for k, default in [
                    ("base_url", "http://localhost:23119/api/"),
                    ("library", "users/0"),
                    ("collection_key", None),
                    ("include_subcollections", False),
                ]
            }
        )

    def authorized(self, raw: str) -> Path:
        path = local_path(raw)
        path = (self.root / path).resolve() if not path.is_absolute() else path.resolve()
        roots = [(self.root / local_path(p)).resolve() for p in self.data.get("source_roots", [])]
        if not any(path.is_relative_to(p) for p in roots):
            raise VaultError(f"Source outside configured source_roots: {raw}")
        return path

    def works(self) -> list[dict]:
        path = inside(self.root, "works.yaml")
        works = load_yaml(path).get("works", []) if path.exists() else []
        if not isinstance(works, list):
            raise VaultError("works must be a list")
        seen = set()
        for work in works:
            if not isinstance(work, dict) or not all(
                work.get(k)
                for k in ("id", "title", "document_type", "status", "source_type", "source_path")
            ):
                raise VaultError(
                    "Each work needs id, title, document_type, status, source_type, source_path"
                )
            if (
                not isinstance(work["id"], str)
                or not ID.fullmatch(work["id"])
                or work["id"] in seen
            ):
                raise VaultError("Work IDs must be unique safe identifiers")
            if any(
                not isinstance(work[k], str)
                for k in ("id", "title", "document_type", "status", "source_type", "source_path")
            ):
                raise VaultError("Work fields must be strings")
            if work.get("bibliography_path") is not None and not isinstance(
                work["bibliography_path"], str
            ):
                raise VaultError("bibliography_path must be a string")
            if work["source_type"] not in {"pdf", "latex"}:
                raise VaultError("source_type must be pdf or latex")
            seen.add(work["id"])
            self.authorized(work["source_path"])
            if work.get("bibliography_path"):
                self.authorized(work["bibliography_path"])
        return works
