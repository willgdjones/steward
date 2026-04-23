"""Tests for the calendar sub-agent."""
from __future__ import annotations

import pytest

from steward.calendar.fake import FakeCalendar
from steward.calendar.subagent import create_fake_calendar_sub_agent


@pytest.fixture
def agent(tmp_path):
    cal = FakeCalendar(tmp_path / "cal.json")
    cal.create_event(
        title="Dentist", start="2026-05-15T14:00:00Z", end="2026-05-15T15:00:00Z",
        attendees=["dentist@example.com"],
    )
    cal.create_event(
        title="Standup", start="2026-05-02T09:00:00Z", end="2026-05-02T09:30:00Z",
    )
    a = create_fake_calendar_sub_agent(cal)
    a._cal = cal  # hand through for test assertions
    return a


async def test_read_lists_all_events(agent):
    outcome = await agent.dispatch({"capability": "read"})
    assert outcome["success"] is True
    assert len(outcome["events"]) == 2
    titles = {e["title"] for e in outcome["events"]}
    assert titles == {"Dentist", "Standup"}


async def test_read_single_event(agent):
    events = agent._cal.list_events()
    dentist_id = next(e["id"] for e in events if e["title"] == "Dentist")
    outcome = await agent.dispatch({"capability": "read", "eventId": dentist_id})
    assert outcome["success"] is True
    assert outcome["event"]["title"] == "Dentist"


async def test_read_unknown_event(agent):
    outcome = await agent.dispatch({"capability": "read", "eventId": "evt_nope"})
    assert outcome["success"] is False
    assert "not found" in outcome["error"]


async def test_create_event_happy_path(agent):
    outcome = await agent.dispatch({
        "capability": "create",
        "title": "Design review",
        "start": "2026-05-10T10:00:00Z",
        "end": "2026-05-10T11:00:00Z",
        "attendees": ["bob@example.com"],
    })
    assert outcome["success"] is True
    assert outcome["action_taken"] == "create"
    assert outcome["eventId"].startswith("evt_")
    # Event exists with the right data
    event = agent._cal.get_event(outcome["eventId"])
    assert event["title"] == "Design review"
    assert event["attendees"] == ["bob@example.com"]


async def test_create_rejects_missing_fields(agent):
    outcome = await agent.dispatch({"capability": "create", "title": "x"})
    assert outcome["success"] is False
    assert "missing required field" in outcome["error"]


async def test_decline_existing(agent):
    events = agent._cal.list_events()
    dentist_id = next(e["id"] for e in events if e["title"] == "Dentist")
    outcome = await agent.dispatch({"capability": "decline", "eventId": dentist_id})
    assert outcome["success"] is True
    assert outcome["eventId"] == dentist_id
    # Event is now declined
    event = agent._cal.get_event(dentist_id)
    assert event["status"] == "declined"


async def test_decline_unknown(agent):
    outcome = await agent.dispatch({"capability": "decline", "eventId": "evt_nope"})
    assert outcome["success"] is False
    assert "not found" in outcome["error"]


async def test_decline_missing_event_id(agent):
    outcome = await agent.dispatch({"capability": "decline"})
    assert outcome["success"] is False
    assert "no eventId" in outcome["error"]


async def test_unknown_capability(agent):
    outcome = await agent.dispatch({"capability": "reschedule", "eventId": "x"})
    assert outcome["success"] is False
    assert "unknown capability" in outcome["error"]


async def test_verify_create_confirms_title_and_start(agent):
    outcome = await agent.dispatch({
        "capability": "create",
        "title": "Lunch",
        "start": "2026-05-20T12:00:00Z",
        "end": "2026-05-20T13:00:00Z",
    })
    v = await agent.verify(outcome["eventId"], "create", {"title": "Lunch", "start": "2026-05-20T12:00:00Z"})
    assert v["verified"] is True


async def test_verify_create_title_mismatch(agent):
    outcome = await agent.dispatch({
        "capability": "create",
        "title": "Lunch",
        "start": "2026-05-20T12:00:00Z",
        "end": "2026-05-20T13:00:00Z",
    })
    v = await agent.verify(outcome["eventId"], "create", {"title": "Dinner"})
    assert v["verified"] is False
    assert v["actual_state"] == "title_mismatch"


async def test_verify_decline_success(agent):
    events = agent._cal.list_events()
    eid = next(e["id"] for e in events if e["title"] == "Dentist")
    await agent.dispatch({"capability": "decline", "eventId": eid})
    v = await agent.verify(eid, "decline")
    assert v["verified"] is True
    assert v["actual_state"] == "declined"


async def test_verify_decline_not_declined(agent):
    events = agent._cal.list_events()
    eid = next(e["id"] for e in events if e["title"] == "Standup")
    v = await agent.verify(eid, "decline")
    assert v["verified"] is False
    assert v["actual_state"] == "confirmed"
