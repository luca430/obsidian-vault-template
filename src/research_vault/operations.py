"""Atomic per-operation receipts; successful outputs precede scientific integration."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from .storage import VaultError, atomic, inside, now, read_json

STATUSES = {"pending", "running", "completed", "failed", "deferred"}


class OperationStore:
    def __init__(self, root, *, recover_running=False):
        # Accept the original aggregate journal too. Per-task files supersede its
        # entries, avoiding quadratic rewrites for large collections.
        self.path = inside(root, ".pipeline/operations.json")
        self.directory = inside(root, ".pipeline/operations")
        self.data = read_json(self.path, {"schema": 1, "operations": {}})
        if self.data.get("schema") != 1 or not isinstance(self.data.get("operations"), dict):
            raise VaultError("Invalid operation journal; restore it from backup")
        for path in sorted(self.directory.glob("*.json")):
            record = read_json(inside(root, path.relative_to(root).as_posix()))
            if not isinstance(record, dict) or record.get("id") != path.stem:
                raise VaultError("Invalid task receipt; restore the affected receipt from backup")
            self.records[path.stem] = record
        if any(
            not isinstance(key, str)
            or not re.fullmatch(r"[a-f0-9]{64}", key)
            or not isinstance(r, dict)
            or r.get("status") not in STATUSES
            for key, r in self.records.items()
        ):
            raise VaultError("Invalid task status in operation journal")
        if recover_running:
            for key, r in self.records.items():
                if r["status"] == "running":
                    self.update(
                        key,
                        "pending",
                        reason="Previous process stopped before a durable completion receipt",
                    )

    @property
    def records(self):
        return self.data["operations"]

    def save(self, key):
        atomic(
            self.directory / (key + ".json"),
            json.dumps(self.records[key], ensure_ascii=False, sort_keys=True),
        )

    def plan(self, key, **details):
        record = self.records.get(key)
        if record is None:
            record = {"id": key, "status": "pending", "created_at": now(), "attempts": 0, **details}
            self.records[key] = record
            self.save(key)
        return record

    def update(self, key, status, **details):
        if status not in STATUSES:
            raise VaultError("Invalid task transition")
        record = self.records[key]
        record.setdefault("events", []).append(
            {
                "status": status,
                "at": now(),
                **{k: v for k, v in details.items() if k in {"reason", "error_type"}},
            }
        )
        record.update(status=status, updated_at=now(), **details)
        self.save(key)
        return record

    def defer_provider(self, provider, reason, retry_after=None, error_type="allowance_exhausted"):
        for key, r in self.records.items():
            if r.get("provider") == provider and r["status"] in {
                "pending",
                "running",
                "failed",
                "deferred",
            }:
                details = {"reason": reason, "error_type": error_type}
                if retry_after:
                    details["eligible_after"] = (
                        datetime.now(timezone.utc) + timedelta(seconds=retry_after)
                    ).isoformat()
                self.update(key, "deferred", **details)

    def eligible(self, record, *, retry=False):
        if retry:
            return True
        stamp = record.get("eligible_after")
        return not stamp or datetime.now(timezone.utc) >= datetime.fromisoformat(stamp)

    def summary(self):
        return {
            status: sum(r["status"] == status for r in self.records.values())
            for status in sorted(STATUSES)
        }
