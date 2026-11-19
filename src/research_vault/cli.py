"""CLI shared by the terminal and Obsidian Shell Commands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Config
from .pipeline import Pipeline
from .storage import VaultError, inside, lock, recover


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One-shot, review-driven research vault")
    parser.add_argument(
        "--vault", type=Path, default=Path.cwd(), help="Vault root (default: current directory)"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("sync", help="Synchronize; never apply pending reviews")
    sync.add_argument("--full", action="store_true")
    sync.add_argument("--dry-run", action="store_true")
    apply = commands.add_parser(
        "apply", help="Apply validated, explicitly approved review decisions"
    )
    apply.add_argument(
        "--dry-run", action="store_true", help="Show exact affected paths and removal impact"
    )
    commands.add_parser("status")
    commands.add_parser("doctor")
    args = parser.parse_args(argv)
    try:
        pipeline = Pipeline(Config.load(args.vault))
        dry = getattr(args, "dry_run", False)
        if dry or args.command in {"status", "doctor"}:
            if dry and inside(pipeline.root, ".pipeline/transaction.json").exists():
                raise VaultError(
                    "Interrupted transaction exists; run sync/apply without --dry-run to recover first"
                )
            result = (
                pipeline.sync(full=args.full, dry_run=True)
                if args.command == "sync"
                else pipeline.apply(dry_run=True)
                if args.command == "apply"
                else getattr(pipeline, args.command)()
            )
        else:
            with lock(pipeline.root):
                recover(pipeline.root)
                try:
                    result = (
                        pipeline.sync(full=args.full)
                        if args.command == "sync"
                        else pipeline.apply()
                    )
                except (VaultError, OSError, ValueError, KeyboardInterrupt) as exc:
                    pipeline.report_error(str(exc) or "Interrupted execution; run again to retry")
                    raise
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if args.command == "doctor" and any(v != "ok" for v in result.values()) else 0
    except (VaultError, OSError, ValueError) as exc:
        print(f"research-vault: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("research-vault: interrupted; unfinished work will be retried", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
