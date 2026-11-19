# Transactions, backups and recovery

## Failed synchronization

A source or reasoning failure before integration leaves old notes, review
registrations and successful membership state in place. Staged Codex input/output
may remain for diagnosis. Logs and Home's error indicator may change. Fix the
source/configuration/authentication problem and rerun the same command. `--full`
is usually unnecessary.

All mutating CLI commands hold an OS lock in `.pipeline/run.lock`. The presence of
that file alone does not mean a process is running. The lock is released when the
process exits; never “unlock” a live process by deleting the path.

At integration, `storage.commit` first copies all prior bytes to
`.pipeline/rollback/` and atomically writes `.pipeline/transaction.json`. It then
replaces each destination atomically. Removing the journal is the commit point.
An ordinary exception rolls the batch back immediately. After abrupt termination,
the next normal `sync`/`apply` restores every pre-transaction file, including state,
before doing any new work. Do not delete the journal or rollback directory.
Status reports an outstanding journal; dry runs refuse it. Read-only status during
an active write may observe an interim state; wait and run it again.

Source bytes are hashed and rechecked after extraction/inference. Before sync
integration, the pipeline also verifies that notes, existing reviews and Home were
not edited during reasoning. If they changed, the user edit wins and the staged
run is not committed. This is a conflict guard, not a lock on Obsidian; avoid editing
notes while a short `apply` batch writes them.

A filesystem/OS crash affecting the storage medium itself cannot be fully guarded
against by an application journal. Keep an independent backup. Only one machine
should run this vault at a time; OS locks are local and cloud file-sync systems
are not distributed transaction managers.

## Deletion checkpoints

Deletion requires an explicitly approved removal decision, an unchanged reviewed
note and unchanged link-impact preview, a Git executable, a committed repository
rooted at the vault, and no tracked modifications. Generated data is normally
ignored, so the actual checkpoint is a verified ZIP with SHA256 manifest. The
checkpoint includes old bytes for every changed file and records which destination
paths did not previously exist. State and Home are included. The backup path is
returned in the apply result. `.pipeline/last-impact.json` records the planned impact.

Before deletion use `apply --dry-run`, review incoming/outgoing relationships and
save/commit tracked changes. The Python code cleans only generated links regions.
Personal notes and user sections remain intact even if they now have a broken
link; revision records ask the user to reconsider the relationship and scientific
claim. Do not enable Obsidian's automatic link rewriting during this workflow.

## Restore a deletion backup

Stop pipeline commands and close Obsidian before restoring. Make an independent
copy of the entire current vault, including ignored data. Restore only a backup
created by this vault; it contains state/history specific to the project. Restoring
a checkpoint intentionally replaces later versions of its affected files, so first
inspect the ZIP's MANIFEST.json and the current files.

Use the included script from the project root:

```bash
.venv/bin/python scripts/restore_backup.py .pipeline/backups/BACKUP_ID.zip
```

PowerShell:

```powershell
& .\.venv\Scripts\python.exe scripts\restore_backup.py .pipeline\backups\BACKUP_ID.zip
```

The script checks archive paths and hashes, takes the same pipeline lock, recovers
an unfinished transaction if needed, and restores the checkpoint through the same
write-ahead transaction mechanism. Files listed as previously absent are removed.
The backup archive itself is retained. It never executes archive contents or Git
commands, and refuses a checkpoint from another project identity.

After restoration, review statuses are restored too. In particular a removal file
may again say approved/delete. **Change that decision to pending or rejected before
running apply again.** Open Obsidian, inspect the restored note, then run status
and sync. Restoring state is essential: restoring only the literature Markdown
would leave approval/history inconsistent with the deleted/active identity.

## Working on the code

Install with `pip install -e '.[dev]'`. The package is editable, so updates to src
are used by the CLI immediately. Role instruction changes, code changes and Codex
settings change the processing recipe and can cause reprocessing. Note layouts in
Templates are documentation/manual reference; automatic summaries use the stable
managed-section renderer in notes.py. Keep marker contracts backward compatible
or provide a state/note migration. The current state schema is 1.

The installed Python package is not a standalone vault initializer: scripts and
agent rules must remain in the cloned vault. Use GitHub's template feature for a
new project, not an arbitrary empty working directory with only the wheel installed.
