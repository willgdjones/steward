"""Slice 017 — calendar sub-agent e2e tests."""
from __future__ import annotations

import json
from pathlib import Path

from steward.calendar.fake import FakeCalendar
from steward.calendar.subagent import create_fake_calendar_sub_agent
from steward.rules import ReversibilityDecl, load_rules
from tests.conftest import empty_rules


def calendar_rules():
    r = empty_rules()
    r.reversibility = [
        ReversibilityDecl(action="read", reversible=True),
        ReversibilityDecl(action="create", reversible=False),
        ReversibilityDecl(action="decline", reversible=False),
    ]
    return r


async def test_decline_halts_then_declines_and_verifies(make_server, tmp_path):
    cal = FakeCalendar(tmp_path / "cal.json")
    event = cal.create_event(
        title="Dentist",
        start="2026-05-15T14:00:00Z",
        end="2026-05-15T15:00:00Z",
    )
    agent = create_fake_calendar_sub_agent(cal)

    async def plan_fn(_):
        return {
            "id": "g-decline",
            "title": "Decline dentist on the 15th",
            "reason": "I'm out of town",
            "messageId": "m1",
            "transport": "calendar",
            "action": "decline",
            "eventId": event["id"],
        }

    fixture = await make_server(
        messages=[{"id": "m1", "from": "dentist@example.com", "subject": "Appointment",
                   "body": "", "unread": True}],
        rules=calendar_rules(),
        plan=plan_fn,
        calendar_sub_agent=agent,
    )
    # Approve → halt (decline is irreversible)
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        halt = await r.json()
    assert halt["halted"] is True
    # Re-approval carries event id
    async with fixture.client.get("/card") as r:
        re_goal = await r.json()
    assert re_goal["action"] == "decline"
    assert re_goal["eventId"] == event["id"]
    # Approve re-approval → decline + verify
    async with fixture.client.post(f"/card/{re_goal['id']}/decision", json={"decision": "approve"}) as r:
        body = await r.json()
    assert body["ok"] is True
    assert body["outcomes"][0]["success"] is True
    assert body["verification"]["verified"] is True
    # Event is declined in the calendar
    assert cal.get_event(event["id"])["status"] == "declined"


async def test_create_halts_then_creates_and_verifies(make_server, tmp_path):
    cal = FakeCalendar(tmp_path / "cal.json")
    agent = create_fake_calendar_sub_agent(cal)

    async def plan_fn(_):
        return {
            "id": "g-create",
            "title": "Schedule follow-up with Alice",
            "reason": "Agreed to touch base next week",
            "messageId": "m1",
            "transport": "calendar",
            "action": "create",
            "eventTitle": "Follow-up with Alice",
            "eventStart": "2026-05-10T15:00:00Z",
            "eventEnd": "2026-05-10T15:30:00Z",
            "eventAttendees": ["alice@example.com"],
        }

    fixture = await make_server(
        messages=[{"id": "m1", "from": "alice@example.com", "subject": "Let's sync",
                   "body": "", "unread": True}],
        rules=calendar_rules(),
        plan=plan_fn,
        calendar_sub_agent=agent,
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        halt = await r.json()
    assert halt["halted"] is True
    async with fixture.client.get("/card") as r:
        re_goal = await r.json()
    assert re_goal["eventTitle"] == "Follow-up with Alice"
    async with fixture.client.post(f"/card/{re_goal['id']}/decision", json={"decision": "approve"}) as r:
        body = await r.json()
    assert body["ok"] is True
    event_id = body["outcomes"][0]["eventId"]
    assert event_id.startswith("evt_")
    event = cal.get_event(event_id)
    assert event["title"] == "Follow-up with Alice"
    assert event["attendees"] == ["alice@example.com"]
    assert body["verification"]["verified"] is True


async def test_read_does_not_halt_and_dispatches_immediately(make_server, tmp_path):
    cal = FakeCalendar(tmp_path / "cal.json")
    e = cal.create_event(title="Standup", start="2026-05-02T09:00:00Z", end="2026-05-02T09:30:00Z")
    agent = create_fake_calendar_sub_agent(cal)

    async def plan_fn(_):
        return {
            "id": "g-read",
            "title": "Read the standup event",
            "reason": "Sanity check",
            "messageId": "m1",
            "transport": "calendar",
            "action": "read",
            "eventId": e["id"],
        }

    fixture = await make_server(
        messages=[{"id": "m1", "from": "x@y.com", "subject": "x", "body": "", "unread": True}],
        rules=calendar_rules(),
        plan=plan_fn,
        calendar_sub_agent=agent,
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        body = await r.json()
    # No halt — read is reversible
    assert "halted" not in body
    assert body["ok"] is True
    assert body["outcomes"][0]["success"] is True
    assert body["outcomes"][0]["event"]["title"] == "Standup"


async def test_decline_unknown_event_journaled_as_failure(make_server, tmp_path):
    cal = FakeCalendar(tmp_path / "cal.json")
    agent = create_fake_calendar_sub_agent(cal)

    async def plan_fn(_):
        return {
            "id": "g-decline",
            "title": "Decline nonexistent",
            "reason": "x",
            "messageId": "m1",
            "transport": "calendar",
            "action": "decline",
            "eventId": "evt_nonexistent",
        }

    fixture = await make_server(
        messages=[{"id": "m1", "from": "x@y.com", "subject": "x", "body": "", "unread": True}],
        rules=calendar_rules(),
        plan=plan_fn,
        calendar_sub_agent=agent,
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        halt = await r.json()
    async with fixture.client.post(f"/card/{halt['reApprovalId']}/decision", json={"decision": "approve"}) as r:
        body = await r.json()
    assert body["outcomes"][0]["success"] is False
    assert body["verification"]["verified"] is False


def test_calendar_md_is_loaded_without_error(tmp_path):
    (tmp_path / "principles.md").write_text("blacklist: []\n")
    (tmp_path / "calendar.md").write_text("# soft rules\nauto_decline: []\n")
    rules = load_rules(tmp_path)
    # No parsed soft-rule schema yet; just confirm the loader doesn't crash.
    assert rules.blacklist == []


async def test_journal_records_calendar_action(make_server, tmp_path):
    cal = FakeCalendar(tmp_path / "cal.json")
    e = cal.create_event(title="Dentist", start="2026-05-15T14:00:00Z", end="2026-05-15T15:00:00Z")
    agent = create_fake_calendar_sub_agent(cal)

    async def plan_fn(_):
        return {
            "id": "g-decline",
            "title": "Decline dentist",
            "reason": "out of town",
            "messageId": "m1",
            "transport": "calendar",
            "action": "decline",
            "eventId": e["id"],
        }

    fixture = await make_server(
        messages=[{"id": "m1", "from": "x@y.com", "subject": "x", "body": "", "unread": True}],
        rules=calendar_rules(),
        plan=plan_fn,
        calendar_sub_agent=agent,
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        halt = await r.json()
    async with fixture.client.post(f"/card/{halt['reApprovalId']}/decision", json={"decision": "approve"}) as r:
        await r.json()
    entries = [json.loads(line) for line in Path(fixture.journal_path).read_text().strip().split("\n") if line]
    action = next(e for e in entries if e.get("kind") == "action" and e.get("action") == "decline")
    assert action["transport"] == "calendar"
    assert action["eventId"] == e["id"]
    assert action["verification"]["verified"] is True
