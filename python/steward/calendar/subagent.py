"""Calendar sub-agent — read (reversible), create + decline (irreversible).

Instruction capabilities:
- "read": list upcoming events (or get one by id)
- "create": create a new event from title/start/end/attendees
- "decline": mark an event declined by id
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from steward.calendar.fake import FakeCalendar

READ_CAPABILITY = "read"
CREATE_CAPABILITY = "create"
DECLINE_CAPABILITY = "decline"


class CalendarSubAgent(Protocol):
    async def dispatch(self, instruction: dict[str, Any]) -> dict[str, Any]: ...
    async def verify(
        self,
        event_id: str,
        expected_action: str,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


@dataclass
class FakeCalendarSubAgent:
    calendar: FakeCalendar

    async def dispatch(self, instruction: dict[str, Any]) -> dict[str, Any]:
        cap = instruction.get("capability")
        if cap == READ_CAPABILITY:
            return self._dispatch_read(instruction)
        if cap == CREATE_CAPABILITY:
            return self._dispatch_create(instruction)
        if cap == DECLINE_CAPABILITY:
            return self._dispatch_decline(instruction)
        return {
            "success": False,
            "action_taken": cap or "unknown",
            "error": f"unknown capability: {cap}",
        }

    def _dispatch_read(self, instruction: dict[str, Any]) -> dict[str, Any]:
        event_id = instruction.get("eventId")
        if event_id:
            event = self.calendar.get_event(event_id)
            if not event:
                return {
                    "success": False,
                    "action_taken": READ_CAPABILITY,
                    "error": f"event not found: {event_id}",
                }
            return {
                "success": True,
                "action_taken": READ_CAPABILITY,
                "event": dict(event),
            }
        return {
            "success": True,
            "action_taken": READ_CAPABILITY,
            "events": [dict(e) for e in self.calendar.list_events()],
        }

    def _dispatch_create(self, instruction: dict[str, Any]) -> dict[str, Any]:
        title = instruction.get("title")
        start = instruction.get("start")
        end = instruction.get("end")
        attendees = instruction.get("attendees")
        if not title or not start or not end:
            return {
                "success": False,
                "action_taken": CREATE_CAPABILITY,
                "error": "missing required field (title, start, end)",
            }
        event = self.calendar.create_event(
            title=title,
            start=start,
            end=end,
            attendees=attendees,
            description=instruction.get("description"),
        )
        return {
            "success": True,
            "action_taken": CREATE_CAPABILITY,
            "eventId": event["id"],
            "event": dict(event),
        }

    def _dispatch_decline(self, instruction: dict[str, Any]) -> dict[str, Any]:
        event_id = instruction.get("eventId")
        if not event_id:
            return {
                "success": False,
                "action_taken": DECLINE_CAPABILITY,
                "error": "no eventId provided",
            }
        found = self.calendar.decline_event(event_id)
        if not found:
            return {
                "success": False,
                "action_taken": DECLINE_CAPABILITY,
                "eventId": event_id,
                "error": f"event not found: {event_id}",
            }
        return {
            "success": True,
            "action_taken": DECLINE_CAPABILITY,
            "eventId": event_id,
        }

    async def verify(
        self,
        event_id: str,
        expected_action: str,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if expected_action == CREATE_CAPABILITY:
            event = self.calendar.get_event(event_id)
            if not event:
                return {"verified": False, "actual_state": "not_found", "eventId": event_id}
            # Optionally confirm expected title/start matches
            if meta:
                if meta.get("title") and event.get("title") != meta["title"]:
                    return {"verified": False, "actual_state": "title_mismatch", "eventId": event_id}
                if meta.get("start") and event.get("start") != meta["start"]:
                    return {"verified": False, "actual_state": "start_mismatch", "eventId": event_id}
            return {
                "verified": event.get("status") == "confirmed",
                "actual_state": event.get("status", "unknown"),
                "eventId": event_id,
            }
        if expected_action == DECLINE_CAPABILITY:
            event = self.calendar.get_event(event_id)
            if not event:
                return {"verified": False, "actual_state": "not_found", "eventId": event_id}
            declined = event.get("status") == "declined"
            return {
                "verified": declined,
                "actual_state": event.get("status", "unknown"),
                "eventId": event_id,
            }
        if expected_action == READ_CAPABILITY:
            # Read is reversible — verification just confirms the event exists.
            event = self.calendar.get_event(event_id) if event_id else None
            return {
                "verified": event is not None or not event_id,
                "actual_state": event.get("status") if event else "no_event_id",
                "eventId": event_id,
            }
        return {"verified": False, "actual_state": "unknown", "eventId": event_id}


def create_fake_calendar_sub_agent(calendar: FakeCalendar) -> FakeCalendarSubAgent:
    return FakeCalendarSubAgent(calendar=calendar)
