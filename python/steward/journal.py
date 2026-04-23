"""Append-only JSONL decision journal.

Every non-trivial decision is recorded here by the executor. The LLM
process never writes the journal — this preserves integrity if a
planner goes off the rails.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"


def append_journal(path: str | Path, entry: dict[str, Any]) -> dict[str, Any]:
    """Append an entry as a JSONL line, stamping ts. Returns the full entry."""
    full: dict[str, Any] = {"ts": _now_iso(), **entry}
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(full) + "\n")
    return full


def read_journal(path: str | Path) -> list[dict[str, Any]]:
    """Read all journal entries. Returns [] if the file doesn't exist or is empty."""
    p = Path(path)
    if not p.exists():
        return []
    content = p.read_text(encoding="utf-8").strip()
    if not content:
        return []
    return [json.loads(line) for line in content.split("\n")]
