# Fix Summary: Room Sorting Issue

**Date:** October 15, 2025  
**Issue:** Rooms appearing to jump around when clicked  
**Status:** ✓ RESOLVED

## What Was Wrong

When you clicked on a chat, it seemed to move to a different position in the rooms list. This was confusing because clicking shouldn't reorder rooms - only sending messages should move them to the top.

### Root Cause

The room metadata validator was assigning the **exact same timestamp** to all rooms when it repaired missing metadata. This meant:

```json
// BEFORE (ALL IDENTICAL):
"r1760472077271": { "updated_at": 1760551005 },  // Same!
"r1760454120720": { "updated_at": 1760551005 },  // Same!
"r1760461582395": { "updated_at": 1760551005 },  // Same!
"r1760460505517": { "updated_at": 1760551005 },  // Same!
"r1760531599370": { "updated_at": 1760551005 }   // Same!
```

When multiple rooms have identical timestamps, their order becomes unpredictable. Even though there's a stable sort fallback, any state update or re-render could shuffle the tied rooms, making it appear that clicking caused the reordering.

## What Was Fixed

### 1. Updated the Validator Logic

**File:** `utils/rooms_meta_validator.py`

- Added `_get_room_timestamp()` function that extracts the actual timestamp from each room's last message
- Falls back to file modification time if no messages exist
- Each room now gets a unique timestamp based on real activity

### 2. Fixed Existing Room Data

**Script:** `scripts/fix_room_timestamps.py`

- Ran a one-time fix to update all existing rooms
- Each room now has a unique timestamp reflecting its last message

```json
// AFTER (ALL UNIQUE):
"r1760472077271": { "updated_at": 1760538000 },  // Unique
"r1760454120720": { "updated_at": 1760461833 },  // Unique
"r1760461582395": { "updated_at": 1760467376 },  // Unique
"r1760460505517": { "updated_at": 1760460742 },  // Unique
"r1760531599370": { "updated_at": 1760541730 }   // Unique
```

### 3. Added Tests

**File:** `tests/test_room_timestamp_uniqueness.py`

- 7 comprehensive tests covering all scenarios
- Ensures timestamps are unique and based on actual activity
- Validates stable sorting behavior
- All tests passing ✓

## What You'll Notice

### Clicking a Room
- ✓ Room stays in its current position
- ✓ No unexpected jumping or reordering
- ✓ Predictable, stable list order

### Sending a Message
- ✓ Room moves to the top (most recent)
- ✓ All other rooms maintain their order
- ✓ Expected behavior works correctly

## Files Changed

1. **utils/rooms_meta_validator.py** - Validator logic updated
2. **vault/chats_meta.json** - Timestamps corrected (5 rooms)
3. **scripts/fix_room_timestamps.py** - One-time fix script (created)
4. **tests/test_room_timestamp_uniqueness.py** - Comprehensive tests (created)
5. **docs/operations/ROOM_SORTING_FIX.md** - Technical documentation (created)

## What to Do Now

**Just refresh your browser!**

The server-side fix is complete. Simply refresh your browser (F5 or Ctrl+Shift+R) to see the stable room order.

## Verification

To confirm the fix is working:

1. **Refresh your browser** - rooms should appear in a stable order
2. **Click different rooms** - they should NOT reorder
3. **Send a message in any room** - that room should move to the top
4. **Click around again** - order should remain stable

## Technical Details

### What Updates Timestamps (Correct Behavior)

✓ Sending a message  
✓ Auto-generating a room title from the first message  
✓ Manually updating a room title  
✓ Renaming a room  

### What Does NOT Update Timestamps (Also Correct)

✓ Clicking on a room  
✓ Loading room history  
✓ Reading messages  
✓ Switching between rooms  

All behavior is now correct and matches the design specifications.

---

**Summary:** Room timestamps are now unique and based on actual message activity. Clicking rooms no longer causes reordering. The rooms list maintains a stable, predictable order based on when messages were last sent.

