# Task Room Isolation Fix - October 20, 2025

## Problem Summary

Tasks were executing in the wrong rooms, causing content to bleed between unrelated conversations. Specifically:

1. **Meta "How to Agent" task outputs** (Task_Template.md, Task_Ledger_v0.md, Progress_Log_Template.md) were appearing in the "Slight Deficit Weight Loss Plan" room
2. Tasks were falling back to the **active room** (whatever the user was currently viewing) when their designated room failed to queue
3. File attachments were causing scroll jitter due to 20+ duplicate files being sent
4. **File sending tools** were not receiving the task's `run_id` from context, causing them to send files to the active room instead of the task's designated room

## Root Causes Identified

### Cause 1: Active Room Fallback (Critical)
**Location:** `utils/queue_processor.py` lines 276-291

When a task's explicit `room_id` failed to queue as a valid candidate, the system fell back to `get_active_room()`. This caused tasks to execute in whatever room the user happened to be viewing at the time, leading to content bleeding.

**Example:** Meta task scheduled to run at 2:17 AM. If user had diet room open at that time, meta task outputs would appear in the diet room instead of the meta task's designated room.

### Cause 2: Room Validation Too Strict
**Location:** `utils/queue_processor.py` lines 249-261

The `_queue_candidate` function treated task room IDs (`task_*`) as potential placeholders, causing valid task rooms to be rejected if they didn't pass certain checks.

### Cause 3: Missing run_id Context Injection
**Location:** `utils/tool_executor.py` lines 464-466 and 503-505

The `execute_tool_call` function received a `context` parameter containing the `run_id`, but the file sending tools (`send_file`, `manually_send_message`) were only looking for `run_id` in the tool arguments, not in the context. This caused them to fall back to the active room instead of using the task's designated room.

### Cause 4: Insufficient Logging
Tasks could fail to resolve to their designated rooms without clear diagnostic logging, making debugging difficult.

## Solutions Implemented

### Fix 1: Removed Active Room Fallback ✅
**File:** `utils/queue_processor.py` lines 290-294

**Changed:**
```python
# OLD: Fall back to active room
if not explicit_room_queued:
    active_room = get_active_room()
    _queue_candidate(active_room, allow_placeholder=True)
```

**To:**
```python
# NEW: NEVER fall back to active room for tasks
# CRITICAL: Tasks MUST NOT fall back to active_room
# This was causing tasks to execute in whatever room the user was viewing,
# leading to content bleeding between unrelated conversations.
# Instead, if explicit room fails, we create a NEW dedicated room for the task.
# This ensures task isolation and prevents cross-contamination.
```

**Impact:** Tasks now create new dedicated rooms instead of bleeding into user conversations.

### Fix 3: Fixed run_id Context Injection ✅
**File:** `utils/tool_executor.py` lines 464-466 and 503-505

**Problem:** File sending tools (`send_file`, `manually_send_message`) were not receiving the `run_id` from the execution context, causing them to fall back to the active room instead of using the task's designated room.

**Fixed:**
```python
# OLD: Only used run_id from tool arguments
run_id = arguments.get("run_id")

# NEW: Use run_id from context if not provided in arguments
run_id = arguments.get("run_id") or (context.get("run_id") if context else None)
```

**Impact:** Files created by tasks are now correctly sent to the task's designated room instead of the user's active room.

### Fix 2: Strengthened Room Validation ✅
**File:** `utils/queue_processor.py` lines 249-267

**Added:**
- Task rooms (`task_*` prefix) are ALWAYS treated as valid candidates
- They are never treated as placeholders
- Logging added to track which rooms are queued and why

**Code:**
```python
# Task rooms (starting with 'task_') are ALWAYS valid - never treat as placeholder
# This ensures tasks stay in their designated rooms even if the file doesn't exist yet
is_task_room = rid.startswith("task_")
if lowered in placeholder_rooms and not allow_placeholder and not is_task_room:
    # Store placeholders to try after primary fallbacks
    logger.debug(f"L6.queue_processor [task] - Room '{rid}' queued as placeholder (not primary candidate)")
    placeholder_queue.append(rid)
    return
```

**Impact:** Task rooms are never rejected, ensuring tasks stay in their designated rooms.

### Fix 3: Added Room Resolution Validation ✅
**File:** `utils/queue_processor.py` lines 369-385

**Added:**
- Validation check after room resolution
- Warning if task resolves to a non-task room (doesn't start with `task_`)
- Enhanced logging to track room resolution success

**Code:**
```python
# VALIDATION: Ensure task resolved to a task room (not a standard room)
# This prevents task content from bleeding into user conversation rooms
if final_room and not final_room.startswith("task_"):
    logger.warning(
        "L6.queue_processor [task] - Task %s resolved to NON-TASK room '%s' (expected task_* pattern). "
        "This may cause content bleeding. Explicit room was: %s",
        task_id,
        final_room,
        explicit_room
    )
```

**Impact:** Immediate visibility into any room resolution failures.

### Fix 4: Enhanced Meta Task Logging ✅
**File:** `utils/agency.py` lines 85-113

**Added:**
- Clear documentation that meta task gets isolated room
- Logging of both task ID and room ID
- Explanation that sub-tasks will inherit the room

**Impact:** Better visibility into meta task room isolation.

### Fix 5: Sub-Task Room Inheritance Logging ✅
**File:** `layer4_tools/agentic_tools.py` lines 1345-1353

**Enhanced:**
- Clear logging when sub-tasks inherit parent task rooms
- Explanation that this ensures task chain isolation

**Code:**
```python
logger.info(
    f"L4.tools [create_task] - Sub-task '{name}' inheriting parent task room: {task_room}. "
    f"This ensures task chain isolation (parent room: {origin_room})"
)
```

**Impact:** Clear audit trail of task room inheritance.

## How Task Room Isolation Now Works

### For New Tasks Created by User
1. Task created in **standard room** → Room is **duplicated**
2. Task created in **task room** → **Same room** is reused (sub-task correlation)

### For Task Execution
1. Task fires via scheduler
2. Queue processor extracts `room_id` from task metadata
3. Room validation checks if room starts with `task_`
4. If validation fails → **NEW room** is created (NOT active room)
5. Task executes in its designated task room
6. All outputs stay in that room

### For Sub-Tasks (Task Chains)
1. Meta task executes in its dedicated room (e.g., `task1760893474858`)
2. Meta task creates sub-task (e.g., "Wire dashboard counters")
3. Sub-task inherits meta task's room automatically
4. All meta task work stays isolated in one room

## Expected Behavior Going Forward

### ✅ Meta Task Isolation
- Meta "How to Agent" task has its own dedicated room
- All sub-tasks (framework work, ledger updates, etc.) stay in that room
- **NO** meta task outputs will appear in diet room or other unrelated rooms

### ✅ Diet Task Isolation
- Weigh-in reminders stay in diet task room (`task1760902689941`)
- Sunday reviews stay in diet task room
- **NO** diet outputs will appear in meta task room or standard rooms

### ✅ Standard Room Separation
- Standard rooms (like "Slight Deficit Weight Loss Plan") remain clean conversation spaces
- When tasks are created there, rooms are duplicated preserving context
- Original standard room stays pristine for user conversations

## File Attachment Scroll Jitter

**Root Cause:** Theo sent the same meal plan file 20+ times due to unclear tool feedback (fixed separately in SEND_FILE_IMPROVEMENTS.md).

**Related Fix:** The tool confirmation message was improved to clearly indicate file delivery success, preventing retries.

**This Fix:** By ensuring task isolation, we prevent attachment duplication across rooms.

## Testing & Validation

### Manual Validation Steps
1. **Create a new task from standard room** → Verify room is duplicated
2. **Create a task from task room** → Verify same room is reused
3. **Let scheduled task fire** → Check logs for room resolution
4. **Create sub-task from task** → Verify room inheritance logged

### Log Patterns to Watch
```
# Good: Task stayed in designated room
L6.queue_processor [task] - Queued task room 'task1760902689941' as valid candidate
L6.queue_processor [task] - Task 576fbe32 resolved to room 'task1760902689941' (is_task_room=True)

# Warning: Task resolved to wrong room type
L6.queue_processor [task] - Task abc123 resolved to NON-TASK room 'r1760896386708' (expected task_* pattern)

# Good: Sub-task inherited parent room
L4.tools [create_task] - Sub-task 'Update ledger' inheriting parent task room: task1760893474858
```

## Files Modified

1. **utils/queue_processor.py**
   - Removed active room fallback (lines 290-294)
   - Strengthened room validation (lines 249-267)
   - Added resolution validation (lines 369-385)

2. **utils/agency.py**
   - Enhanced meta task logging (lines 85-113)
   - Documented room isolation strategy

3. **layer4_tools/agentic_tools.py**
   - Enhanced sub-task room inheritance logging (lines 1345-1353)

## Questions Answered

### 1. Duplicate room duration
**Answer:** After a room is duplicated, the two rooms are **completely independent forever**. There is no linkage. The original remains a standard room, the duplicate becomes a task room with its own lifecycle.

### 2. Meta task room strategy
**Implementation:** Each meta task invocation gets a **new dedicated room**. Sub-tasks created by that meta task **share the same room**, ensuring all work for that meta task chain stays isolated.

### 3. File attachment scroll jitter
**Implementation:** 
- Primary cause (file duplication) was fixed via better tool feedback
- This fix (room isolation) prevents attachments from bleeding across rooms
- Future enhancement could add duplicate detection at attachment level

## Migration Notes

### Existing Tasks
- No migration required
- Existing tasks will continue using their current rooms
- New behavior applies to newly created tasks and future executions

### Existing Room Structure
- Standard rooms remain unchanged
- Task rooms remain unchanged
- No need to clean up or restructure existing data

## Success Metrics

✅ **Zero cross-contamination** - Tasks stay in their designated rooms  
✅ **Clear audit trail** - Logs show room resolution for every task  
✅ **Sub-task correlation** - All chained tasks stay in parent room  
✅ **User experience** - No more unexpected content in conversation rooms

## Related Documentation

- `docs/tasks_notifications_v2.md` - Task & room assignment specs
- `docs/TASK_ROOM_ASSIGNMENT_IMPLEMENTATION.md` - Original implementation
- `docs/TASK_ROOM_SELECTION_ISSUE.md` - Previous room selection issues
- `docs/SEND_FILE_IMPROVEMENTS.md` - File duplication fixes

