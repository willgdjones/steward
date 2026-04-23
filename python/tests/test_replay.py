"""Tests for the replay harness (slice 020)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from steward.journal import append_journal
from steward.planner import Goal
from steward.replay import (
    ReplayResult,
    format_report,
    replay_entry,
    replay_journal,
)
from tests.conftest import empty_rules


def _decision_entry(**overrides):
    base = {
        "ts": "2026-04-20T12:00:00.000Z",
        "kind": "decision",
        "decision": "approve",
        "goalId": "g1",
        "messageId": "m1",
        "title": "Archive newsletter",
        "transport": "gmail",
        "action": "archive",
        "features": {
            "deadline": None,
            "amount": None,
            "waiting_on_user": False,
            "category": "newsletter",
            "urgency": "low",
        },
        "redactedMessage": {
            "id": "m1",
            "fromDomain": "substack.com",
            "subject": "Weekly digest",
        },
        "snippet": "A weekly newsletter digest",
    }
    base.update(overrides)
    return base


async def _static_plan(_input):
    return Goal(
        id="g-x",
        title="Archive",
        reason="stub",
        messageId="m1",
        transport="gmail",
        action="archive",
    )


async def _always_reply_plan(input_):
    return Goal(
        id="g-y",
        title="Reply",
        reason="stub",
        messageId=input_["message"]["id"],
        transport="gmail",
        action="reply",
    )


async def _always_browser_plan(input_):
    return Goal(
        id="g-z",
        title="Browser thing",
        reason="stub",
        messageId=input_["message"]["id"],
        transport="browser",
        action="browser_read",
    )


async def test_replay_entry_no_divergence_when_same_action():
    entry = _decision_entry()
    result = await replay_entry(entry, _static_plan, empty_rules())
    assert result is not None
    assert result.diverged is False
    assert result.historical["action"] == "archive"
    assert result.new["action"] == "archive"


async def test_replay_entry_detects_action_change():
    entry = _decision_entry()
    result = await replay_entry(entry, _always_reply_plan, empty_rules())
    assert result is not None
    assert result.diverged is True
    assert result.reason == "action: archive → reply"
    assert result.historical["action"] == "archive"
    assert result.new["action"] == "reply"


async def test_replay_entry_detects_transport_change():
    entry = _decision_entry()
    result = await replay_entry(entry, _always_browser_plan, empty_rules())
    assert result is not None
    assert result.diverged is True
    assert "transport: gmail → browser" in result.reason


async def test_replay_entry_returns_none_for_unreplayable():
    # Missing redactedMessage
    entry = _decision_entry()
    del entry["redactedMessage"]
    assert await replay_entry(entry, _static_plan, empty_rules()) is None

    # Missing features
    entry = _decision_entry()
    del entry["features"]
    assert await replay_entry(entry, _static_plan, empty_rules()) is None

    # Wrong kind
    entry = _decision_entry(kind="blocked")
    assert await replay_entry(entry, _static_plan, empty_rules()) is None


async def test_replay_entry_applies_current_redaction_rules():
    """If rules tighten after the entry was recorded, the replay planner sees
    the more-redacted input. Here: the historical snapshot has a subject that
    a new redaction rule now strips."""
    entry = _decision_entry()
    entry["redactedMessage"] = {
        "id": "m1",
        "fromDomain": "bank.com",
        "subject": "Account 12345678 ready",
    }
    captured = {}

    async def capturing_plan(input_):
        captured["subject"] = input_["message"]["subject"]
        return Goal(
            id="g-x", title="t", reason="r", messageId="m1",
            transport="gmail", action="archive",
        )

    rules = empty_rules(redaction=[{"field": "subject", "pattern": r"\d{8}"}])
    await replay_entry(entry, capturing_plan, rules)
    assert captured["subject"] == "Account [REDACTED] ready"


async def test_replay_journal_skips_non_replayable(tmp_path):
    jp = tmp_path / "journal.jsonl"
    # Replayable
    append_journal(jp, _decision_entry(goalId="g1", messageId="m1"))
    # Not replayable — no redactedMessage
    e2 = _decision_entry(goalId="g2", messageId="m2")
    del e2["redactedMessage"]
    append_journal(jp, e2)
    # Not replayable — verifier_anomaly kind
    append_journal(jp, {"kind": "verifier_anomaly", "goalId": "g3", "messageId": "m3"})

    results = await replay_journal(str(jp), _static_plan, empty_rules())
    assert len(results) == 1
    assert results[0].entry["goalId"] == "g1"


async def test_replay_journal_filters_by_since(tmp_path):
    jp = tmp_path / "journal.jsonl"
    old = _decision_entry(goalId="g-old", messageId="m-old")
    old["ts"] = "2026-04-01T00:00:00.000Z"
    new = _decision_entry(goalId="g-new", messageId="m-new")
    new["ts"] = "2026-04-20T00:00:00.000Z"
    append_journal(jp, old)
    append_journal(jp, new)

    since = datetime(2026, 4, 10, tzinfo=timezone.utc)
    results = await replay_journal(str(jp), _static_plan, empty_rules(), since=since)
    assert len(results) == 1
    assert results[0].entry["goalId"] == "g-new"


async def test_replay_journal_full_flow_with_divergences(tmp_path):
    jp = tmp_path / "journal.jsonl"
    for i in range(3):
        append_journal(jp, _decision_entry(goalId=f"g{i}", messageId=f"m{i}"))

    # Planner that always replies — all three entries should diverge
    results = await replay_journal(str(jp), _always_reply_plan, empty_rules())
    assert len(results) == 3
    assert all(r.diverged for r in results)
    assert all("action: archive → reply" in (r.reason or "") for r in results)


def test_format_report_shows_divergences():
    entries = [
        ReplayResult(
            entry={"ts": "2026-04-20T12:00:00Z", "messageId": "m1"},
            historical={"transport": "gmail", "action": "archive", "title": "Archive newsletter"},
            new={"transport": "gmail", "action": "reply", "title": "Reply"},
            diverged=True,
            reason="action: archive → reply",
        ),
        ReplayResult(
            entry={"ts": "2026-04-20T13:00:00Z", "messageId": "m2"},
            historical={"transport": "gmail", "action": "archive", "title": "Archive"},
            new={"transport": "gmail", "action": "archive", "title": "Archive"},
            diverged=False,
        ),
    ]
    report = format_report(entries)
    assert "Replayed 2 entries, 1 divergent" in report
    assert "DIVERGENCE" in report
    assert "m1" in report
    assert "action: archive → reply" in report
    # Non-divergent entries don't show detail lines
    assert "m2" not in report


def test_format_report_empty():
    assert "No replayable entries" in format_report([])


async def test_replay_works_on_action_entries():
    """Action entries carry features + redactedMessage from slice 020's enhancement,
    so replay should work on them too — not just decisions."""
    entry = _decision_entry(kind="action")
    result = await replay_entry(entry, _always_reply_plan, empty_rules())
    assert result is not None
    assert result.diverged is True
