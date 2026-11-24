"""Project-local configuration; no code paths depend on research subject."""

from __future__ import annotations

import re
from dataclasses import dataclass
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

    @classmethod
    def load(cls, root: Path) -> Config:
        root = root.resolve()
        data = load_yaml(inside(root, "config.yaml"))
        for field in ("zotero", "codex", "attachments", "processing", "ocr"):
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
        return cls(root, data)

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
