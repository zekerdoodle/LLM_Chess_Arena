# Room Metadata Persistence Guide

**Date:** October 15, 2025  
**Status:** ✓ Production-Ready

## Overview

Room metadata (chat names, update timestamps, active runs) is stored in `vault/chats_meta.json`. This file is critical for the UI to display room names correctly. This document describes how metadata persistence works and the safeguards in place.

## Architecture

### Storage Location
```
vault/
  chats/
    r1760454120720.json          # Chat history
    r1760460505517.json
    ...
  chats_meta.json                 # ← Metadata for all rooms
```

### Metadata Structure
```json
{
  "r1760454120720": {
    "title": "First Self-Improving AI",
    "updated_at": 1760461833,
    "active_run": {...}             // Optional: current processing run
  },
  "task1760454090686": {
    "title": "Task · Daily Standup",
    "updated_at": 1760538604,
    "room_type": "task",
    "task_id": "31caea12"
  }
}
```

## Protection Mechanisms

### 1. Startup Validation (Automatic)

**When:** Every server start  
**File:** `utils/rooms_meta_validator.py`

The validator:
1. Lists all `*.json` files in `vault/chats/`
2. Checks if each room ID exists in `chats_meta.json`
3. If missing: Extracts title from first user message in chat history
4. Creates missing entries automatically
5. Logs repair actions for monitoring

**Log Output Example:**
```
[INFO] Web startup - Room metadata validation: All 10 rooms validated
```

Or if repairs needed:
```
[WARNING] RoomsMetaValidator: Found 3 rooms without metadata
[INFO] RoomsMetaValidator: Repairing: r1760531599370, r1760472077271, r1760461582395
[INFO] Web startup - Room metadata validation: Repaired 3 missing metadata entries
```

### 2. Atomic Writes (Automatic)

**When:** Every metadata save  
**File:** `utils/rooms_meta.py`

Prevents corruption during:
- System crashes
- Power loss
- Disk full conditions
- Process termination

**How it works:**
1. Write data to temporary file: `.chats_meta_tmp_XXXXX.json`
2. Verify write succeeded
3. Atomically rename temp file to `chats_meta.json`
4. Old file is replaced only after new file is complete

**Atomic Guarantee:**
- POSIX `os.replace()` is atomic on same filesystem
- No partial writes ever visible
- Either old data or new data, never corrupted mix

### 3. Git Backups (Automatic)

**When:** Every 10 minutes  
**File:** Managed by scheduler in `layer4_tools/self_patch_tools.py`

Regular automated backups to git include `vault/chats_meta.json`. To recover previous metadata:

```bash
# List recent backups
git log --oneline -20 -- vault/chats_meta.json

# View metadata from specific backup
git show <commit-hash>:vault/chats_meta.json

# Restore from backup
git show <commit-hash>:vault/chats_meta.json > vault/chats_meta.json
```

### 4. Manual Repair (On-Demand)

**File:** `scripts/fix_missing_room_metadata.py`

Run manually if you notice missing room names:

```bash
cd /home/debian/Projects/Theo
python3 scripts/fix_missing_room_metadata.py
```

The script:
- Creates `.backup` file before changes
- Extracts titles from chat history
- Reports all repairs with details
- Safe to run multiple times (idempotent)

## Common Issues & Solutions

### Issue: Room names disappeared after reboot

**Cause:** Metadata file was corrupted or partially overwritten

**Solution:** Restart Theo - the validator will auto-repair missing entries

**To restore original names from git:**
```bash
# Find backup before the issue
git log --since="2025-10-15 12:00" -- vault/chats_meta.json

# Restore that version
git show <commit-hash>:vault/chats_meta.json > vault/chats_meta.json

# Restart Theo
python theo.py restart
```

### Issue: New room has no name

**Cause:** Room created but metadata not written properly

**Solution:**
1. Automatic: Validator will repair on next restart
2. Manual: Run `python3 scripts/fix_missing_room_metadata.py`
3. From UI: Double-click room name to rename it

### Issue: Metadata file corrupted

**Symptoms:**
- JSON parse errors in logs
- `RoomsMeta: failed to load` warnings
- All rooms show as "New Chat"

**Solution:**
```bash
# Restore from most recent git backup
git log -1 --oneline -- vault/chats_meta.json
git checkout HEAD~1 -- vault/chats_meta.json

# Restart Theo
python theo.py restart
```

## Monitoring

### Logs to Watch

**Successful validation:**
```
[INFO] RoomsMetaValidator: All 10 rooms have metadata ✓
[INFO] Web startup - Room metadata validation: All 10 rooms validated
```

**Automatic repairs:**
```
[WARNING] RoomsMetaValidator: Found 2 rooms without metadata
[INFO] RoomsMetaValidator: Added metadata for r1760531599370: 'Message from my new galaxy s25'
```

**Save errors:**
```
[ERROR] RoomsMeta: failed to save: [Errno 28] No space left on device
```

### Health Check Command

```bash
# Verify all rooms have metadata
cd /home/debian/Projects/Theo
python3 << 'EOF'
from utils.rooms_meta_validator import validate_and_repair
success, message = validate_and_repair()
print(f"Status: {'✓ OK' if success else '✗ FAIL'}")
print(f"Details: {message}")
EOF
```

## API Reference

### `utils.rooms_meta_validator`

#### `validate_and_repair() -> Tuple[bool, str]`
Checks all rooms have metadata, repairs if needed.

**Returns:**
- `(True, "All 10 rooms validated")` - All OK
- `(True, "Repaired 3 missing metadata entries")` - Auto-fixed
- `(False, "Validation error: ...")` - Failed

#### `atomic_save(meta: Dict) -> None`
Save metadata with atomic write guarantee.

**Raises:** `Exception` if save fails

### `utils.rooms_meta`

#### `load() -> Dict`
Load metadata, returns `{}` if file missing/corrupt.

#### `save(meta: Dict) -> None`
Save metadata using atomic writes.

#### `sanitize(meta: Dict) -> Dict`
Normalize metadata structure, coerce types.

#### `bump(room_id: str) -> None`
Update `updated_at` timestamp for a room.

## Testing

### Unit Tests
```bash
pytest tests/test_rooms_meta_utils.py -v
pytest tests/test_rooms_meta_validator.py -v  # If test file exists
```

### Integration Test
```bash
# 1. Start Theo
python theo.py start

# 2. Watch logs for validation message
tail -f logs/theo.log | grep -i "room metadata validation"

# 3. Create a new room via UI
# 4. Check metadata file includes new room
cat vault/chats_meta.json | python3 -m json.tool | grep -A 3 "r[0-9]"
```

### Corruption Recovery Test
```bash
# Simulate corruption by removing entries
python3 << 'EOF'
import json
with open('vault/chats_meta.json') as f:
    meta = json.load(f)
# Keep only task rooms
meta = {k: v for k, v in meta.items() if v.get('room_type') == 'task'}
with open('vault/chats_meta.json', 'w') as f:
    json.dump(meta, f, indent=2)
print(f"Reduced to {len(meta)} entries")
EOF

# Restart and verify auto-repair
python theo.py restart
# Should see: "Repaired N missing metadata entries" in logs
```

## Future Improvements

Potential enhancements to consider:

1. **Periodic Validation:** Run validator every hour, not just at startup
2. **Metadata Versioning:** Track schema version for migrations
3. **Redundant Storage:** Mirror critical metadata in SQLite
4. **Checksums:** Detect silent corruption with file hash verification
5. **Recovery UI:** Let users restore from git backups via web interface

## Related Files

- `utils/rooms_meta.py` - Core metadata operations
- `utils/rooms_meta_validator.py` - Validation and repair logic
- `server/routers/rooms.py` - API endpoints for room operations
- `server/app.py` - Startup validation hook
- `scripts/fix_missing_room_metadata.py` - Manual repair tool
- `vault/chats_meta.json` - The metadata file itself

## Change Log

- **2025-10-15:** Added startup validator, atomic saves, and repair script
- **2025-10-14:** Initial metadata system (pre-validation)

