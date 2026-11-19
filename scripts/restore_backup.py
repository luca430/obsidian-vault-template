#!/usr/bin/env python3
"""Explicit restoration of a verified pipeline checkpoint into its original vault."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

from research_vault.config import Config
from research_vault.storage import VaultError, commit, inside, lock, recover


def restore(root: Path, backup: Path) -> None:
    config = Config.load(root)
    changes: dict[str, bytes | None] = {}
    try:
        with zipfile.ZipFile(backup) as archive:
            manifest = json.loads(archive.read("MANIFEST.json"))
            for name, expected in manifest["files"].items():
                inside(root, name)
                if not name.startswith(
                    ("Literature/", "Concepts/", "My Work/", "Reviews/")
                ) and name not in {"Home.md", ".pipeline/state.json"}:
                    raise VaultError(f"Unsupported checkpoint path: {name}")
                if expected is None:
                    changes[name] = None
                else:
                    data = archive.read(name)
                    if hashlib.sha256(data).hexdigest() != expected:
                        raise VaultError(f"Checkpoint hash mismatch: {name}")
                    changes[name] = data
    except (KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise VaultError(f"Malformed checkpoint: {exc}") from exc
    state_bytes = changes.get(".pipeline/state.json")
    if not state_bytes or json.loads(state_bytes).get("identity") != config.identity:
        raise VaultError("Checkpoint belongs to a different project or lacks state")
    with lock(root):
        recover(root)
        commit(root, changes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        restore(root, args.backup)
    except (VaultError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("Checkpoint restored. Review restored approval decisions before running apply again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
