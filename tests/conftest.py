from __future__ import annotations

import shutil
from copy import deepcopy
from pathlib import Path

import pymupdf
import pytest
import yaml

from research_vault.config import Config
from research_vault.pipeline import Pipeline
from research_vault.storage import VaultError, digest, file_hash

REPO = Path(__file__).resolve().parents[1]


def write_pdf(
    path: Path, text: str = "Synthetic scientific evidence about a shared mechanism."
) -> Path:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(path)
    document.close()
    return path


class FakeZotero:
    server = "synthetic-instance"

    def __init__(self):
        self.items = {}
        self.fail = False

    def snapshot(self):
        if self.fail:
            raise VaultError("Synthetic Zotero connectivity failure")
        return deepcopy(self.items)

    def paper(self, key, attachments=(), version=1, **fields):
        files = [
            {
                "key": k,
                "path": str(p),
                "sha256": file_hash(p),
                "classification": role,
                "metadata": {"key": k, "data": {"itemType": "attachment"}},
            }
            for k, p, role in attachments
        ]
        metadata = {
            "key": key,
            "version": version,
            "data": {"key": key, "itemType": "journalArticle", "title": "Synthetic item", **fields},
        }
        self.items[key] = {
            "metadata": metadata,
            "attachments": files,
            "annotations": [],
            "fingerprint": digest([metadata, files]),
        }


class FakeAgent:
    def __init__(self):
        self.calls = []
        self.links = []
        self.fail_role = None
        self.callback = None

    def available(self):
        return "mock-codex"

    def run(self, role, payload):
        self.calls.append((role, deepcopy(payload)))
        if self.callback:
            self.callback(role, payload)
        if self.fail_role == role:
            raise VaultError("Synthetic agent failure")
        if role in {"literature", "manuscript"}:
            result = {
                "title": "Synthetic summary",
                "summary": "## Objectives\nSynthetic scientific evidence.\n\nText extraction only; figures were not interpreted.",
                "evidence": [
                    {
                        "source": page["source"],
                        "locator": page["locator"],
                        "quote": page["text"].strip(),
                    }
                    for page in payload["pages"]
                ],
            }
            result["claims"] = [
                {
                    "statement": "Synthetic scientific evidence.",
                    "evidence": result["evidence"],
                    "uncertainty": "Synthetic claim; scientific validity not assessed.",
                }
            ]
            return result
        if role == "concepts":
            return {"concepts": []}
        if role == "linking":
            return {
                "links": [
                    deepcopy(link)
                    for link in self.links
                    if all(
                        e["note"] in payload["notes"]
                        and " ".join(e["quote"].split())
                        in " ".join(payload["notes"][e["note"]].split())
                        for e in link["evidence"]
                    )
                ]
            }
        return {"revisions": []}


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    for name in ["AGENTS.md", "Home.md"]:
        shutil.copy(REPO / name, root / name)
    shutil.copytree(REPO / "agents", root / "agents")
    for folder in [
        "Literature",
        "Concepts",
        "My Work",
        "My Notes",
        "Reviews/Links",
        "Reviews/Removals",
        "Reviews/Revisions",
    ]:
        (root / folder).mkdir(parents=True)
    source = tmp_path / "sources"
    source.mkdir()
    (root / "config.yaml").write_text(
        yaml.safe_dump({"zotero": {"collection_key": "COLLECT1"}, "source_roots": [str(source)]})
    )
    z, agent = FakeZotero(), FakeAgent()
    config = Config.load(root)
    return root, source, z, agent, Pipeline(config, z, agent)


def decide(root, state, pid, status="approved", decision=None):
    path = root / state["proposals"][pid]["path"]
    from research_vault.storage import frontmatter, markdown

    meta, body = frontmatter(path.read_text())
    meta["status"] = status
    if decision:
        meta["decision"] = decision
    path.write_text(markdown(meta, body))


def link(source="Literature/PAPER001.md", target="Concepts/mechanism.md"):
    return {
        "source": source,
        "target": target,
        "relationship": "mechanism",
        "justification": "Both describe the same mechanism under the stated assumptions.",
        "evidence": [{"note": source, "quote": "Synthetic scientific evidence."}],
        "target_concept": {
            "title": "Mechanism",
            "body": "A scoped scientific mechanism grounded in source evidence.",
        },
    }
