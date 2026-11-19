"""Safe paths, atomic writes, OS locks, and write-ahead rollback journals."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import yaml


class VaultError(Exception):
    """An actionable failure; unsuccessful work must remain retryable."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def inside(root: Path, name: str) -> Path:
    p = root / name
    if Path(name).is_absolute() or ".." in Path(name).parts:
        raise VaultError(f"Unsafe vault path: {name}")
    # Reject symlinks even if they currently point inside the vault.
    if any(x.is_symlink() for x in [p, *p.parents] if x != root.parent):
        raise VaultError(f"Symlink paths are not writable: {name}")
    if not p.resolve().is_relative_to(root.resolve()):
        raise VaultError(f"Path escapes vault: {name}")
    return p


def atomic(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(content.encode() if isinstance(content, str) else content)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise VaultError(f"Invalid state {path.name}: {exc}") from exc


def frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise VaultError("Missing YAML frontmatter")
    head, body = text[4:].split("\n---\n", 1)
    try:
        data = yaml.safe_load(head)
    except yaml.YAMLError as exc:
        raise VaultError(f"Malformed YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise VaultError("Frontmatter must be a mapping")
    return data, body


def markdown(meta: dict, body: str) -> str:
    return "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---\n" + body


def replace_section(text: str, section: str, body: str) -> str:
    start, end = f"<!-- {section}:start -->", f"<!-- {section}:end -->"
    if text.count(start) != 1 or text.count(end) != 1 or text.index(end) < text.index(start):
        raise VaultError(f"Missing or ambiguous managed markers: {section}")
    a, tail = text.split(start)
    _, b = tail.split(end)
    return a + start + "\n" + body.rstrip() + "\n" + end + b


@contextmanager
def lock(root: Path) -> Iterator[None]:
    path = inside(root, ".pipeline/run.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise VaultError("Another pipeline run holds the vault lock") from exc
        try:
            stream.seek(0)
            if not stream.read(1):
                stream.write(b"0")
                stream.flush()
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def recover(root: Path) -> bool:
    journal = inside(root, ".pipeline/transaction.json")
    entries = read_json(journal)
    if entries is None:
        return False
    for name, backup in entries.items():
        target = inside(root, name)
        if backup is None:
            target.unlink(missing_ok=True)
        else:
            atomic(target, inside(root, backup).read_bytes())
    journal.unlink()
    return True


def commit(root: Path, changes: dict[str, str | bytes | None]) -> None:
    """Journal all old bytes before changing anything; state is part of the batch.

    An interrupted batch is rolled back under the next run's lock. Backups are kept
    until that recovery or the next commit, so kill/power-loss cannot lose old notes.
    """
    journal = inside(root, ".pipeline/transaction.json")
    if journal.exists():
        raise VaultError("Recover the pending transaction before committing")
    backup_dir = inside(root, ".pipeline/rollback")
    shutil.rmtree(backup_dir, ignore_errors=True)
    backup_dir.mkdir(parents=True)
    entries = {}
    for i, name in enumerate(changes):
        target = inside(root, name)
        backup = f".pipeline/rollback/{i}"
        entries[name] = backup if target.exists() else None
        if target.exists():
            atomic(inside(root, backup), target.read_bytes())
    atomic(journal, json.dumps(entries))
    try:
        for name, content in changes.items():
            target = inside(root, name)
            if content is None:
                target.unlink(missing_ok=True)
            else:
                atomic(target, content)
        journal.unlink()  # Commit point. Backups remain until next transaction.
    except BaseException:
        recover(root)
        raise
