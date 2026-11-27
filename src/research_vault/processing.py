"""Bounded scientific processing with content-addressed, validated restart caches."""

from __future__ import annotations

import json
import re
from collections import Counter

from .agents import fits, payload_budget, validate_note_evidence, validate_output, validate_summary
from .cancellation import check_cancelled
from .chunking import section_chunks
from .inference_errors import (
    AllowanceExhausted,
    ContextExhausted,
    InvalidOutput,
    OperationDeferred,
    RateLimited,
)
from .operations import OperationStore
from .recipes import NAMESPACES, content_recipe, stage_recipe
from .sources import extraction_complete, page_coverage, pdf, usable_pages
from .storage import VaultError, atomic, digest, inside, read_json


def size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False))


def fragments(records: list[dict], chars: int):
    """Cover every character. Preserve page/file locators and add character offsets."""
    for record in records:
        text = record["text"]
        for start in range(0, max(1, len(text)), chars):
            yield {**record, "text": text[start : start + chars], "character_offset": start}


class Processor:
    def __init__(
        self,
        config,
        agent,
        recipe,
        progress,
        full=False,
        dry_run=False,
        retry=False,
        operation_budget=None,
    ):
        self.config, self.agent, self.recipe = config, agent, recipe
        self.progress, self.full = progress, full
        self.hits = 0
        self.calls = 0
        self.completed_operations = 0
        self.summary_warnings = {}
        self.operations = []
        self.evaluation_failures = []
        self.last_cache_metadata = None
        self.extraction_recipe = stage_recipe(config, "extraction")
        self.tasks = OperationStore(config.root, recover_running=not dry_run)
        self.blocked_providers = {}
        self.blocked_errors = {}
        self.retry = retry
        self.deferred_operations = []
        self.task_context = None
        self.operation_budget = operation_budget

    def plan_inference(self, role, payload, provider=None, evaluation=False):
        selected, settings = self.config.runtime(role, provider)
        key = digest(
            [
                "evaluation" if evaluation else "inference",
                role,
                payload,
                content_recipe(self.config, role),
            ]
        )
        context = {
            "id": key,
            "kind": "inference",
            "role": role,
            "provider": selected,
            "recipe": stage_recipe(self.config, role, provider),
            "configuration": settings,
            "input_fingerprint": digest(payload),
            "evaluation_only": evaluation,
        }
        record = self.tasks.plan(key, **{k: v for k, v in context.items() if k != "id"})
        if record["status"] != "completed" and record.get("recipe") != context["recipe"]:
            self.tasks.update(
                key,
                "pending",
                **{k: v for k, v in context.items() if k != "id"},
                attempts=0,
                eligible_after=None,
                reason="Agent configuration changed for an unfinished operation",
            )
        return key, context

    def cached(self, namespace, key, operation, reusable=None, metadata=None):
        check_cancelled()
        path = inside(self.config.root, f".pipeline/cache/{namespace}/{digest(key)}.json")
        logical = self.task_context or {
            "id": digest([namespace, key]),
            "kind": "extraction",
            "namespace": namespace,
        }
        task_id = logical["id"]
        record = self.tasks.plan(task_id, **{k: v for k, v in logical.items() if k != "id"})
        # Completed receipts pin the actual cache/recipe used for this scientific input.
        if not self.full and record.get("cache_path"):
            path = inside(self.config.root, record["cache_path"])
        try:
            cached = read_json(path) if path.exists() and not self.full else None
        except VaultError:
            cached = None
        if cached is not None and (
            not isinstance(cached, dict) or cached.get("value_hash") != digest(cached.get("value"))
        ):
            cached = None
        if cached is not None:
            valid = (
                isinstance(cached, dict)
                and cached.get("key") == record.get("cache_key", digest(key))
                and cached.get("value_hash") == digest(cached.get("value"))
                and (
                    "metadata" not in cached
                    or cached.get("metadata_hash") == digest(cached["metadata"])
                )
            )
            if valid and (reusable is None or reusable(cached["value"])):
                self.tasks.update(
                    task_id,
                    "completed",
                    cache_path=path.relative_to(self.config.root).as_posix(),
                    cache_key=cached["key"],
                    cache_hits=record.get("cache_hits", 0) + 1,
                )
                self.hits += 1
                self.last_cache_metadata = cached.get("metadata")
                self.progress("cache", namespace=namespace, reused=self.hits)
                return cached["value"]
        provider = logical.get("provider")
        if provider in self.blocked_providers:
            reason = self.blocked_providers[provider]
            self.tasks.update(
                task_id, "deferred", reason=reason, error_type=self.blocked_errors[provider]
            )
            self.deferred_operations.append(task_id)
            raise OperationDeferred(reason)
        settings = logical.get("configuration", {})
        max_attempts = settings.get("retry_policy", {}).get(
            "max_attempts", self.config.processing("max_retry_attempts")
        )
        if not self.tasks.eligible(record, retry=self.retry) or (
            record["status"] == "failed"
            and record.get("attempts", 0) >= max_attempts
            and not self.retry
            and not self.full
        ):
            self.deferred_operations.append(task_id)
            raise OperationDeferred(
                "Task retry policy defers this operation; use the next eligible Sync or explicit --retry"
            )
        self.tasks.update(task_id, "running", attempts=record.get("attempts", 0) + 1)
        try:
            result = operation()
            # A validated result is durable BEFORE observing a cancellation that
            # arrived during inference. Integration remains a separate checkpoint.
        except BaseException as exc:
            status = (
                "pending"
                if isinstance(exc, KeyboardInterrupt)
                else "deferred"
                if isinstance(exc, (AllowanceExhausted, OperationDeferred))
                else "failed"
            )
            details = {
                "reason": str(exc),
                "error_type": getattr(
                    exc,
                    "code",
                    "invalid_output"
                    if logical.get("kind") == "inference" and isinstance(exc, VaultError)
                    else type(exc).__name__,
                ),
            }
            delay = getattr(exc, "retry_after", None)
            if isinstance(exc, RateLimited) and delay is None:
                delay = settings.get("retry_policy", {}).get("retry_after_seconds", 60)
            if delay:
                from datetime import datetime, timedelta, timezone

                details["eligible_after"] = (
                    datetime.now(timezone.utc) + timedelta(seconds=delay)
                ).isoformat()
            self.tasks.update(task_id, status, **details)
            if isinstance(exc, OperationDeferred):
                self.deferred_operations.append(task_id)
            if isinstance(exc, (AllowanceExhausted, RateLimited)):
                self.blocked_providers[provider] = str(exc)
                self.blocked_errors[provider] = exc.code
                self.tasks.defer_provider(provider, str(exc), delay, error_type=exc.code)
                self.deferred_operations.append(task_id)
            raise
        self.last_cache_metadata = metadata() if metadata else None
        resolved_recipe = (self.last_cache_metadata or {}).get(
            "resolved_recipe", logical.get("recipe")
        )
        cache_key = digest([key, resolved_recipe]) if metadata else digest(key)
        path = inside(self.config.root, f".pipeline/cache/{namespace}/{cache_key}.json")
        self.tasks.update(
            task_id,
            "running",
            cache_path=path.relative_to(self.config.root).as_posix(),
            cache_key=cache_key,
            configured_recipe=logical.get("recipe"),
            recipe=resolved_recipe,
            resolved_model=(self.last_cache_metadata or {}).get("resolved_model"),
        )
        atomic(
            path,
            json.dumps(
                {
                    "key": cache_key,
                    "value_hash": digest(result),
                    "value": result,
                    **(
                        {
                            "metadata": self.last_cache_metadata,
                            "metadata_hash": digest(self.last_cache_metadata),
                        }
                        if metadata
                        else {}
                    ),
                },
                ensure_ascii=False,
            ),
        )
        self.tasks.update(
            task_id,
            "completed",
            cache_path=path.relative_to(self.config.root).as_posix(),
            cache_key=cache_key,
            recipe=resolved_recipe,
            resolved_model=(self.last_cache_metadata or {}).get("resolved_model"),
            reasoning_effort=(self.last_cache_metadata or {}).get("reasoning_effort"),
            usage=(self.last_cache_metadata or {}).get("usage"),
        )
        check_cancelled()
        return result

    def run(self, role, payload, validator=None, *, provider=None, evaluation=False):
        selected, settings = self.config.runtime(role, provider)
        metadata = {
            "role": role,
            "provider": selected,
            "model": settings.get("model"),
            "recipe": stage_recipe(self.config, role, provider),
            "evaluation_only": evaluation,
            "reasoning_effort": settings.get("reasoning_effort"),
            "configuration": settings,
        }
        logical_id, self.task_context = self.plan_inference(role, payload, provider, evaluation)
        if not fits(self.config, role, payload, provider):
            error = ContextExhausted(
                f"{role} request cannot fit configured input/output budget; no truncation"
            )
            self.tasks.update(logical_id, "failed", reason=str(error), error_type=error.code)
            self.record_operation(
                {
                    **metadata,
                    "cached": True,
                    "validation_status": "failed",
                    "error_type": error.code,
                }
            )
            self.task_context = None
            raise error
        if hasattr(self.agent, "identity"):
            identity = (
                self.agent.identity(role, selected)
                if hasattr(self.agent, "run_provider")
                else self.agent.identity(role)
            )
            metadata.update(identity)
        reused = True

        def invoke():
            nonlocal reused
            if self.operation_budget is not None and self.calls >= self.operation_budget:
                raise OperationDeferred(
                    "Startup inference budget reached; resume with manual or startup Sync"
                )
            reused = False
            self.calls += 1
            record = self.tasks.records[logical_id]
            self.tasks.update(logical_id, "running", requests=record.get("requests", 0) + 1)
            self.progress(
                "inference",
                role=role,
                provider=selected,
                model=settings.get("model"),
                operation=self.calls,
            )
            if provider is not None and not hasattr(self.agent, "run_provider"):
                raise VaultError(
                    "Injected LLM runtime does not support secondary provider execution"
                )
            raw = (
                self.agent.run_provider(role, payload, selected)
                if provider is not None
                else self.agent.run(role, payload)
            )
            try:
                result = validate_output(role, raw)
            except VaultError as exc:
                raise InvalidOutput(str(exc)) from exc
            metadata.update(getattr(self.agent, "last_metadata", {}))
            metadata["resolved_recipe"] = digest(
                [
                    metadata["recipe"],
                    metadata.get("resolved_model", metadata.get("model")),
                    metadata.get("reasoning_effort"),
                ]
            )
            check(result)
            return result

        def check(result):
            warnings = []
            if role in {"literature", "manuscript"}:
                self.remember(result, validate_summary(result, payload["pages"]))
                if {p["source"] for p in payload["pages"]} - {
                    e["source"] for e in result["evidence"]
                }:
                    raise VaultError(
                        "Summary must provide evidence from every processed source, including supplements"
                    )
                warnings = self.warnings_for(result)
            else:
                key = (
                    "links"
                    if role == "linking"
                    else "concepts"
                    if role == "concepts"
                    else "consolidations"
                    if role == "consolidation"
                    else "revisions"
                )
                for item in result[key]:
                    warnings.extend(validate_note_evidence(item["evidence"], payload["notes"]))
            if validator:
                validator(result)
            metadata.update(
                validation_status="review_required" if warnings else "validated", warnings=warnings
            )

        def reusable_result(value):
            try:
                validate_output(role, value)
                check(value)
                return True
            except VaultError:
                return False

        if not self.full and not self.tasks.records[logical_id].get("cache_path"):
            namespace = "evaluations" if evaluation else NAMESPACES[role]
            legacy_key = digest(
                [stage_recipe(self.config, role, provider, legacy=True), role, payload]
            )
            legacy_path = inside(self.config.root, f".pipeline/cache/{namespace}/{legacy_key}.json")
            try:
                legacy = read_json(legacy_path)
                if (
                    isinstance(legacy, dict)
                    and legacy.get("key") == legacy_key
                    and legacy.get("value_hash") == digest(legacy.get("value"))
                    and (
                        "metadata" not in legacy
                        or legacy.get("metadata_hash") == digest(legacy["metadata"])
                    )
                    and reusable_result(legacy["value"])
                ):
                    self.tasks.plan(
                        logical_id, **{k: v for k, v in self.task_context.items() if k != "id"}
                    )
                    self.tasks.update(
                        logical_id,
                        "completed",
                        cache_path=legacy_path.relative_to(self.config.root).as_posix(),
                        cache_key=legacy_key,
                    )
            except VaultError:
                pass
        try:
            result = self.cached(
                "evaluations" if evaluation else NAMESPACES[role],
                [stage_recipe(self.config, role, provider), role, payload],
                invoke,
                reusable=reusable_result,
                metadata=lambda: metadata,
            )
            if reused:
                metadata.update(self.last_cache_metadata or {"legacy_cache": True})
            validate_output(role, result)
            check(result)
        except (VaultError, OSError, KeyboardInterrupt) as exc:
            latest = getattr(self.agent, "last_metadata", {})
            if not reused and latest.get("provider") == selected and latest.get("role") == role:
                metadata.update(latest)
            metadata.update(
                validation_status="failed", failure=str(exc), failure_type=type(exc).__name__
            )
            metadata["error_type"] = getattr(
                exc, "code", "invalid_output" if isinstance(exc, VaultError) else type(exc).__name__
            )
            self.record_operation({**metadata, "cached": reused})
            self.task_context = None
            raise
        self.task_context = None
        self.record_operation({**metadata, "cached": reused})
        self.completed_operations += 1
        comparison = self.config.data.get("llm", {}).get("evaluation", {})
        if not evaluation and comparison.get("enabled") and comparison["provider"] != selected:
            try:
                self.run(role, payload, validator, provider=comparison["provider"], evaluation=True)
            except (VaultError, OSError) as exc:
                self.evaluation_failures.append(
                    {"role": role, "provider": comparison["provider"], "error": str(exc)}
                )
        return result

    def usage_by_agent(self):
        summary = {}
        for op in self.operations:
            role = op["role"]
            row = summary.setdefault(
                role, {"requests": 0, "cache_hits": 0, "failures": 0, "deferred": 0, "tokens": {}}
            )
            deferred = op.get("error_type") in {"allowance_exhausted", "deferred"}
            if op.get("cached") and op.get("validation_status") != "failed":
                row["cache_hits"] += 1
            elif not op.get("cached"):
                row["requests"] += 1
            if deferred:
                row["deferred"] += 1
            elif op.get("validation_status") == "failed":
                row["failures"] += 1
            if not op.get("cached"):
                for key, value in (op.get("usage") or {}).items():
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        row["tokens"][key] = row["tokens"].get(key, 0) + value
        return summary

    def record_operation(self, metadata):
        self.operations.append(metadata)
        path = inside(self.config.root, ".pipeline/logs/operations.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(metadata, ensure_ascii=False) + "\n")

    def remember(self, result, warnings):
        self.summary_warnings[digest(result)] = list(
            {digest(w): w for w in [*self.warnings_for(result), *warnings]}.values()
        )
        return result

    def warnings_for(self, result):
        return self.summary_warnings.get(digest(result), [])

    def extract(self, path, source, sha256):
        self.task_context = None
        key = [self.extraction_recipe, source, sha256]

        def page_cache(index, operation):
            return self.cached(
                "extractions",
                key + ["page", index + 1],
                operation,
                reusable=lambda page: extraction_complete([page]),
            )

        return self.cached(
            "extractions",
            key,
            lambda: pdf(path, source, self.config.ocr, page_cache),
            reusable=extraction_complete,
        )

    def batches(self, role, header, pages):
        budget = payload_budget(self.config, role)
        # JSON escaping can expand characters sixfold. Check actual serialized sizes too.
        room = budget - size({**header, "pages": []}) - 256
        if room < 512:
            raise VaultError("Instructions/metadata/output reserve leave no usable document budget")
        context = min(self.config.processing("chunk_context_chars"), max(0, room // 48))
        chars = min(self.config.processing("chunk_chars"), max(1, room // 6 - 2 * context))
        batch = []
        for page in section_chunks(pages, chars, context):
            if not fits(self.config, role, {**header, "pages": [page]}):
                raise VaultError(
                    "Source locator/metadata exceeds per-request budget; no truncation"
                )
            if batch and (
                size(batch + [page]) > self.config.processing("chunk_chars")
                or not fits(self.config, role, {**header, "pages": batch + [page]})
            ):
                yield {**header, "pages": batch}
                batch = []
            batch.append(page)
        if batch:
            yield {**header, "pages": batch}

    def summarize(self, role, payload):
        pdf_pages = [p for p in payload["pages"] if "extraction_status" in p]
        if pdf_pages:
            if not usable_pages(pdf_pages):
                raise VaultError("No processable PDF text; unprocessed pages were not read")
            payload = {
                **payload,
                "page_processing": page_coverage(payload["pages"]),
                "coverage": "complete" if extraction_complete(payload["pages"]) else "partial",
                "pages": usable_pages(payload["pages"]),
            }
        pages = payload["pages"]
        if not pages:
            raise VaultError("No processable document text; unprocessed pages were not read")
        if fits(self.config, role, payload) and size(pages) <= self.config.processing(
            "chunk_chars"
        ):
            result = self.run(role, payload)
            validate_summary(result, pages)
            return result
        # Preserve all metadata/annotations as labeled records if it cannot accompany every chunk.
        header = {k: v for k, v in payload.items() if k != "pages"}
        if size(header) > max(512, payload_budget(self.config, role) // 4):
            pages = pages + [
                {
                    "source": "document-metadata",
                    "locator": "attribution/commentary only",
                    "text": json.dumps(header, ensure_ascii=False),
                }
            ]
            header = {}
        header = {
            **header,
            "processing_stage": "chunk: preserve mathematics, assumptions, results and limitations; partial document only",
        }
        chunks = []
        for batch in self.batches(role, header, pages):
            result = self.run(role, batch)
            validate_summary(result, batch["pages"])
            chunks.append(result)
        if not chunks:
            raise VaultError("No processable document text")
        return self.combine(role, chunks)

    def combine(self, role, chunks):
        """Consolidate summaries, retaining every original claim and its exact provenance."""
        if len(chunks) == 1:
            return chunks[0]
        # Hierarchical overviews are navigational; every detailed chunk summary is retained.
        level = chunks
        warnings = [w for chunk in chunks for w in self.warnings_for(chunk)]
        for depth in range(12):
            if len(level) == 1:
                break
            records = [
                {
                    "source": f"chunk-{i + 1}",
                    "locator": "validated partial summary",
                    "text": item["summary"],
                }
                for i, item in enumerate(level)
            ]
            reduced = []
            for batch in self.batches(
                role,
                {
                    "processing_stage": "consolidation: reconcile supplied partial summaries; retain uncertainty and source attribution"
                },
                records,
            ):
                result = self.run(role, batch)
                validate_summary(result, batch["pages"])
                warnings.extend(self.warnings_for(result))
                reduced.append(result)
            if len(reduced) >= len(level) and size(reduced) >= size(level):
                raise VaultError(
                    "Consolidation made no progress within request budget; chunk results cached for retry"
                )
            level = reduced
        if len(level) != 1:
            raise VaultError("Consolidation depth exceeded; completed chunks remain cached")
        result = {
            "title": level[0]["title"],
            "summary": "Consolidated overview from partial summaries; inspect the original-source statements and detailed evidence below.\n\n"
            + level[0]["summary"]
            + "\n\n## Detailed partial summaries (all chunks retained)\n\n"
            + "\n\n".join(f"### Chunk {i + 1}\n{c['summary']}" for i, c in enumerate(chunks)),
            "evidence": [e for chunk in chunks for e in chunk["evidence"]],
        }
        result["claims"] = [claim for chunk in chunks for claim in chunk["claims"]]
        return self.remember(result, warnings)


def terms(text):
    return Counter(
        w
        for w in re.findall(r"[^\W_]{3,}", text.casefold())
        if w
        not in {
            "the",
            "and",
            "for",
            "with",
            "this",
            "that",
            "from",
            "source",
            "metadata",
            "evidence",
            "processed",
            "true",
            "literature",
            "page",
            "attachment",
            "title",
        }
    )


def graph_batches(config, notes, changed, membership, passages, statuses=None):
    """Standalone summary retrieval; the pipeline supplies its persistent Registry instead."""
    from .registry import Registry

    class IndexOnly:
        full = False

        def run(self, role, payload, validator):
            return {"concepts": []}

    registry = Registry()
    for name, text in notes.items():
        registry.update(config, name, text, {}, IndexOnly())
    yield from registry.batches(config, notes, changed, membership, passages, statuses or {})
