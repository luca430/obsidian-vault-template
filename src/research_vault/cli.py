"""CLI shared by the terminal and Obsidian Shell Commands."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .config import PROVIDERS, Config
from .execution import finish, launch, running
from .pipeline import Pipeline
from .storage import VaultError, atomic, inside, lock, now, recover


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One-shot, review-driven research vault")
    parser.add_argument(
        "--vault", type=Path, default=Path.cwd(), help="Vault root (default: current directory)"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("sync", help="Synchronize; never apply pending reviews")
    sync.add_argument("--full", action="store_true")
    sync.add_argument("--dry-run", action="store_true")
    sync.add_argument(
        "--background",
        action="store_true",
        help="Launch a finite detached run and return immediately",
    )
    sync.add_argument(
        "--startup",
        action="store_true",
        help="Bounded incremental documents; defer conceptual reasoning",
    )
    sync.add_argument("--run-id", help=argparse.SUPPRESS)
    sync.add_argument(
        "--provider", choices=PROVIDERS, help="Override every agent's provider for this run only"
    )
    apply = commands.add_parser(
        "apply", help="Apply validated, explicitly approved review decisions"
    )
    apply.add_argument(
        "--dry-run", action="store_true", help="Show exact affected paths and removal impact"
    )
    commands.add_parser("status")
    commands.add_parser("doctor")
    args = parser.parse_args(argv)
    run_id = getattr(args, "run_id", None)
    if run_id and not re.fullmatch(r"[a-f0-9]{32}", run_id):
        parser.error("Invalid background run identifier")

    def progress(event):
        print(json.dumps(event, ensure_ascii=False), file=sys.stderr, flush=True)
        if args.command == "sync" and not args.dry_run:
            atomic(
                inside(args.vault.resolve(), ".pipeline/progress.json"),
                json.dumps({"time": now(), **event}),
            )

    try:
        config = Config.load(args.vault)
        if args.command == "sync" and args.provider:
            config = config.with_provider(args.provider)
        pipeline = Pipeline(
            config,
            progress=progress,
        )
        dry = getattr(args, "dry_run", False)
        if args.command == "sync":
            if (args.full and args.startup) or (args.background and (dry or run_id)):
                raise VaultError(
                    "Use --startup without --full, and --background without --dry-run/--run-id"
                )
            if args.background:
                print(json.dumps(launch(pipeline.config, args.full, args.startup), indent=2))
                return 0
        if dry or args.command in {"status", "doctor"}:
            if dry and inside(pipeline.root, ".pipeline/transaction.json").exists():
                raise VaultError(
                    "Interrupted transaction exists; run sync/apply without --dry-run to recover first"
                )
            result = (
                pipeline.sync(full=args.full, dry_run=True, startup=args.startup)
                if args.command == "sync"
                else pipeline.apply(dry_run=True)
                if args.command == "apply"
                else getattr(pipeline, args.command)()
            )
        else:
            with lock(pipeline.root):
                if run_id:
                    running(pipeline.root, run_id)
                recover(pipeline.root)
                try:
                    result = (
                        pipeline.sync(full=args.full, startup=args.startup)
                        if args.command == "sync"
                        else pipeline.apply()
                    )
                except (VaultError, OSError, ValueError, KeyboardInterrupt) as exc:
                    pipeline.report_error(str(exc) or "Interrupted execution; run again to retry")
                    raise
        print(json.dumps(result, ensure_ascii=False, indent=2))
        exit_code = (
            1
            if args.command in {"doctor", "sync"} and result.get("blocking_errors")
            else 2
            if args.command == "sync" and not dry and not result.get("complete", True)
            else 0
        )
        if run_id:
            finish(pipeline.root, run_id, result, exit_code)
        return exit_code
    except (VaultError, OSError, ValueError) as exc:
        print(f"research-vault: {exc}", file=sys.stderr)
        if run_id:
            finish(args.vault.resolve(), run_id, {"error": str(exc)}, 1)
        return 1
    except KeyboardInterrupt:
        if run_id:
            finish(
                args.vault.resolve(),
                run_id,
                {"error": "Interrupted; completed operations remain cached"},
                130,
            )
        print("research-vault: interrupted; unfinished work will be retried", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
