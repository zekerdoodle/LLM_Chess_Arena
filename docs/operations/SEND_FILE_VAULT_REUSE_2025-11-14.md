# Send File: Vault-Only Mode (loop fix)

**Date:** 2025-11-14  
**Component:** `layer4_tools/web_tools.py` (`send_file`)  

## Problem

- The previous patch still allowed inline `content` and filename-only auto-locate logic.  
- Models continued to regenerate the same artifact over and over (changing filenames each time) instead of sharing the copy that already existed in the vault.  
- Chat run `06D88J03KMG2RR5B933FZJT748` shows three `send_file` calls in a row because the tool never enforced “vault-only” mode.

## Fix

1. **Vault paths only**
   - Removed all inline-content creation, filename guessing, MIME overrides, etc.  
   - `send_file` now accepts only `path` / `paths` that already exist under the vault root. If no valid file is supplied the tool fails fast with `artifact_missing_source`.

2. **Automatic messaging**
   - When `message` is omitted the tool generates friendly summaries such as `Sharing \`Riff-Invaders-Theo.html\`` or `Sharing 3 files: …`.  
   - Result metadata now always includes `shared_paths`, plus `blocked_paths` / `missing_paths` for partial failures.

3. **Clear failures**
   - If attachment upload fails (e.g., file deleted mid-call) the tool surfaces `attachment_upload_failed`.  
   - Non-vault paths are explicitly listed so the orchestrator can stop retrying the same invalid arguments.

## Tests

- Added regression coverage in `tests/test_tool_executor.py` for:
  1. Sending a vault file via absolute path.
  2. Sending via vault-relative path.
  3. Erroring when every provided path is invalid.
  4. Default messaging when no `message` is supplied.
- `pytest` collection in this workspace is currently filtered to zero, so these tests are documented here for when the filters are relaxed.

## Impact

- Theo can only share files that already exist in his vault, preventing loops where he keeps regenerating the same HTML.  
- Users receive a single, stable download link per share (no `_1`, `_2` duplicates).  
- Tool output now communicates exactly which paths were skipped, so the LLM knows whether to try a different vault file or stop.