"""Fake calendar provider. Stands in for Google Calendar.

Events are plain dicts on-disk (one JSON file). Tests stage a calendar state
and the executor reads / writes against it, same pattern as FakeGmail.
"""
from __future__ import annotations

import json
import random
import string
from pathlib import Path
from typing import Any


class FakeCalendar:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, events: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(events, indent=2))

    def list_events(self) -> list[dict[str, Any]]:
        """Return all events that aren't declined or cancelled."""
        return [e for e in self.load() if e.get("status", "confirmed") not in ("declined", "cancelled")]

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        for e in self.load():
            if e.get("id") == event_id:
                return e
        return None

    def create_event(
        self,
        *,
        title: str,
        start: str,
        end: str,
        attendees: list[str] | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        events = self.load()
        event_id = "evt_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        event: dict[str, Any] = {
            "id": event_id,
            "title": title,
            "start": start,
            "end": end,
            "attendees": list(attendees or []),
            "status": "confirmed",
        }
        if description is not None:
            event["description"] = description
        events.append(event)
        self.save(events)
        return event

    def decline_event(self, event_id: str) -> bool:
        events = self.load()
        for e in events:
            if e.get("id") == event_id:
                e["status"] = "declined"
                self.save(events)
                return True
        return False
