"""Finite detached CLI launches and durable reports; no persistent worker or shell."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid

from .storage import VaultError, atomic, inside, lock, now, read_json


def launch(config, full=False, startup=False):
    # Detect an existing writer before spawning. The child also takes the same OS lock;
    # simultaneous launch requests can never execute synchronization concurrently.
    with lock(config.root):
        pass
    run_id = uuid.uuid4().hex
    record = {
        "id": run_id,
        "status": "launching",
        "started_at": now(),
        "log": f".pipeline/runs/{run_id}.log",
        "full": full,
        "startup": startup,
        "provider_override": config.provider_override,
    }
    record_path = inside(config.root, f".pipeline/runs/{run_id}.json")
    atomic(record_path, json.dumps(record, indent=2))
    command = [
        sys.executable,
        "-m",
        "research_vault",
        "--vault",
        str(config.root),
        "sync",
        "--run-id",
        run_id,
    ]
    if full:
        command.append("--full")
    if startup:
        command.append("--startup")
    if config.provider_override:
        command.extend(["--provider", config.provider_override])
    options = (
        {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    try:
        with inside(config.root, record["log"]).open("ab") as log:
            process = subprocess.Popen(
                command,
                cwd=config.root,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                close_fds=True,
                **options,
            )
    except OSError as exc:
        finish(config.root, run_id, {"error": str(exc)}, 1)
        raise VaultError(f"Cannot launch synchronization: {exc}") from exc
    # The child owns the record after spawn; never overwrite a fast child's final report.
    return {
        **record,
        "pid": process.pid,
        "report": str(record_path.relative_to(config.root)),
        "message": "Synchronization launched. Use status or Home.md for progress and the final report.",
    }


def finish(root, run_id, result, exit_code):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise VaultError("Invalid background run identifier")
    path = inside(root, f".pipeline/runs/{run_id}.json")
    record = read_json(path, {"id": run_id})
    record.update(
        status="complete" if exit_code == 0 else "incomplete" if exit_code == 2 else "failed",
        finished_at=now(),
        exit_code=exit_code,
        result=result,
    )
    atomic(path, json.dumps(record, ensure_ascii=False, indent=2))


def running(root, run_id):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise VaultError("Invalid background run identifier")
    path = inside(root, f".pipeline/runs/{run_id}.json")
    record = read_json(path, {"id": run_id})
    record.update(status="running", pid=os.getpid())
    atomic(path, json.dumps(record, indent=2))
