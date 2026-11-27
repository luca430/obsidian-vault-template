"""CLI shared by the terminal and Obsidian Shell Commands."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .cancellation import cancellation
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
        "--retry", action="store_true", help="Retry unchanged failures and extraction limitations"
    )
    sync.add_argument("--control-id", help=argparse.SUPPRESS)
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
    apply.add_argument(
        "--ready", action="store_true", help="Explicitly apply only independently ready approvals"
    )
    apply.add_argument(
        "--proposal", action="append", help="Apply only these exact approved proposal IDs"
    )
    apply.add_argument("--plan-token", help="Require an unchanged selected preview")
    review = commands.add_parser(
        "review", help="Inspect or refresh reviews without synchronization"
    )
    review.add_argument("action", choices=["status", "refresh", "history", "reconsider"])
    review.add_argument("id", nargs="?")
    review.add_argument("--evidence-note")
    review.add_argument("--evidence-quote")
    migration = commands.add_parser(
        "migrate",
        help="Preview title filenames, bibliography and finalized review archiving; no inference",
    )
    migration.add_argument("--execute", action="store_true")
    migration.add_argument("--plan-token")
    migration.add_argument("--authorize-note", action="append", default=[])
    concepts = commands.add_parser(
        "concepts", help="Inspect concept candidates or request a pending consolidation"
    )
    concepts.add_argument("action", choices=["candidates", "suggest"])
    concepts.add_argument("id", nargs="?")
    concepts.add_argument(
        "--member",
        action="append",
        help="Explicit concept paths to compare instead of a candidate ID",
    )
    commands.add_parser("status")
    commands.add_parser("doctor")
    args = parser.parse_args(argv)
    if args.command == "apply" and args.ready and args.proposal:
        parser.error("Choose --ready or explicit --proposal IDs")
    if args.command == "concepts" and args.member and args.action != "suggest":
        parser.error("--member is only supported for concepts suggest")
    if getattr(args, "plan_token", None) and not re.fullmatch(r"[a-f0-9]{64}", args.plan_token):
        parser.error("Invalid preview token")
    for value in [
        *(getattr(args, "proposal", None) or []),
        *([args.id] if getattr(args, "id", None) else []),
    ]:
        if not re.fullmatch(r"[a-f0-9]{24}", value):
            parser.error("Review and candidate IDs must contain 24 hexadecimal characters")
    run_id = getattr(args, "run_id", None)
    control_id = getattr(args, "control_id", None)
    if control_id and not re.fullmatch(r"[a-f0-9]{32}", control_id):
        parser.error("Invalid sync control identifier")
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

        def synchronize():
            marker = inside(pipeline.root, f".pipeline/cancel/{control_id}") if control_id else None
            with cancellation(marker):
                return pipeline.sync(
                    full=args.full, dry_run=dry, startup=args.startup, retry=args.retry
                )

        if args.command == "migrate":
            from .migrations import migrate

            if args.execute:
                with lock(pipeline.root):
                    recover(pipeline.root)
                    result = migrate(pipeline, True, args.plan_token, args.authorize_note)
            else:
                if inside(pipeline.root, ".pipeline/transaction.json").exists():
                    raise VaultError("Recover the interrupted transaction before migration")
                result = migrate(pipeline, authorize_notes=args.authorize_note)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        def apply_selected():
            return pipeline.apply(
                dry_run=dry,
                proposal_ids=args.proposal,
                ready_only=args.ready,
                plan_token=args.plan_token,
            )

        if args.command in {"review", "concepts"}:
            if args.action == "history":
                state = pipeline.state()
                from .reviews import read_reviews

                reviews = read_reviews(pipeline.root, state["proposals"])
                result = [
                    {
                        "id": p,
                        "path": r["path"],
                        "kind": r["kind"],
                        "decision": reviews[p]["status"],
                        "outcome": r.get("application_outcome"),
                    }
                    for p, r in state["proposals"].items()
                    if r.get("archived_at")
                ]
                if args.id:
                    record = state["proposals"].get(args.id)
                    if not record or not record.get("archived_at"):
                        raise VaultError("Choose an archived review")
                    result = {
                        "record": record,
                        "markdown": inside(pipeline.root, record["path"]).read_text(
                            encoding="utf-8"
                        ),
                    }
                else:
                    result = {"history": result}
            elif args.action in {"status", "candidates"}:
                report = pipeline.review_status()
                result = report if args.action == "status" else report["concept_candidates"]
            else:
                if not args.id and not getattr(args, "member", None):
                    raise VaultError("Specify a review or candidate ID")
                if args.command == "concepts" and args.id and args.member:
                    raise VaultError("Choose a candidate ID or explicit members, not both")
                with lock(pipeline.root):
                    recover(pipeline.root)
                    result = (
                        pipeline.reconsider_review(args.id, args.evidence_note, args.evidence_quote)
                        if args.action == "reconsider"
                        else pipeline.refresh_review(args.id)
                        if args.command == "review"
                        else pipeline.suggest_consolidation(args.id, args.member)
                    )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "sync":
            if (args.full and args.startup) or (args.background and (dry or run_id)):
                raise VaultError(
                    "Use --startup without --full, and --background without --dry-run/--run-id"
                )
            if args.background:
                print(
                    json.dumps(
                        launch(pipeline.config, args.full, args.startup, args.retry), indent=2
                    )
                )
                return 0
        if dry or args.command in {"status", "doctor"}:
            if dry and inside(pipeline.root, ".pipeline/transaction.json").exists():
                raise VaultError(
                    "Interrupted transaction exists; run sync/apply without --dry-run to recover first"
                )
            result = (
                synchronize()
                if args.command == "sync"
                else apply_selected()
                if args.command == "apply"
                else getattr(pipeline, args.command)()
            )
        else:
            with lock(pipeline.root):
                if run_id:
                    running(pipeline.root, run_id)
                recover(pipeline.root)
                try:
                    result = synchronize() if args.command == "sync" else apply_selected()
                except (VaultError, OSError, ValueError, KeyboardInterrupt) as exc:
                    if not isinstance(exc, KeyboardInterrupt):
                        pipeline.report_error(str(exc))
                    raise
        print(json.dumps(result, ensure_ascii=False, indent=2))
        exit_code = (
            1
            if args.command in {"doctor", "sync"}
            and (result.get("blocking_errors") or result.get("status") == "failed")
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
        if (
            args.command == "sync"
            and not args.dry_run
            and "pipeline" in locals()
            and not getattr(pipeline, "_interruption_reported", False)
        ):
            pipeline.report_error("Synchronization interrupted; completed work remains resumable")
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
