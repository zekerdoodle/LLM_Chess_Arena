# File-Send and Code Agent Integration Fixes

**Date:** October 21, 2025  
**Status:** ✅ Implemented  
**Issue:** Code agent output not being properly received by Theo; file-send tool failing with "Not Found" errors

---

## Summary

Fixed two critical issues affecting Theo's ability to:
1. Properly interpret code agent results (was creating duplicate clones and ignoring completed work)
2. Send files to new vault subdirectories (was hitting bash circuit breaker)

## Root Causes Identified

### Issue 1: Code Agent Output Truncation

**Problem:** The `code_agent` tool only returned the last 200 lines of stdout, which caused:
- Important completion/success messages to be cut off
- Theo couldn't determine if work was finished successfully
- Theo would create new clones and restart work, ignoring completed fixes

**Evidence from Chat History:**
- Line 254 in chat: Code agent successfully fixed file-send with a diff
- Lines 262-266: Theo immediately created a NEW clone
- Lines 270-275: Theo started code agent again, duplicating effort

### Issue 2: File-Send Directory Creation Failure

**Problem:** When sending a file with content to a path like `/vault/diet/meal_plan.md`:
- If the `/vault/diet/` directory didn't exist, the tool would fail
- It attempted to fall back to bash commands (`mkdir -p`) which failed
- This triggered the bash circuit breaker
- Users got `{"detail":"Not Found"}` errors

**Root Cause:** The `send_file` function used `_coerce_to_vault_file_path()` which only worked for *existing* files. If a file didn't exist, the path was marked as "blocked" even if it was a valid vault path.

---

## Fixes Applied

### Fix 1: Enhanced Code Agent Output Format

**File:** `layer4_tools/code_agent.py` (lines 153-243)

**Changes:**
1. Added clear SUCCESS/FAILURE header at the start of output
2. Extracts and displays modified file list
3. Shows exit code prominently
4. Includes modified file count and names
5. Adds `modified_files` to result dict for programmatic access

**New Output Format:**
```
============================================================
CODE AGENT RESULT
============================================================
Status: ✓ SUCCESS (exit code 0)
Clone: clones/clone_20251021_081949
Files Modified: 2
  - layer4_tools/web_tools.py
  - utils/tool_schemas.py

============================================================

Detailed Output (last 200 lines):
[... existing truncated output ...]
```

**Benefits:**
- ✅ Theo can immediately see if code agent succeeded
- ✅ Theo knows which files were modified without parsing output
- ✅ No more duplicate clone creation after successful runs
- ✅ Clear status visible even if detailed output is truncated

### Fix 2: File-Send Directory Creation

**File:** `layer4_tools/web_tools.py`

**Added Helper Function:** `_normalize_candidate_to_vault()` (lines 43-97)
- Similar to `_coerce_to_vault_file_path()` but doesn't require file to exist
- Returns a Path object for valid vault paths
- Used for creating new files

**Modified send_file Function:** (lines 520-702)
1. **Added tracking lists:**
   - `missing_paths` - paths that couldn't be found
   - `create_targets` - valid vault paths to create
   - `created_paths` - successfully created files

2. **Path resolution logic:**
   - First tries `_coerce_to_vault_file_path()` for existing files
   - If not found, tries `_normalize_candidate_to_vault()` for creation
   - Distinguishes between blocked (outside vault) and missing (not found)

3. **File creation:**
   - Uses `target_path.parent.mkdir(parents=True, exist_ok=True)` to create directories
   - Writes files using Python's native `open()` instead of bash
   - Logs creation success/failure

4. **Enhanced error messages:**
   - Clearly distinguishes blocked vs missing vs created paths
   - Success messages note how many files were created
   - Error messages specify which paths failed and why

**Key Code:**
```python
# Create parent directories if needed
target_path.parent.mkdir(parents=True, exist_ok=True)
# Write the file
with open(target_path, "wb") as wf:
    wf.write(payload_bytes)
```

**Benefits:**
- ✅ No more bash command dependencies for directory creation
- ✅ No more circuit breaker activation
- ✅ Files can be sent to any valid vault subdirectory
- ✅ Clear feedback about what was created vs what failed

---

## Testing Recommendations

### Test 1: Code Agent Output Visibility
```python
# Have Theo create a clone and run code agent with any task
# Verify the output shows:
# - Clear SUCCESS/FAILURE status
# - Modified files list
# - Exit code

# Example:
code_agent("Fix a typo in README.md", clone_path="clones/test_clone")
```

**Expected Result:**
- Clear status header visible at start
- No duplicate clones created after success
- Theo references the modified files in response

### Test 2: File-Send to New Directory
```python
# Have Theo send a file to a directory that doesn't exist yet
# Example from chat: diet meal plan to /vault/diet/meal_plan.md

send_file(
    filename="meal_plan.md",
    content="# Daily Meal Plan\n\nBreakfast: ...",
    path="diet/meal_plan.md"
)
```

**Expected Result:**
- ✅ File created successfully
- ✅ Directory `/vault/diet/` created automatically
- ✅ Success message mentions file was created
- ✅ No bash circuit breaker activation
- ✅ File downloadable by user
- ✅ No `{"detail":"Not Found"}` error

### Test 3: Mixed Scenarios
1. Send file with content + path (creates file, then sends it)
2. Send file to path without content (error with clear message)
3. Send file to path outside vault (blocked with clear message)
4. Send file to existing path (works as before)

---

## Files Modified

1. **layer4_tools/code_agent.py**
   - Enhanced output formatting (lines 153-243)
   - Added file modification detection
   - Added clear status headers

2. **layer4_tools/web_tools.py**
   - Added `_normalize_candidate_to_vault()` helper (lines 43-97)
   - Modified `send_file()` function (lines 520-702)
   - Added directory creation logic
   - Enhanced error messages

---

## Backward Compatibility

✅ All changes are backward compatible:
- Existing file-send calls with valid paths work unchanged
- Code agent output just has additional information prepended
- No breaking changes to function signatures
- Error handling improved but not breaking

---

## Next Steps

1. **Testing:** Verify both fixes work as expected (marked as TODO)
2. **Monitor:** Watch for any bash circuit breaker activations (should be eliminated)
3. **Document:** Consider updating tool schemas if needed

---

## Related Documentation

- Original issue analysis: Plan `/fix-code-agent-integration.plan.md`
- Chat history: `vault/chats/r1761050974213.json`
- Code agent tool: `layer4_tools/code_agent.py`
- File-send improvements: `docs/SEND_FILE_IMPROVEMENTS.md`

---

## Developer Notes

The file-send fix was actually first implemented by the code agent itself in a previous run, but the clone was deleted before the patch could be applied to the live codebase. This is exactly the scenario that Fix #1 (code agent output) prevents - Theo now clearly sees when code agent completes successfully and can apply the changes.

**Lesson learned:** Always verify code agent success status before creating new clones!

