# Room Sorting Issue Fix

**Date:** October 15, 2025  
**Issue:** Rooms appearing to reorder unexpectedly when clicked  
**Status:** ✓ RESOLVED

## Problem

When clicking on a chat room, it appeared to move to a different position in the rooms list, causing confusion. The expected behavior was:
- **Clicking a room:** Should NOT reorder it (stay in place)
- **Sending a message:** Should move room to the top (most recent)

## Root Cause

The `rooms_meta_validator.py` was setting ALL rooms to the exact same timestamp when repairing missing metadata. This caused:

1. Multiple rooms had identical `updated_at` timestamps (e.g., all 5 regular rooms had timestamp `1760551005`)
2. When sorted by timestamp, tied rooms could appear in unpredictable order
3. Any state updates or re-renders could shuffle the order of tied rooms
4. Users perceived this as rooms "jumping" when clicked, though clicking wasn't actually changing timestamps

## Solution

### Part 1: Fixed the Validator

**File:** `utils/rooms_meta_validator.py`

Added a new function `_get_room_timestamp()` that:
1. Reads the timestamp from the room's last message
2. Falls back to file modification time if no messages
3. Ensures each room gets a unique timestamp based on actual activity

Updated `validate_and_repair()` to use this function instead of mass-assigning `time.time()` to all rooms.

### Part 2: Fixed Existing Rooms

**Script:** `scripts/fix_room_timestamps.py`

Created and ran a one-time script that:
1. Loaded all existing room metadata
2. Updated each room's timestamp based on its last message
3. Saved the corrected metadata

**Results:**
- Updated 5 rooms that had identical timestamps
- Each room now has a unique timestamp reflecting its last activity
- Rooms maintain stable, predictable order

## Verification

### Before Fix
```json
{
  "r1760472077271": { "updated_at": 1760551005 },  // Same!
  "r1760454120720": { "updated_at": 1760551005 },  // Same!
  "r1760461582395": { "updated_at": 1760551005 },  // Same!
  "r1760460505517": { "updated_at": 1760551005 },  // Same!
  "r1760531599370": { "updated_at": 1760551005 }   // Same!
}
```
**Problem:** All 5 rooms tied in sort order → unpredictable ordering

### After Fix
```json
{
  "r1760472077271": { "updated_at": 1760538000 },  // Unique
  "r1760454120720": { "updated_at": 1760461833 },  // Unique
  "r1760461582395": { "updated_at": 1760467376 },  // Unique
  "r1760460505517": { "updated_at": 1760460742 },  // Unique
  "r1760531599370": { "updated_at": 1760541730 }   // Unique
}
```
**Solution:** Each room has unique timestamp → stable, predictable order

### Confirmed Behavior

**Clicking a Room:**
- Calls `setActiveRoomServer()` → POST `/api/rooms/active`
- Updates `active_room.json` (for background tasks)
- Does NOT update `chats_meta.json` timestamps
- ✓ Room stays in place

**Sending a Message:**
- Creates a run via `RunService.create_run_record()`
- Calls `rooms_meta.set_active_run()` → updates `updated_at`
- Updates `chats_meta.json` with current timestamp
- ✓ Room moves to top

## Files Changed

1. **utils/rooms_meta_validator.py**
   - Added `_get_room_timestamp()` function
   - Updated `validate_and_repair()` to use actual timestamps

2. **scripts/fix_room_timestamps.py**
   - One-time script to fix existing rooms
   - Can be re-run if needed (safe, idempotent)

3. **vault/chats_meta.json**
   - Updated with corrected timestamps for all rooms

## Testing

To verify the fix:

1. **Check current timestamps:**
   ```bash
   python3 -c "import json; print(json.dumps(json.load(open('vault/chats_meta.json')), indent=2))"
   ```

2. **Click different rooms** - they should stay in the same order

3. **Send a message** - that room should move to the top

4. **Refresh browser** - order should remain stable

## Future Prevention

The validator now ensures:
- New rooms get proper timestamps from their first message
- Repaired rooms use actual activity timestamps
- No mass timestamp assignments

If rooms ever share timestamps again:
1. Run the fix script: `python3 scripts/fix_room_timestamps.py`
2. Refresh browser

## Technical Details

### Room Sorting Logic

**Frontend:** `web/src/features/rooms/RoomsList.tsx` (lines 38-56)

```typescript
const lastTs = arr && arr.length ? 
  Number((arr[arr.length - 1] as any).timestamp || 0) : 0;
const metaTs = Number((roomMeta && roomMeta[rid] && 
  roomMeta[rid].updated_at) || 0);
const recency = Math.max(lastTs || 0, metaTs || 0);

rows.sort((a, b) => {
  if (b.recency !== a.recency) return b.recency - a.recency;
  return a.idx - b.idx;  // Stable sort fallback
});
```

**Sort order:** Descending by `recency` (newest first)

### When Timestamps Update

**Only these code paths update `chats_meta.json` timestamps:**

1. **Message sent:**
   - `server/run_service.py` → `create_run_record()` 
   - Calls `rooms_meta.set_active_run()` → sets `updated_at`

2. **Auto-title generated:**
   - `server/routers/runs.py` → `_maybe_title_room()`
   - Sets room title from first message → sets `updated_at`

3. **Manual title update:**
   - POST `/api/rooms/{room_id}/title` → sets `updated_at`

4. **Room renamed:**
   - PATCH `/api/rooms/{room_id}` → sets `updated_at`

**NOT updated when:**
- Clicking a room
- Loading history
- Reading messages
- Switching between rooms

## Summary

✓ Room timestamps now reflect actual message activity  
✓ Each room has a unique timestamp  
✓ Clicking rooms doesn't reorder them  
✓ Sending messages moves room to top  
✓ Sort order is stable and predictable  

**User action required:** Refresh browser to see the stable room order.

