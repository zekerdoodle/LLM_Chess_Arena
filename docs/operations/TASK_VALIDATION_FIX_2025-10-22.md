# Task Validation and Scheduling Fix - October 22, 2025

## Problem Summary

The weigh-in reminder failed to trigger on October 22, 2025 at 6:15am CT due to multiple validation and scheduling issues:

1. **Task "cfce4776" was created with empty fields** (Oct 21, 1:35pm CT):
   - Name: "Untitled Task" 
   - Details: "" (empty string)
   - Schedule was correct: `start_time`: "2025-10-22T06:15:00", `recurrence`: "15 6 * * 1,3,5"

2. **Attempted repair failed** (Oct 22, 7:29am CT - after 6:15am had passed):
   - Theo tried to update task cfce4776 but fields remained empty
   - Created scheduled message "291409cf" as workaround
   - Set `start_time`: "2025-10-24T06:15:00" (Friday) instead of today
   - Since created at 7:29am Wednesday, next cron occurrence became Friday 6:15am

3. **Root Causes**:
   - `create_task` allowed empty name and details through validation
   - `CronTrigger.from_crontab()` ignored `start_time` parameter
   - No feedback to Theo about when recurring jobs would actually fire
   - No warning when next occurrence was days away

## Files Modified

### `/home/debian/Projects/Theo/layer4_tools/agentic_tools.py`

#### 1. Added `_calculate_next_occurrence` helper (after line 769)

```python
def _calculate_next_occurrence(recurrence: str, start_from: Optional[datetime] = None) -> Optional[datetime]:
    """Calculate the next occurrence of a cron expression from a given time."""
```

Calculates when a cron expression will next fire, used for validation and user feedback.

#### 2. Enhanced `create_task` validation (lines 980-1026)

- Added empty string validation for `name` and `details` fields
- Calculate next occurrence for recurring tasks
- Log warning if next occurrence is more than 48 hours away
- Include next occurrence info in response to Theo with format:
  ```
  ⚠️ Next occurrence: 2025-10-24T06:15:00 (42.3 hours from now)
  NOTE: Next run is more than 48 hours away. If you expected it sooner, check your cron expression.
  ```

#### 3. Enhanced `schedule_manual_message` validation (lines 710-756)

- Added same next occurrence calculation and validation
- Warnings logged if next occurrence is > 48 hours away
- Added `_next_run_info` to return value for recurring messages

#### 4. Fixed `_schedule_task_job` to respect start_time (lines 624-642)

- Uses `next_run_time` parameter in `add_job()` to set first execution time if start_time is in the future
- Prevents recurring jobs from firing before their intended start time
- Logs when start_date is being applied
- Note: APScheduler's `CronTrigger.from_crontab()` doesn't accept start_date as kwarg, so we pass it via `next_run_time` to `add_job()`

#### 5. Fixed `_schedule_scheduled_message_job` similarly (lines 668-686)

- Same next_run_time logic applied to scheduled messages
- Ensures recurring messages respect their start_time

## Expected Behavior Changes

### Before Fix

```python
# Theo creates task with empty fields
create_task(name="", details="", start_time="2025-10-22 06:15", recurrence="15 6 * * 1,3,5")
# Returns: success=True (WRONG!)

# Theo creates recurring task that won't fire for days
create_task(name="Task", details="Do thing", start_time="2025-10-22 06:15", recurrence="15 6 * * 1,3,5")
# Returns: "Created task 'Task' (ID: abc123)" 
# No indication next run is Friday (2 days away)
```

### After Fix

```python
# Theo tries to create task with empty fields
create_task(name="", details="", start_time="2025-10-22 06:15", recurrence="15 6 * * 1,3,5")
# Returns: "ERROR: Task name cannot be empty"

# Theo creates recurring task
create_task(name="Task", details="Do thing", start_time="2025-10-22 06:15", recurrence="15 6 * * 1,3,5")
# Returns: "Created task 'Task' (ID: abc123)
#
# ⚠️ Next occurrence: 2025-10-24T06:15:00 (42.3 hours from now)
# NOTE: Next run is more than 48 hours away. If you expected it sooner, check your cron expression."
```

## Impact on Existing Tasks

- Task "cfce4776" remains in vault with empty fields (status: active)
- Scheduled message "291409cf" will fire correctly on Friday 6:15am
- All future task/message creation will be validated
- No migration needed - validation only applies to new creations

## Testing Recommendations

1. Try creating a task with empty name - should reject with error
2. Try creating a task with empty details - should reject with error
3. Create a recurring task with cron "0 9 * * 1" (Mondays 9am) - should see next occurrence in response
4. Verify existing scheduled tasks still work after restart
5. Check logs for start_date messages when recurring jobs are scheduled

## Related Issues

- Empty task validation prevents data quality issues
- Next occurrence feedback helps Theo understand scheduler behavior
- start_date support ensures recurring jobs honor their start_time
- Warning for distant next occurrences catches cron expression mistakes

## Follow-up Considerations

1. Consider adding UI indicator for tasks with empty/invalid fields
2. Possibly add `list_task` filter to show problematic tasks
3. Could add validation to `update_task` to prevent clearing required fields
4. May want to add tool to "dry run" cron expressions before committing

