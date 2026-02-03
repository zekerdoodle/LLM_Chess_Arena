# Manual Runbook: Apply Patch Backup

Use this runbook to validate that Theo's self-patching pipeline snapshots the codebase before patch application and falls back cleanly when backups fail.

## Preconditions
- Theo is running locally with access to the Git-based backup mechanism (see `self_patch_tools.py`).
- Ensure the scheduler has not created a fresh backup in the past minute so you can clearly identify the manual snapshot.
- Confirm you have a scratch patch file that makes a harmless change (e.g., edits `README.md`).

## Steps
1. **Trigger the backup-enabled patch flow**
   - Call the `apply_patch` tool through Theo or run `python theo.py apply-patch --patch-file <file>`.
   - Observe logs for `Starting backup before patch`. This should precede any git apply work.
2. **Validate backup artefacts**
   - Locate the timestamped backup archive under `vault/backups/`.
   - Confirm the directory contains a full Git bundle and metadata JSON with the patch hash.
3. **Induce a transient backup failure**
   - Temporarily make the backup directory read-only (`chmod 500 vault/backups`).
   - Re-run the patch flow and verify Theo reports a user-facing failure message without modifying the working tree.
   - Restore permissions afterwards.
4. **Verify restore-on-error behaviour**
   - Modify a tracked file to simulate partial patch application.
   - Rerun `apply_patch` and interrupt the process after the patch is applied but before completion (Ctrl+C).
   - Ensure the backup restore routine resets the repository state and the partial change disappears.

## Expected Results
- A new backup directory is created before each patch attempt.
- User-facing Discord/web notifications mention backup success/failure.
- On any failure, Theo refuses to proceed with patch application and suggests remediation (fix permissions, free disk space, etc.).

## Troubleshooting
- If the backup directory is missing, check `theo.log` for `BackupFailureError`.
- When backups succeed but the apply step fails, confirm that the auto-restore pulls the latest Git snapshot (not the vault).
- Persistent permission errors usually indicate a stale `index.lock`; clear it and retry.
