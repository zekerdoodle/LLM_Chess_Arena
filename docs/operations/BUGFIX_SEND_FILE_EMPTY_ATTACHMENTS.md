# Bugfix: Send File Empty/Invalid Attachments

**Date:** 2025-10-21  
**Component:** `layer4_tools/web_tools.py`  
**Function:** `send_file`

## Problem

When Theo attempted to send files that didn't exist, the `send_file` tool reported success but sent empty or invalid attachments to the user. This resulted in confusing messages where Theo claimed to have sent files, but the user received nothing useful.

### Reproduction Case

In a diet-related conversation, Theo tried to send 5 files:
- 3 bash commands failed (files didn't exist)
- `send_file` still reported "success"
- User received empty file attachments

From logs:
```
/bin/bash: line 1: /vault/diet_tracking/meal_plan.md: No such file or directory
/bin/bash: line 1: /vault/diet_tracking/weigh_ins.csv: No such file or directory
...
L4.tools [tool:send_file] - Tool executed successfully
```

## Root Causes

1. **Silent failure in `manually_send_message`** (lines 301-302):
   - Non-existent files were skipped with `continue`
   - No error raised or tracked

2. **`send_file` returned success despite missing files** (lines 697-700):
   - Added warning notes but still returned `success: True`
   - No validation that attachments were actually created

3. **Incomplete validation logic** (lines 623-637):
   - Only failed if no paths AND no content provided
   - Didn't fail when ALL provided paths were missing

## Solution

Added validation logic in `send_file` to check if attachments were actually created:

```python
# Check if any attachments were actually created
attachments_sent = result_data.get("attachments", [])

# Complete failure: no attachments and we had paths that were blocked/missing
if not attachments_sent and (blocked_paths or missing_paths):
    error_parts = []
    if missing_paths:
        error_parts.append(f"{len(missing_paths)} not found: {', '.join(missing_paths)}")
    if blocked_paths:
        error_parts.append(f"{len(blocked_paths)} blocked (non-vault): {', '.join(blocked_paths)}")
    
    error_msg = "Failed to send file(s): " + "; ".join(error_parts)
    return (
        error_msg,
        {
            "success": False,
            "error": "all_files_unavailable",
            "blocked_paths": blocked_paths,
            "missing_paths": missing_paths,
        },
    )
```

## Behavior Changes

### Before
- ✗ Missing files: `success: True` + empty attachments
- ✗ User confused by empty file messages
- ✗ Tool orchestrator didn't know about failure

### After
- ✓ All files missing: `success: False` + clear error message
- ✓ Some files missing: `success: True` + warning note (partial success)
- ✓ All files present: `success: True` (no change)
- ✓ Content provided: `success: True` (no change)

## Test Cases

All test cases verified:

1. **No attachments + missing paths** → FAIL ✓
2. **No attachments + blocked paths** → FAIL ✓
3. **Has attachments + some missing** → PARTIAL SUCCESS (with note) ✓
4. **Has attachments + none missing** → SUCCESS ✓
5. **No attachments but content provided** → SUCCESS ✓

## Expected Error Messages

When all files are missing:
```
Failed to send file(s): 3 not found: diet_tracking/meal_plan.md, diet_tracking/weigh_ins.csv, diet_tracking/progress.log
```

When some files are missing (partial success):
```
File "recipes.md" sent successfully to user (Note: 2 path(s) were not found: meal_plan.md, weigh_ins.csv)
```

## Impact

- **User Experience:** No more confusing empty file attachments
- **Tool Reliability:** Clear failure signals for orchestrator
- **Debugging:** Better error messages with specific file names
- **Backward Compatibility:** Preserved for valid use cases (content, existing files, partial success)

