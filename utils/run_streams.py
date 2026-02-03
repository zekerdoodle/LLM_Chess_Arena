"""
Run stream persistence utilities (NDJSON + status).

Files under vault/runs/:
- <run_id>.ndjson  (append-only event stream; one JSON object per line)
- <run_id>.json    (status file: {"run_id","room_id","status","last_seq","updated_at"})
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional, Tuple
import time

from utils.logger import get_logger
from utils.vault_paths import get_vault_root

logger = get_logger(__name__)


def _runs_dir() -> Path:
    try:
        base = Path(get_vault_root()) / "runs"
    except Exception:
        base = Path("vault") / "runs"
    base.mkdir(parents=True, exist_ok=True)
    return base


def stream_path(run_id: str) -> Path:
    return _runs_dir() / f"{run_id}.ndjson"


def status_path(run_id: str) -> Path:
    return _runs_dir() / f"{run_id}.json"


def append_event(run_id: str, room_id: str, seq: int, evt: Dict) -> None:
    """Append an event to the run's NDJSON stream and update status file."""
    try:
        p = stream_path(run_id)
        rec = dict(evt)
        rec.setdefault("ts", int(time.time()))
        rec.setdefault("seq", seq)
        # Write line atomically-ish (append then fsync)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            try:
                f.flush(); os.fsync(f.fileno())
            except Exception:
                pass
    except Exception as e:
        logger.error(f"run_streams: append_event failed for {run_id}: {e}")
    try:
        sp = status_path(run_id)
        meta = {
            "run_id": run_id,
            "room_id": room_id,
            "status": str(evt.get("value") if evt.get("type") == "status" else None) or _load_status(sp).get("status", "running"),
            "last_seq": seq,
            "updated_at": int(time.time()),
        }
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"run_streams: update status failed for {run_id}: {e}")


def _load_status(p: Path) -> Dict:
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    return {}


def get_status(run_id: str) -> Dict:
    return _load_status(status_path(run_id))


def read_from_seq(run_id: str, start_seq: int | None) -> Iterator[Tuple[int, Dict]]:
    """Yield (seq, object) from the stream with seq > start_seq.

    If start_seq is None, yields from the beginning.
    """
    p = stream_path(run_id)
    if not p.exists():
        return iter(())  # empty iterator
    def _iter() -> Iterator[Tuple[int, Dict]]:
        try:
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        seq = int(obj.get("seq") or 0)
                        if start_seq is None or seq > start_seq:
                            yield seq, obj
                    except Exception:
                        continue
        except Exception as e:
            logger.error(f"run_streams: read_from_seq failed for {run_id}: {e}")
            return
    return _iter()


def tail_follow(run_id: str, start_seq: int) -> Iterator[Tuple[int, Dict]]:
    """Follow the file for new lines, yielding seq,obj when seq>start_seq.

    Simple polling tail; caller should run in an async wrapper with sleeps.
    """
    p = stream_path(run_id)
    last_seq = start_seq
    pos = 0
    try:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                f.seek(0, os.SEEK_END)
                pos = f.tell()
    except Exception:
        pos = 0
    while True:
        try:
            if not p.exists():
                time.sleep(0.2)
                continue
            with open(p, "r", encoding="utf-8") as f:
                f.seek(pos)
                for line in f:
                    pos = f.tell()
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        seq = int(obj.get("seq") or 0)
                        if seq > last_seq:
                            last_seq = seq
                            yield seq, obj
                    except Exception:
                        continue
        except Exception:
            time.sleep(0.2)
            continue
        time.sleep(0.2)

