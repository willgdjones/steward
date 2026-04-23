"""Tests for the fake calendar provider."""
from __future__ import annotations

from steward.calendar.fake import FakeCalendar


def test_load_empty(tmp_path):
    c = FakeCalendar(tmp_path / "cal.json")
    assert c.load() == []


def test_create_and_get(tmp_path):
    c = FakeCalendar(tmp_path / "cal.json")
    event = c.create_event(
        title="Weekly standup",
        start="2026-05-01T09:00:00Z",
        end="2026-05-01T09:30:00Z",
        attendees=["alice@example.com"],
    )
    assert event["id"].startswith("evt_")
    assert event["title"] == "Weekly standup"
    assert event["status"] == "confirmed"
    assert c.get_event(event["id"]) == event


def test_list_excludes_declined(tmp_path):
    c = FakeCalendar(tmp_path / "cal.json")
    c.create_event(title="Meeting A", start="2026-05-01T09:00:00Z", end="2026-05-01T09:30:00Z")
    b = c.create_event(title="Meeting B", start="2026-05-02T09:00:00Z", end="2026-05-02T09:30:00Z")
    c.decline_event(b["id"])
    events = c.list_events()
    assert len(events) == 1
    assert events[0]["title"] == "Meeting A"


def test_decline_unknown_event(tmp_path):
    c = FakeCalendar(tmp_path / "cal.json")
    assert c.decline_event("evt_nonexistent") is False


def test_decline_persists(tmp_path):
    c = FakeCalendar(tmp_path / "cal.json")
    e = c.create_event(title="M", start="2026-05-01T09:00:00Z", end="2026-05-01T09:30:00Z")
    assert c.decline_event(e["id"]) is True
    # Reload from disk
    c2 = FakeCalendar(tmp_path / "cal.json")
    event = c2.get_event(e["id"])
    assert event["status"] == "declined"
