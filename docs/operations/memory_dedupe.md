# Memory Dedupe Sweep

Theo can remove overlapping Theo↔Verbatim memories and rebuild the embedding index with a dedicated maintenance task. Use this runbook before large migrations or after bulk memory imports.

## Command

```bash
python theo.py maintenance-dedupe --threshold 0.90 --preview
```

- `--threshold` (float, default `0.92`): cosine similarity threshold at which verbatim chunks are considered duplicates of Theo memories.
- `--preview/--apply`: run in preview mode (no deletions) or apply the cleanup. Omit `--preview` to delete duplicates and rebuild embeddings.

## Steps

1. **Preview**
   - Run with `--preview` to inspect how many verbatim chunks would be removed.
   - Capture the JSON output and attach it to your maintenance notes.
2. **Apply (optional)**
   - Re-run without `--preview` when you are confident in the plan.
   - Theo rebuilds the embeddings index after cleanup; monitor logs for `[DEDupe]` messages.
3. **Regression**
   - Execute `python -m pytest tests/test_maintenance_dedupe.py -k apply` to ensure the dedupe helpers remain covered.
   - Optionally run `python theo.py housekeeping --keep-clones 3 --keep-archives 10` afterwards to trim old clones/archives.

## Expected Output

The task emits JSON similar to:

```json
{
  "identified_verbatim_duplicates": 12,
  "removed_verbatim_duplicates": 12,
  "removed_chunk_ids": ["chunk_123", "chunk_456"],
  "preview": false,
  "similarity_threshold": 0.9
}
```

If the embedding rebuild fails you will see `[DEDupe] Failed to rebuild embedding index`; investigate logs before rerunning.
