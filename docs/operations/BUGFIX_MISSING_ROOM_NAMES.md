# Bug Fix: Missing Room Names After Reboot

**Date:** October 15, 2025  
**Status:** ✓ RESOLVED

## Problem

After a system update and reboot, Theo started successfully but:
- All chat rooms displayed without names (showing blank/empty titles)
- Client logs showed multiple errors:
  - `rooms: poll failed`
  - `rooms: set context failed`
  - `history: stream read failed`
  - `window.error` events

## Root Cause

The `vault/chats_meta.json` file was missing metadata entries for regular chat rooms. Only task rooms had metadata entries. Without metadata, the server returned `null` titles for rooms, causing the UI to display empty/blank room names.

**Affected Rooms (missing metadata):**
- `r1760454120720`
- `r1760460505517`
- `r1760461582395`
- `r1760472077271`
- `r1760531599370`

## Investigation

1. **Verified server was running correctly**: API endpoint `/api/rooms` was functional
2. **Checked hostname resolution**: No DNS issues (`debian` resolves to `127.0.1.1`)
3. **Examined metadata file**: Found only 5 entries (all task rooms) vs 10 actual room files
4. **Confirmed client errors**: All errors occurred during initial page load when metadata was incomplete

## Solution

Created and executed `scripts/fix_missing_room_metadata.py` which:

1. Scanned all room files in `vault/chats/`
2. Identified rooms without metadata entries
3. Extracted meaningful titles from the first user message in each room's history
4. Generated metadata entries with:
   - `title`: First user message (truncated to 50 chars) or "New Chat" if empty
   - `updated_at`: Current timestamp
5. Backed up original `chats_meta.json` to `chats_meta.json.backup`
6. Saved updated metadata

### Results

Successfully regenerated metadata for 5 rooms:
- `r1760472077271`: "Hey Theo! How's it goin' :)"
- `r1760454120720`: "Hey Theo, welcome. I'm Zeke. I've spent the las..."
- `r1760461582395`: "What's up Theo :)"
- `r1760460505517`: "Hey Theo! Mind hooking me up to my two bank acc..."
- `r1760531599370`: "Message from my new galaxy s25 😎😎"

## Verification

```bash
# Verify all rooms have metadata
curl -s http://localhost:8000/api/rooms | python3 -m json.tool

# Check metadata file directly
cat vault/chats_meta.json | python3 -m json.tool
```

All 10 rooms now have complete metadata with titles.

## User Action Required

**Refresh your browser** to reload the web client. Room names should now appear correctly.

If rooms still don't show names:
1. Hard refresh: `Ctrl+Shift+R` (Windows/Linux) or `Cmd+Shift+R` (Mac)
2. Check browser console for any new errors
3. Verify server is still running: `ps aux | grep theo`

## Prevention Measures Implemented

To prevent this issue from recurring, the following safeguards have been added:

### 1. Automatic Startup Validation
**File:** `utils/rooms_meta_validator.py`

Every time Theo starts, the server now:
- Scans all room files in `vault/chats/`
- Checks that each room has a metadata entry
- Automatically repairs missing entries by extracting titles from chat history
- Logs validation results for monitoring

### 2. Atomic File Writes
**Updated:** `utils/rooms_meta.py` save function

Metadata saves now use atomic writes:
- Writes to a temporary file first
- Only replaces the real file after successful write
- Prevents partial writes during crashes or power loss
- Uses `os.replace()` for POSIX atomic rename guarantees

### 3. Git Backups
Theo's existing automated backup system (every 10 minutes) continues to protect metadata in git history. If corruption occurs, original names can be recovered from git history.

### 4. Manual Repair Script
**File:** `scripts/fix_missing_room_metadata.py`

Available for manual repairs if needed. Safe to run anytime - creates backups before modifications.

## Related Files

- `vault/chats_meta.json` - Room metadata (titles, timestamps, active runs)
- `scripts/fix_missing_room_metadata.py` - Repair script (can be run anytime)
- `utils/rooms_meta.py` - Metadata management utilities
- `server/routers/rooms.py` - API endpoints for room data
- `web/src/features/rooms/RoomsList.tsx` - Client-side room list component

## Technical Details

### Metadata Structure

Each room entry in `chats_meta.json` should have:
```json
{
  "room_id": {
    "title": "Room Title",
    "updated_at": 1760550770,
    "room_type": "chat",  // optional, "task" for task rooms
    "task_id": "...",     // optional, for task rooms only
    "active_run": {...}   // optional, when Theo is actively processing
  }
}
```

### Client Error Chain

1. Client polls `/api/rooms` on startup
2. Server returns rooms list with metadata
3. If metadata is missing/null, client can't display titles
4. This causes cascade failures in:
   - Room list rendering (blank titles)
   - Context setting (no valid room to select)
   - History loading (can't determine which room to load)

The errors were client-side manifestations of incomplete server data, not network or hostname issues.

