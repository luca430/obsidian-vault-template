"""Private persistent concept annotations and an incremental bounded inverted index.

Annotations and their document associations are candidates, never approved graph edges.
"""

from __future__ import annotations

import math
from collections import Counter

from .agents import payload_budget, validate_note_evidence
from .identities import registered_relationship_key, relationship_key
from .processing import fragments, size, terms
from .recipes import stage_recipe
from .storage import VaultError, digest, frontmatter


def redirected(text):
    return text.startswith("---\n") and frontmatter(text)[0].get("kind") == "concept-redirect"


class Registry:
    def __init__(self, data=None):
        self.data = (
            data
            if data is not None
            else {
                "schema": 1,
                "documents": {},
                "concepts": {},
                "postings": {},
                "reviewed_relationships": {},
            }
        )
        if self.data.get("schema") != 1 or any(
            not isinstance(self.data.get(k), dict)
            for k in ("documents", "concepts", "postings", "reviewed_relationships")
        ):
            raise VaultError("Concept registry is damaged; restore state from backup")

    def remove(self, name):
        previous = self.data["documents"].pop(name, {})
        for fragment in previous.get("fragments", []):
            for token in fragment["terms"]:
                posting = self.data["postings"].get(token, [])
                if fragment["id"] in posting:
                    posting.remove(fragment["id"])
                if not posting:
                    self.data["postings"].pop(token, None)
        for key in previous.get("concept_ids", []):
            concept = self.data["concepts"].get(key)
            if concept:
                concept["sources"].pop(name, None)
                if not concept["sources"]:
                    del self.data["concepts"][key]

    def update(self, config, name, text, provenance, processor):
        if redirected(text):
            self.remove(name)
            return False
        old = self.data["documents"].get(name, {})
        signature = digest(text)
        concept_recipe = stage_recipe(config, "concepts")
        index_recipe = stage_recipe(config, "retrieval")
        if (
            old.get("fingerprint") == signature
            and old.get("recipe") == concept_recipe
            and old.get("index_recipe") == index_recipe
            and not processor.full
        ):
            old["provenance"] = provenance
            return False
        annotations = old.get("annotations", [])
        validation_warnings = old.get("validation_warnings", [])
        if (
            old.get("fingerprint") != signature
            or old.get("recipe") != concept_recipe
            or processor.full
        ):
            annotations = []
            validation_warnings = []
            budget = payload_budget(config, "concepts")
            if budget < 1500:
                raise VaultError("Concept extraction budget cannot fit instructions and context")
            for fragment in fragments([{"text": text}], max(64, budget // 12)):
                payload = {"notes": {name: fragment["text"]}, "provenance": provenance}

                def check(result):
                    for concept in result["concepts"]:
                        validate_note_evidence(concept["evidence"], payload["notes"])
                        validate_note_evidence(concept["evidence"], {name: text})

                primary = processor.run("concepts", payload, check)["concepts"]
                for concept in primary:
                    validation_warnings.extend(
                        validate_note_evidence(concept["evidence"], payload["notes"])
                    )
                    validation_warnings.extend(
                        validate_note_evidence(concept["evidence"], {name: text})
                    )
                annotations.extend(primary)
            # Unverified annotations cannot supply established concepts or retrieval terms.
            annotations = [
                a for a in annotations if not validate_note_evidence(a["evidence"], {name: text})
            ]
        self.remove(name)
        # Exact annotation identity is an indexing convention, not scientific equivalence.
        concept_ids = []
        for annotation in annotations:
            key = digest([annotation["title"], annotation["definition"]])
            concept = self.data["concepts"].setdefault(
                key,
                {
                    "title": annotation["title"],
                    "definition": annotation["definition"],
                    "status": "unreviewed_retrieval_annotation",
                    "sources": {},
                },
            )
            concept["sources"][name] = {
                "evidence": annotation["evidence"],
                "provenance": provenance,
            }
            concept_ids.append(key)
        extra_terms = set(terms(" ".join(a["title"] + " " + a["definition"] for a in annotations)))
        indexed = []
        chars = config.processing("graph_fragment_chars")
        for fragment in fragments([{"text": text}], chars):
            start = fragment["character_offset"]
            key = digest([name, start, fragment["text"]])
            vocabulary = sorted(set(terms(fragment["text"])) | extra_terms)
            indexed.append(
                {
                    "id": key,
                    "start": start,
                    "end": start + len(fragment["text"]),
                    "terms": vocabulary,
                }
            )
            for token in vocabulary:
                posting = self.data["postings"].setdefault(token, [])
                posting.append(key)
                posting.sort()
        self.data["documents"][name] = {
            "fingerprint": signature,
            "recipe": concept_recipe,
            "index_recipe": index_recipe,
            "provenance": provenance,
            "annotations": annotations,
            "validation_warnings": list({digest(w): w for w in validation_warnings}.values()),
            "concept_ids": sorted(set(concept_ids)),
            "fragments": indexed,
        }
        return True

    def reviewed(self, proposals, notes=None):
        relationships = {}
        for pid, record in sorted(proposals.items()):
            links = (
                [record["payload"]]
                if record["kind"] == "link"
                else record["payload"].get("links", [])
                if record["kind"] == "consolidation"
                else []
            )
            for link in links:
                key = (
                    registered_relationship_key(record, notes or {})
                    if record["kind"] == "link"
                    else relationship_key(
                        link, {**(notes or {}), **record.get("expected_notes", {})}
                    )
                )
                identity = digest(key)
                relationship = relationships.setdefault(
                    identity,
                    {
                        "identity": key,
                        "proposals": [],
                        "approved": False,
                        "decisions": [],
                    },
                )
                relationship["proposals"].append(pid)
                relationship["approved"] |= bool(record.get("applied"))
                relationship["decisions"].append(
                    {
                        "proposal": pid,
                        "decision": record.get("decision", "pending"),
                        "applied_at": record.get("applied_at"),
                        "resolution": record.get("resolution"),
                        "superseded_by": record.get("superseded_by"),
                    }
                )
        self.data["relationships"] = relationships
        self.data["relationship_history"] = {
            pid: {
                "source": record["payload"]["source"],
                "target": record["payload"]["target"],
                "relationship": record["payload"]["relationship"],
                "identity": registered_relationship_key(record, notes or {}),
                "decision": record.get("decision", "pending"),
                "applied": record.get("applied", False),
                "superseded_by": record.get("superseded_by"),
                "resolution": record.get("resolution"),
            }
            for pid, record in sorted(proposals.items())
            if record["kind"] == "link"
        }
        redirects = {}
        rewritten = {}
        for pid, record in proposals.items():
            if (
                record["kind"] == "consolidation"
                and record.get("applied")
                and record["payload"]["operation"] == "merge"
            ):
                payload = record["payload"]
                redirects.update(
                    {
                        p: (payload["canonical"]["path"], pid)
                        for p in payload["members"]
                        if p != payload["canonical"]["path"]
                    }
                )
                rewritten.update(
                    {
                        (r["source"], r["previous_target"]): (r["target"], pid)
                        for r in record.get("rewritten_links", [])
                    }
                )
        relationships = {
            pid: record["payload"]
            for pid, record in proposals.items()
            if record["kind"] == "link"
            and record.get("applied")
            and record["payload"]["source"] not in redirects
        }
        for pid, record in proposals.items():
            if record["kind"] == "consolidation" and record.get("applied"):
                for number, link in enumerate(record["payload"]["links"]):
                    if link["source"] not in redirects:
                        relationships[f"{pid}:{number}"] = link
        for pid, payload in list(relationships.items()):
            target = payload["target"]
            seen = set()
            approvals = []
            while (payload["source"], target) in rewritten and target not in seen:
                seen.add(target)
                target, approval = rewritten[(payload["source"], target)]
                approvals.append(approval)
            if approvals:
                relationships[pid] = {
                    **payload,
                    "target": target,
                    "consolidation_approvals": approvals,
                }
        self.data["reviewed_relationships"] = relationships

    def batches(self, config, notes, changed, membership, passages, statuses):
        available = min(
            payload_budget(config, role) for role in ("linking", "synthesis", "maintenance")
        )
        if available < 1500:
            raise VaultError("Instructions/schema/output reserve leave no usable graph budget")
        lookup = {
            fragment["id"]: (name, fragment)
            for name, doc in self.data["documents"].items()
            if name in notes
            for fragment in doc["fragments"]
        }
        seen = set()
        for name in sorted(changed & notes.keys()):
            document = self.data["documents"].get(name)
            if document is None:
                if redirected(notes[name]):
                    continue
                raise VaultError(f"Concept indexing incomplete for {name}; retry sync")
            # Query the persistent postings, never compare each pair of documents.
            for focus in document["fragments"]:
                weights = Counter()
                pool = config.processing("candidate_pool")
                # Singleton metadata/identifiers cannot retrieve another source. Exclude
                # self-only terms before spending the bounded scientific query budget.
                query = sorted(
                    (
                        token
                        for token in focus["terms"]
                        if any(
                            key in lookup and lookup[key][0] != name
                            for key in self.data["postings"].get(token, [])[:pool]
                        )
                    ),
                    key=lambda t: (len(self.data["postings"].get(t, [])), t),
                )
                for token in query[: config.processing("candidate_terms")]:
                    posting = self.data["postings"].get(token, [])
                    weight = math.log(1 + len(lookup) / max(1, len(posting)))
                    for candidate in posting[:pool]:
                        if candidate in lookup and lookup[candidate][0] != name:
                            if candidate in weights or len(weights) < pool:
                                weights[candidate] += weight
                neighbors, names = [], {name}
                for key, _ in weights.most_common():
                    target, fragment = lookup[key]
                    if target not in names:
                        neighbors.append((target, fragment))
                        names.add(target)
                    if len(neighbors) >= config.processing("graph_neighbors"):
                        break
                # Repartition an oversized focus; all characters of changed notes remain covered.
                focus_text = notes[name][focus["start"] : focus["end"]]
                for piece in fragments([{"text": focus_text}], max(64, available // 32)):
                    payload = {
                        "focus_fragment": {
                            "note": name,
                            "character_offset": focus["start"] + piece["character_offset"],
                        },
                        "notes": {name: piece["text"]},
                        "source_passages": [],
                        "active_literature": [],
                        "document_status": {},
                        "concept_candidates": [],
                        "scope": "Bounded concept-registry candidates; missing retrieval is not evidence of no relationship. Citations are attribution only.",
                        "note_categories": {
                            "Literature": "external findings",
                            "My Work": "manuscript claims",
                            "My Notes": "personal hypotheses",
                            "Concepts": "reviewed concepts",
                        },
                    }
                    for target, fragment in neighbors:
                        # One bounded original summary excerpt for each candidate document.
                        excerpt = notes[target][
                            fragment["start"] : min(
                                fragment["end"], fragment["start"] + max(64, available // 32)
                            )
                        ]
                        candidate = {**payload, "notes": {**payload["notes"], target: excerpt}}
                        if size(candidate) <= available * 0.65:
                            payload = candidate
                    context = payload["notes"]
                    payload["active_literature"] = [
                        k
                        for k in membership
                        if any(
                            n == f"Literature/{k}.md" or n.endswith(f" — {k}.md") for n in context
                        )
                    ]
                    payload["existing_relationships"] = []
                    for pid, relationship in sorted(
                        self.data.get("relationship_history", {}).items()
                    ):
                        if not {relationship["source"], relationship["target"]} & context.keys():
                            continue
                        candidate = {
                            **payload,
                            "existing_relationships": payload["existing_relationships"]
                            + [{"proposal": pid, **relationship}],
                        }
                        if size(candidate) <= available * 0.75:
                            payload = candidate
                    payload["document_status"] = {n: statuses[n] for n in context if n in statuses}
                    for n in context:
                        for key in self.data["documents"][n]["concept_ids"]:
                            concept = self.data["concepts"][key]
                            record = {
                                "id": key,
                                "title": concept["title"],
                                "definition": concept["definition"],
                                "source": n,
                                "status": concept["status"],
                            }
                            candidate = {
                                **payload,
                                "concept_candidates": payload["concept_candidates"] + [record],
                            }
                            if size(candidate) <= available * 0.8:
                                payload = candidate
                    query_terms = set(terms(" ".join(context.values())))
                    ranked = sorted(
                        (
                            {**part, "note": n, "availability": statuses.get(n, {})}
                            for n in context
                            for part in fragments(
                                passages.get(n, []), config.processing("passage_chars")
                            )
                        ),
                        key=lambda p: (
                            -len(query_terms & terms(p["text"]).keys()),
                            p["note"] != name,
                        ),
                    )
                    for record in ranked[: config.processing("retrieval_passages")]:
                        candidate = {
                            **payload,
                            "source_passages": payload["source_passages"] + [record],
                        }
                        if size(candidate) <= available:
                            payload = candidate
                    if size(payload) > available:
                        raise VaultError(
                            "Registry focus/provenance cannot fit configured graph budget"
                        )
                    signature = digest(payload)
                    if signature not in seen:
                        seen.add(signature)
                        yield payload
