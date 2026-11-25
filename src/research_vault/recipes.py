"""Stage-scoped cache contracts. Bump only the algorithm contract being changed."""

from __future__ import annotations

from importlib.metadata import version
from inspect import getsource

from .agents import SCHEMAS
from .storage import digest, file_hash, inside

# These explicit versions cover implementation changes, independently of role instructions.
VERSIONS = {
    "extraction": 4,
    "literature": 4,
    "manuscript": 4,
    "concepts": 2,
    "linking": 3,
    "synthesis": 3,
    "maintenance": 3,
    "retrieval": 3,
}
NAMESPACES = {
    "literature": "document_summaries",
    "manuscript": "document_summaries",
    "concepts": "concept_extraction",
    "linking": "relationship_proposals",
    "synthesis": "high_level_synthesis",
    "maintenance": "maintenance",
}


def stage_recipe(config, stage, provider=None):
    if stage == "extraction":
        from .sources import pdf

        return digest([VERSIONS[stage], getsource(pdf), version("PyMuPDF"), config.ocr])
    if stage == "retrieval":
        return digest(
            [
                VERSIONS[stage],
                {
                    k: config.processing(k)
                    for k in (
                        "graph_neighbors",
                        "graph_fragment_chars",
                        "retrieval_passages",
                        "passage_chars",
                        "candidate_terms",
                        "candidate_pool",
                    )
                },
            ]
        )
    selected, settings = config.runtime(stage, provider)
    return digest(
        {
            "algorithm": VERSIONS[stage],
            "schema": SCHEMAS[stage],
            "global_instructions": file_hash(inside(config.root, "AGENTS.md")),
            "role_instructions": file_hash(inside(config.root, f"agents/{stage}.md")),
            "model": {k: settings.get(k) for k in ("model", "executable")},
            # Default Codex retains the exact pre-provider contract and existing cache keys.
            **({"provider": selected, "adapter": 1} if selected != "codex" else {}),
            "chunking": {k: config.processing(k) for k in ("chunk_chars", "chunk_context_chars")}
            if stage in {"literature", "manuscript"}
            else {},
            "upstream_extraction": stage_recipe(config, "extraction")
            if stage in {"literature", "manuscript"}
            else None,
        }
    )


def reasoning_recipe(config):
    return digest(
        [
            stage_recipe(config, role)
            for role in ("concepts", "linking", "synthesis", "maintenance", "retrieval")
        ]
    )
