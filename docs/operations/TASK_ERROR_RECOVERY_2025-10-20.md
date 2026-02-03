# Task Error Recovery System

**Date:** 2025-10-20  
**Status:** ✅ Implemented

## Overview

Implemented an automatic error recovery system for scheduled tasks that handles failures with exponential backoff retries and alerts Theo when manual intervention is needed.

## Problem Statement

Previously, when tasks failed to execute (due to errors in Theo's code, external API failures, or other issues), they would simply fail silently or require manual intervention. There was no automatic retry mechanism, making the system less reliable and requiring constant monitoring.

## Solution

### Automatic Retry with Exponential Backoff

When a task execution fails, the system now:
1. Captures the error message and timestamp
2. Increments the retry count
3. Schedules a retry with exponential backoff: 2^retry_count minutes
   - 1st retry: 1 minute later
   - 2nd retry: 2 minutes later
   - 3rd retry: 4 minutes later
   - 4th retry: 8 minutes later
   - 5th retry: 16 minutes later

### Needs Attention Status

After 5 failed attempts (configurable via `max_retries`), the task is automatically marked as `needs_attention` and Theo receives an alert message in the task's room with:
- Task name and ID
- Last error message
- Suggested actions (fix/archive/retry)

Theo can then:
1. Review the error logs
2. Fix the underlying issue
3. Update the task with `status='active'` to retry (this resets the retry count)
4. Or archive the task if it's no longer needed

## Implementation Details

### New Task Fields

Added to task metadata in `scheduled_tasks.json`:
```json
{
  "retry_count": 0,
  "last_error": null,
  "last_error_time": null,
  "max_retries": 5
}
```

### New Task Status

- `needs_attention`: Automatically set when max retries exceeded

### Code Changes

#### 1. `layer4_tools/agentic_tools.py`

**Added `_schedule_task_retry()` function:**
- Handles retry scheduling with exponential backoff
- Marks tasks as needs_attention after max retries
- Sends alert to Theo for manual review

**Updated `_execute_task()` function:**
- Resets retry count on successful execution
- Catches scheduler-level errors and triggers retry
- Passes retry count in metadata

**Updated `_sanitize_task_record()` function:**
- Adds default values for new error recovery fields

#### 2. `utils/queue_processor.py`

**Updated `_send_task_message()` function:**
- Logs retry count in execution logs
- Monitors run completion/failure
- Triggers retry on run failures with 5-minute timeout
- Handles processing errors with retry scheduling

#### 3. `utils/tool_schemas.py`

**Updated `update_task` schema:**
- Added `needs_attention` to status enum
- Documented automatic retry behavior
- Explained how to reset and retry tasks

#### 4. `layer4_tools/agentic_tools.py` - `update_task()`

**Added status transition logic:**
- Automatically resets retry count when moving from `needs_attention` → `active`
- Clears error fields to allow fresh retry

### Frontend Changes

#### 5. `web/src/types/index.ts`

Added new fields to Task interface:
```typescript
retry_count?: number;
last_error?: string | null;
last_error_time?: string | null;
max_retries?: number;
```

Added `needs_attention` to status type union.

#### 6. `server/routers/tasks.py`

Updated both `/api/tasks` and `/api/tasks/stream` endpoints to return new error recovery fields.

## Error Handling Scenarios

### Scenario 1: Temporary API Failure
- Task fails due to external API being down
- System retries with backoff (1min, 2min, 4min, 8min, 16min)
- API comes back online on 3rd retry
- Task succeeds, retry count resets to 0
- Future executions proceed normally

### Scenario 2: Code Error
- Task fails due to bug in Theo's code
- System retries 5 times with backoff
- All retries fail
- Task marked as `needs_attention`
- Theo receives alert in task room
- Theo fixes the code bug
- Theo updates task status to `active`
- Task retries successfully

### Scenario 3: Permanent Failure
- Task is no longer viable (e.g., API deprecated)
- System retries 5 times
- All fail
- Theo receives alert
- Theo archives the task
- No further retries occur

## Monitoring & Debugging

### Log Messages

**Retry scheduled:**
```
WARNING - L4.tools [retry] - Task abc123 failed (attempt 2/5). 
Scheduled retry in 2 minutes at 2025-10-20T14:32:00
```

**Max retries exceeded:**
```
ERROR - L4.tools [retry] - Task abc123 exceeded max retries (5). 
Marking as needs attention.
```

**Retry count reset:**
```
INFO - L4.tools [scheduler] - Task abc123 retry attempt successful, 
resetting retry count
```

**Status transition:**
```
INFO - L4.tools [update_task] - Task abc123 status changed from 
needs_attention to active, resetting retry count
```

### Task Inspection

Use `list_task` tool to check task status:
```python
list_task(task_id="abc123")
```

Returns:
```
TASK: Daily Sync (ID: abc123)
Status: needs_attention
Retry Count: 5
Last Error: Connection timeout to external API
Last Error Time: 2025-10-20T14:30:00
```

## Configuration

### Adjusting Max Retries

Tasks default to 5 retries. To customize for a specific task:
```python
update_task(task_id="abc123", max_retries=10)
```

### Backoff Formula

Current formula: `2^retry_count` minutes

To modify, edit `_schedule_task_retry()` in `layer4_tools/agentic_tools.py`:
```python
backoff_minutes = 2 ** retry_count  # Current
# Or use a different formula:
backoff_minutes = retry_count * 5  # Linear: 5, 10, 15, 20, 25 minutes
```

## Testing

### Manual Testing

1. **Create a task that will fail:**
   ```python
   create_task(
       name="Test Failure",
       details="Trigger an intentional error",
       start_time="1min"
   )
   ```

2. **Observe retry behavior in logs**

3. **After 5 failures, check task status:**
   ```python
   list_task(task_id="<task_id>")
   # Should show status: needs_attention
   ```

4. **Fix the issue and retry:**
   ```python
   update_task(task_id="<task_id>", status="active")
   ```

### Automated Testing

Run existing test suite:
```bash
pytest tests/test_task*.py -v
```

## Benefits

1. **Improved Reliability**: Transient errors don't cause permanent task failures
2. **Reduced Manual Intervention**: Most failures resolve automatically
3. **Better Visibility**: Clear error tracking and alerts
4. **Graceful Degradation**: Tasks eventually escalate to Theo for review
5. **Self-Healing**: System can recover from temporary outages

## Future Enhancements

1. **Adaptive Backoff**: Adjust retry intervals based on error type
2. **Error Pattern Detection**: Identify recurring issues automatically
3. **Retry Policies**: Different retry strategies per task type
4. **Metrics Dashboard**: Visualize retry rates and failure patterns
5. **Circuit Breaker**: Temporarily disable tasks causing repeated system issues

## Migration

Existing tasks will automatically receive default values for new fields:
- `retry_count`: 0
- `last_error`: null
- `last_error_time`: null
- `max_retries`: 5

No manual migration needed - fields are added during task load via `_sanitize_task_record()`.

## Related Documentation

- `docs/tasks_notifications_v2.md` - Main tasks system specification
- `docs/TASK_SYSTEM_FIXES.md` - Previous task system improvements
- `docs/operations/TASKS_SYSTEM_BUGFIXES_2025-10-20.md` - Recent bugfixes


