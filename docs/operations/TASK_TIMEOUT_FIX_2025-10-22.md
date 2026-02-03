# Task Execution Timeout Fix

**Date:** 2025-10-22  
**Issue:** Scheduled tasks failing after 5 minutes  
**Status:** ✅ Fixed

## Problem Summary

Scheduled tasks were being cancelled mid-execution after approximately 5 minutes, regardless of complexity. This was causing long-running tasks to fail and trigger the retry mechanism.

### Example Failure Log

```
02:25:15 [WARNING] [rid=- run=06D0NJG241EN9QC9EDVXK6C6NW room=task1761070745735] L4.tools [orchestrator] - Tool execution was cancelled.
02:25:15 [WARNING] [rid=- run=06D0NJG241EN9QC9EDVXK6C6NW room=task1761070745735] L4.orchestrator - Cancelled during stage 'iteration_2_rebuild_prompt' (iteration info embedded if present). Content preview: Scheduled Task: 'Meta: Implement agent framework v0.1 — seed task schema + templ
02:25:15 [WARNING] [rid=- run=06D0NJG241EN9QC9EDVXK6C6NW room=task1761070745735] L4.orchestrator - Cancellation exception detail: CancelledError()
```

**Timeline:**
- Task scheduled: 02:20:02 CDT
- Cancellation: 02:25:15 CDT
- Duration: ~5 minutes 13 seconds

## Root Cause

The queue processor (`utils/queue_processor.py`) had a hardcoded 5-minute (300 second) timeout for scheduled task execution:

```python
await asyncio.wait_for(final_run.task, timeout=300)  # 5 min timeout
```

This timeout was designed as a safety mechanism to prevent runaway tasks, but it was far too aggressive for legitimate complex operations like:
- Code generation tasks
- Multi-step agentic workflows
- Large file processing
- Complex tool orchestration sequences

## Solution

### 1. Made timeout configurable

Updated `utils/queue_processor.py` to read the timeout from configuration:

```python
# Get timeout from config or use default of 1 hour
try:
    from utils.config_loader import load_config
    cfg = load_config()
    timeout_seconds = cfg.get("tasks", {}).get("execution_timeout_seconds", 3600)
except Exception:
    timeout_seconds = 3600  # Default to 1 hour

await asyncio.wait_for(final_run.task, timeout=timeout_seconds)
```

### 2. Added configuration option

Added new configuration section to `config.yaml`:

```yaml
tasks:
  execution_timeout_seconds: 3600  # 1 hour timeout for scheduled task execution
```

### 3. Improved logging

Updated timeout error message to include the actual timeout value:

```python
logger.warning(f"L6.queue_processor [task] - Task {task_id} run timed out after {timeout_seconds}s")
raise Exception(f"Task execution timed out after {timeout_seconds} seconds")
```

## Configuration

### Default Values

- **Default timeout:** 3600 seconds (1 hour)
- **Fallback:** If config loading fails, defaults to 3600 seconds

### Customization

Users can adjust the timeout in `config.yaml` based on their needs:

```yaml
tasks:
  execution_timeout_seconds: 7200  # 2 hours for very complex tasks
```

Or disable the timeout entirely by setting it very high:

```yaml
tasks:
  execution_timeout_seconds: 86400  # 24 hours (essentially no timeout)
```

## Impact

### Benefits
- ✅ Complex tasks can now run to completion
- ✅ Timeout is configurable per deployment
- ✅ Better logging shows actual timeout value
- ✅ Prevents false-positive retries for long tasks

### Backward Compatibility
- ✅ Default of 1 hour is reasonable for most tasks
- ✅ Still provides protection against truly runaway tasks
- ✅ No breaking changes to existing task definitions

## Testing Recommendations

1. **Verify complex tasks complete:**
   - Test tasks that previously failed at ~5 minutes
   - Confirm they now run to completion

2. **Verify timeout still works:**
   - Create a test task that runs indefinitely
   - Confirm it times out after configured duration

3. **Verify retry mechanism:**
   - Ensure legitimate timeouts still trigger retry logic
   - Check that retry count increments properly

## Related Files

- `utils/queue_processor.py` - Task execution monitoring
- `config.yaml` - Configuration file
- `layer4_tools/agentic_tools.py` - Task scheduling and retry logic

## Future Improvements

Consider these enhancements in future iterations:

1. **Per-task timeout override:** Allow individual tasks to specify their own timeout
2. **Adaptive timeout:** Learn from historical execution times
3. **Timeout warning:** Send notification before timeout occurs
4. **Graceful timeout:** Allow tasks to save progress before timing out

