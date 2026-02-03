# Manual Runbook: Apply Patch Ordering

This checklist verifies that Theo's automated self-patch sequence respects the required order of operations: backup → clone → edit/test loop → apply → cleanup.

## Preconditions
- Theo services are running in a development environment.
- The `vault/backups/` and `vault/` directories are accessible.
- Prepare a benign patch that touches a single Python file.

## Steps
1. **Enable verbose logging**
   - Set `THEO_LOG_LEVEL=DEBUG` (or use the CLI `python theo.py logs --follow`).
2. **Kick off auto-patch**
   - Request Theo to self-update with a trivial change or manually invoke the orchestrator via `python theo.py apply-patch --auto`.
3. **Observe ordering**
   - Confirm logs show the following sequence:
     1. `Creating git backup snapshot`
     2. `Cloning project into vault/backups/clones/...`
     3. `Running tests in clone`
     4. `Applying patch to primary workspace`
     5. `Cleaning clone + temp artifacts`
4. **Negative test**
   - Introduce a failing pytest in the clone (edit a file inside `vault/backups/clones/...`).
   - Verify Theo halts before the "Applying patch" phase and surfaces the failure reason.
5. **Final confirmation**
   - When the patch ultimately succeeds, ensure cleanup removes the clone directory and updates backup metadata.

## Expected Logs
- Each phase emits structured JSON logs with `phase` fields (`backup`, `clone`, `test`, `apply`, `cleanup`).
- There should be no overlapping phases; each begins only after the previous one completes successfully.

## Troubleshooting
- Missing `cleanup` logs typically indicate an unhandled exception; rerun with `--verbose` and inspect `theo.log`.
- If the clone persists after success, run `python theo.py fresh-start --clones-only` and file a bug.
