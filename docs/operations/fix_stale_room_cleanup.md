# Fix: Stale Active Room References

## Problem

When the server started up after rooms were deleted (e.g., `vault/active_room.json` and room files like `vault/chats/task1759408822867.json`), the system would generate numerous errors:

### Server Logs
```
[WARNING] client[warn] rooms: poll failed
[WARNING] client[warn] rooms: set active failed
```

### Browser Console
```
Failed to load resource: the server responded with a status of 400 (Bad Request)
[Theo core warn] rooms: set active failed
```

## Root Cause

1. **Stale Reference**: `vault/active_room.json` referenced a room that no longer existed
2. **Validation Failure**: Backend's `_is_valid_room()` checked if room file exists on disk
3. **No Cleanup**: System never cleaned up stale references, causing 400 errors
4. **Cascading Failures**: Frontend kept retrying with cached/stale room IDs

## Solution

### 1. Automatic Cleanup in `utils/active_room.py`

Modified `get_active_payload()` to automatically detect and clean up stale room references:

```python
# Check if room is valid; if not, clean it up
if room is not None:
    if not isinstance(room, str) or not _is_valid_room(room):
        logger.debug(f"Cleaning up stale active room reference: {room}")
        room = None
        # Clean up the file to remove the stale reference
        try:
            cleaned_data = {"room": None, "updated_at": int(time.time()), "context": context}
            with open(_ACTIVE_PATH, "w", encoding="utf-8") as f:
                json.dump(cleaned_data, f)
        except Exception as cleanup_exc:
            logger.debug(f"Failed to clean up stale room reference: {cleanup_exc}")
```

**Benefits:**
- Self-healing: Detects invalid rooms on GET requests
- Preserves context: Keeps UI context while clearing invalid room
- Idempotent: Safe to call multiple times

### 2. Better Error Handling in `server/routers/rooms.py`

Enhanced the POST `/api/rooms/active` endpoint with clearer error messages:

```python
if not ok:
    logger.debug(f"rooms.set_active_room - rejected room '{name}': {msg}")
    raise HTTPException(status_code=400, detail=f"Room not found or invalid: {name}")
```

**Benefits:**
- Clear feedback about why a room was rejected
- Proper logging for debugging
- Consistent error responses

## Testing

Created comprehensive test suite in `tests/test_active_room_stale_cleanup.py`:

1. **test_get_active_payload_cleans_up_stale_room**: Verifies automatic cleanup
2. **test_get_active_payload_with_missing_file**: Tests missing file handling
3. **test_get_active_payload_preserves_context_during_cleanup**: Ensures context preservation

All tests pass ✓

## Verification

### API Tests
```bash
# GET /api/rooms/active - Returns proper fallback when no room set
curl http://localhost:8000/api/rooms/active
# Returns: {"room": "web", "context": null}

# POST /api/rooms/active - Accepts valid rooms
curl -X POST http://localhost:8000/api/rooms/active \
  -H "Content-Type: application/json" \
  -d '{"room_id": "task1759409561346", "context": "room:task1759409561346"}'
# Returns: {"ok": true, "room": "task1759409561346", "context": "room:task1759409561346"}

# POST /api/rooms/active - Rejects invalid rooms with clear error
curl -X POST http://localhost:8000/api/rooms/active \
  -H "Content-Type: application/json" \
  -d '{"room_id": "nonexistent_room", "context": "room:nonexistent"}'
# Returns: {"detail":"Room not found or invalid: nonexistent_room"}
```

### Server Startup
Clean startup with no cascading errors:
```
[INFO] Started server process [235262]
[INFO] Application startup complete.
[INFO] Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

## Impact

- **Before**: Startup generated 10+ errors, 400 responses, poor UX
- **After**: Clean startup, automatic recovery, clear error messages
- **Resilience**: System self-heals when rooms are deleted
- **UX**: No user-visible errors on fresh sessions

## Notes

- Client-side warnings from stale browser sessions are expected during server restarts
- These warnings stop once the browser refreshes or clears localStorage
- The fix prevents the issue from affecting fresh sessions or new browser tabs

