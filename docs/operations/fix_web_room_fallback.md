# Fix: Web Room Fallback 400 Errors

## Problem

On startup, the frontend would receive numerous 400 errors when trying to set "web" as the active room:

### Server Logs
```
[WARNING] client[warn] rooms: poll failed
[WARNING] client[warn] rooms: set active failed
```

### Browser Console
```
POST http://debian:8000/api/rooms/active 400 (Bad Request)
[Theo core warn] rooms: set active failed {status: 400, error: '{"detail":"Room not found or invalid: web"}'}
```

## Root Cause

1. **Inconsistent Fallback**: The GET `/api/rooms/active` endpoint returns `"web"` as a fallback when no room is set
2. **Missing File**: The "web" room file (`vault/chats/web.json`) didn't exist
3. **Validation Failure**: POST `/api/rooms/active` validates that rooms exist on disk
4. **400 Error**: Frontend receives error when trying to set the fallback room

The disconnect: Backend **returns** "web" as valid, but **rejects** it when frontend tries to use it.

## Solution

### Auto-Create Web Room on Startup

Modified `server/app.py` to create the "web" room file on startup:

```python
@app.on_event("startup")
async def _startup_ensure_default_room():
    """Ensure the default 'web' room exists to prevent 400 errors on startup.
    
    The 'web' room is used as a fallback throughout the codebase when no other
    room is available. Creating it at startup prevents validation errors when
    the frontend tries to set it as the active room.
    """
    try:
        import json
        import time
        vault_root = str(get_vault_root())
        chats_dir = os.path.join(vault_root, "chats")
        os.makedirs(chats_dir, exist_ok=True)
        
        web_room_path = os.path.join(chats_dir, "web.json")
        if not os.path.exists(web_room_path):
            with open(web_room_path, "w", encoding="utf-8") as f:
                json.dump([], f)
            logger.info("Web startup - Created default 'web' room")
            
            # Add metadata for the web room
            try:
                from utils.rooms_meta import load as rooms_meta_load, save as rooms_meta_save
                meta = rooms_meta_load()
                if "web" not in meta:
                    meta["web"] = {
                        "title": "General Chat",
                        "updated_at": int(time.time())
                    }
                    rooms_meta_save(meta)
            except Exception:
                pass  # Metadata is optional, don't fail startup
    except Exception as e:
        logger.debug(f"Web startup - Default room creation skipped: {e}")
```

**Key Features:**
- Creates `vault/chats/web.json` with empty array `[]`
- Adds metadata with title "General Chat"
- Idempotent: Only creates if doesn't exist
- Graceful: Won't crash startup if creation fails

## Testing

Created comprehensive test suite in `tests/test_web_room_creation.py`:

1. **test_web_room_exists_after_startup**: Verifies file is created
2. **test_web_room_can_be_set_as_active**: Tests setting "web" as active succeeds
3. **test_web_room_validation**: Confirms validation accepts "web"

All tests pass ✓

## Verification

### Before Fix
```bash
curl -X POST http://localhost:8000/api/rooms/active \
  -H "Content-Type: application/json" \
  -d '{"room_id": "web", "context": "room:web"}'
# Returns: {"detail":"Room not found or invalid: web"}
```

### After Fix
```bash
# GET returns web as fallback
curl http://localhost:8000/api/rooms/active
# Returns: {"room": "web", "context": null}

# POST accepts web without error
curl -X POST http://localhost:8000/api/rooms/active \
  -H "Content-Type: application/json" \
  -d '{"room_id": "web", "context": "room:web"}'
# Returns: {"ok": true, "room": "web", "context": "room:web"}

# Web appears in rooms list
curl http://localhost:8000/api/rooms | python3 -m json.tool
# Returns: {"rooms": ["task1759410120200", "web"], "meta": {...}}
```

### Server Startup
Clean startup without errors:
```
[INFO] Started server process [237906]
[INFO] Application startup complete.
[INFO] Uvicorn running on http://0.0.0.0:8000
```

## Codebase Context

The "web" room is used throughout the codebase as a fallback:

- `utils/rooms.py`: `default_room: str = "web"`
- `utils/queue_processor.py`: Falls back to "web" if no room available
- `layer4_tools/web_tools.py`: `return "web", False` as last resort
- `web/src/hooks/useComposer.ts`: Uses "web" as fallback room

This fix ensures consistency between backend fallback logic and file system state.

## Related Fixes

This fix complements the earlier stale room cleanup fix (see `fix_stale_room_cleanup.md`):
- **Stale cleanup**: Removes invalid room references
- **Web room creation**: Ensures fallback room always exists

Together, these fixes provide robust room management:
1. Invalid rooms are automatically cleaned up
2. Fallback room is always available
3. No 400 errors on startup

## Impact

- **Before**: Multiple 400 errors on every startup/page refresh
- **After**: Clean startup, no validation errors
- **Reliability**: Web room always available as fallback
- **UX**: Seamless experience, no error messages in console

