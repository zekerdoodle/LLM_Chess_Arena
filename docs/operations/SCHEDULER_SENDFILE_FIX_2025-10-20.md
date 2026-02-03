# Task Scheduler and Send File Tool Fixes
**Date:** October 20, 2025

## Issues Resolved

### Issue 1: Task Scheduler Timezone Handling
**Problem:** When Theo created tasks with timezone-aware datetime strings (e.g., "2025-10-20T14:30:00-05:00"), the scheduler would fail silently:
- `_parse_time_string` parsed ISO format strings with timezone info, returning timezone-aware datetime objects
- `_schedule_task_job` compared these with `datetime.now()` (timezone-naive), causing TypeError
- The error was caught and logged but NOT returned to `create_task`
- Theo received "Created task successfully" but the task was never actually scheduled

**Root Cause:** Mixing timezone-aware and timezone-naive datetimes in comparisons raises TypeError in Python.

### Issue 2: Send File Tool Feedback
**Problem:** The `send_file` tool provided unclear feedback when paths failed validation:
- Invalid/blocked paths were silently skipped with only warnings logged
- Theo didn't receive clear error messages about which paths failed
- This caused Theo to retry the tool call, thinking it hadn't worked

## Changes Made

### 1. Task Scheduler (`layer4_tools/agentic_tools.py`)

#### A. Updated `_parse_time_string` (line 740)
- Added timezone stripping logic: if parsed datetime has tzinfo, strip it with `replace(tzinfo=None)`
- All returned datetimes are now timezone-naive for scheduler compatibility
- Added documentation explaining APScheduler handles timezone conversions internally
- **Result:** Prevents TypeError when comparing datetimes

#### B. Updated `_schedule_task_job` (line 503)
- Changed return type from `None` to `Tuple[bool, Optional[str]]`
- Returns `(True, None)` on success
- Returns `(False, error_message)` on failure
- Past-time tasks now return clear error: "Task scheduled for past time: {run_date}"
- **Result:** Failures can be detected and handled by callers

#### C. Updated `create_task` (line 1208)
- Check the return value of `_schedule_task_job`
- If scheduling fails, remove the task from storage (rollback)
- Return ERROR with clear message: "Task created but failed to schedule: {error}"
- **Result:** Theo receives clear feedback when scheduling fails

### 2. Send File Tool (`layer4_tools/web_tools.py`)

#### A. Path Validation Tracking
- Added `blocked_paths` list to track paths that fail validation
- When a path is blocked (non-vault), add it to the list
- **Result:** Can report which specific paths were blocked

#### B. Enhanced Error Messages
- When no valid paths or content: include list of blocked paths in error
- Error format: "Error: Provide either `content` or `path(s)` to send a file. Note: N path(s) were blocked (non-vault): path1, path2"
- **Result:** Theo knows exactly why the tool call failed

#### C. Success Warnings
- When files are sent successfully but some paths were blocked, append warning to success message
- Format: "File 'X' sent successfully to user (Warning: N path(s) were skipped as non-vault files: ...)"
- Add `blocked_paths` and `warning` to result data
- **Result:** Theo knows the operation succeeded but some paths were ignored

## Expected Behavior After Fix

### Task Scheduling
1. **Timezone in start_time:**
   - Input: `create_task(name="Test", details="...", start_time="2025-10-20T14:30:00-05:00")`
   - Behavior: Timezone info is stripped, task scheduled successfully
   - Theo sees: "Created task 'Test' (ID: abc123)"

2. **Past time:**
   - Input: `create_task(name="Test", details="...", start_time="2020-01-01T10:00:00")`
   - Behavior: Scheduling fails, task removed from storage
   - Theo sees: "ERROR: Task created but failed to schedule: Task scheduled for past time: 2020-01-01 10:00:00"

3. **Invalid datetime format:**
   - Input: `create_task(name="Test", details="...", start_time="not-a-date")`
   - Behavior: Parsing fails with clear error
   - Theo sees: "ERROR: Unrecognized time format: not-a-date"

### Send File
1. **Valid path:**
   - Input: `send_file(filename="doc.md", path="vault/file.md")`
   - Behavior: File sent successfully
   - Theo sees: "File 'doc.md' sent successfully to user"

2. **Invalid path only:**
   - Input: `send_file(filename="doc.md", path="/etc/passwd")`
   - Behavior: Path blocked, error returned
   - Theo sees: "Error: Provide either `content` or `path(s)` to send a file. Note: 1 path(s) were blocked (non-vault): /etc/passwd"

3. **Mixed valid/invalid paths:**
   - Input: `send_file(filename="doc.md", paths=["vault/file.md", "/etc/passwd"])`
   - Behavior: Valid file sent, warning about blocked path
   - Theo sees: "File 'doc.md' sent successfully to user (Warning: 1 path(s) were skipped as non-vault files: /etc/passwd)"

4. **Valid content:**
   - Input: `send_file(filename="doc.md", content="# Hello")`
   - Behavior: File created and sent
   - Theo sees: "File 'doc.md' sent successfully to user"

## Testing Recommendations

1. **Test timezone-aware datetimes:**
   ```python
   create_task("Test Task", "Details", "2025-10-20T14:30:00-05:00")
   ```
   Should succeed and schedule properly.

2. **Test past-time scheduling:**
   ```python
   create_task("Test Task", "Details", "2020-01-01T10:00:00")
   ```
   Should return clear error about past time.

3. **Test blocked file paths:**
   ```python
   send_file("test.txt", path="/etc/passwd")
   ```
   Should return error with specific blocked path.

4. **Test mixed valid/invalid paths:**
   ```python
   send_file("test.txt", paths=["vault/file.txt", "/etc/passwd"])
   ```
   Should send valid file with warning about blocked path.

## Files Modified

- `/home/debian/Projects/Theo/layer4_tools/agentic_tools.py`
  - `_parse_time_string` function
  - `_schedule_task_job` function
  - `create_task` function

- `/home/debian/Projects/Theo/layer4_tools/web_tools.py`
  - `send_file` function (path validation and error reporting)

## Impact

- **No breaking changes**: All existing functionality preserved
- **Better error messages**: Theo receives clear feedback on failures
- **Prevents loops**: Clear success/failure messages prevent Theo from retrying unnecessarily
- **Timezone compatibility**: Tasks can now be created with timezone-aware datetime strings
- **Security maintained**: Path validation still blocks non-vault files, but now reports them

