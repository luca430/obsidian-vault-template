"""Stage-scoped cache contracts. Bump only the algorithm contract being changed."""

from __future__ import annotations

from importlib.metadata import version
from inspect import getsource

from .agents import PROMPT_SUFFIX, SCHEMAS
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
    "consolidation": 1,
}
NAMESPACES = {
    "literature": "document_summaries",
    "manuscript": "document_summaries",
    "concepts": "concept_extraction",
    "linking": "relationship_proposals",
    "synthesis": "high_level_synthesis",
    "maintenance": "maintenance",
    "consolidation": "concept_consolidation",
}


def stage_recipe(config, stage, provider=None, *, legacy=False):
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
            **(
                {"agent_configuration": settings, "prompt_version": digest(PROMPT_SUFFIX)}
                if not legacy
                else {}
            ),
            # Legacy receipts are imported only after current schema/provenance validation.
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
            content_recipe(config, role)
            for role in ("concepts", "linking", "synthesis", "maintenance", "retrieval")
        ]
    )


def content_recipe(config, stage):
    """Scientific invalidation excludes run-time choices; inference receipts retain them."""
    if stage in {"extraction", "retrieval"}:
        return stage_recipe(config, stage)
    from dataclasses import replace

    data = {**config.data, "codex": {}, "claude": {}, "llm": {}}
    return stage_recipe(replace(config, data=data, provider_override=None), stage)
