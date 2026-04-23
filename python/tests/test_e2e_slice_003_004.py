"""Slices 003 (principles gate + redactor rules) and 004 (two-stage pipeline)."""
import json
from pathlib import Path

from steward.planner import plan_goal
from steward.rules import (
    BlacklistEntry,
    PromotionConfig,
    QueueConfig,
    RedactionRule,
    Rules,
    VerifierConfig,
    load_rules,
)
from tests.conftest import empty_rules, trivial_plan


# -------- Slice 003 --------


async def test_blacklist_blocks_approved_action(make_server, tmp_path):
    rules = empty_rules(blacklist=[{"transport": "gmail", "action": "archive"}])
    fixture = await make_server(
        messages=[{"id": "m1", "from": "alice@example.com", "subject": "hello", "body": "body", "unread": True}],
        rules=rules,
    )
    async with fixture.client.get("/card") as r:
        assert r.status == 200
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        assert r.status == 403
        body = await r.json()
    assert body["error"] == "blocked"
    assert "gmail" in body["reason"]
    entry = json.loads(Path(fixture.journal_path).read_text().strip().split("\n")[0])
    assert entry["kind"] == "blocked"


async def test_redaction_rules_strip_before_planner(make_server, tmp_path):
    rules = empty_rules(redaction=[{"field": "subject", "pattern": r"\d{4}-\d{4}"}])
    planner_input = {}

    async def capture_plan(input_data):
        planner_input.update(input_data)
        return plan_goal(input_data["message"])

    fixture = await make_server(
        messages=[{
            "id": "m1", "from": "alice@bank.com",
            "subject": "Your card 1234-5678 statement", "body": "sensitive", "unread": True,
        }],
        rules=rules,
        plan=capture_plan,
    )
    async with fixture.client.get("/card") as r:
        await r.json()
    assert planner_input["message"]["subject"] == "Your card [REDACTED] statement"
    assert "1234" not in planner_input["message"]["subject"]


def test_load_rules_from_principles_md(tmp_path):
    (tmp_path / "principles.md").write_text(
        "blacklist:\n  - transport: gmail\n    action: send\nredaction:\n  - field: subject\n"
    )
    rules = load_rules(tmp_path)
    assert rules.blacklist == [{"transport": "gmail", "action": "send"}]
    assert rules.redaction == [{"field": "subject"}]


# -------- Slice 004 --------


async def test_triage_runs_before_planner(make_server, tmp_path):
    triage_called = {}
    planner_called = {}

    async def triage_fn(msg):
        triage_called["from"] = msg["from"]
        triage_called["subject"] = msg["subject"]
        from steward.triage import TriageResult
        return TriageResult(
            features={
                "deadline": "2026-04-11",
                "amount": None,
                "waiting_on_user": True,
                "category": "work",
                "urgency": "high",
            },
            snippet="Boss requesting Q2 report by Friday.",
        )

    async def plan_fn(input_data):
        planner_called.update(input_data)
        from steward.planner import Goal
        return Goal(
            id=f"g-{input_data['message']['id']}",
            title="Send Q2 report to boss",
            reason="Q2 report due Friday, boss is waiting for it.",
            messageId=input_data["message"]["id"],
            transport="gmail",
            action="reply",
        )

    fixture = await make_server(
        messages=[{
            "id": "m1", "from": "boss@work.com",
            "subject": "Q2 report due Friday",
            "body": "Please send the Q2 report by end of day Friday.",
            "unread": True,
        }],
        triage=triage_fn,
        plan=plan_fn,
    )
    async with fixture.client.get("/card") as r:
        assert r.status == 200
        goal = await r.json()

    assert triage_called["from"] == "boss@work.com"
    assert triage_called["subject"] == "Q2 report due Friday"
    assert planner_called["message"]["fromDomain"] == "work.com"
    assert "body" not in planner_called["message"]
    assert planner_called["features"]["deadline"] == "2026-04-11"
    assert planner_called["features"]["waiting_on_user"] is True
    assert planner_called["features"]["urgency"] == "high"
    assert planner_called["snippet"] == "Boss requesting Q2 report by Friday."
    assert goal["title"] == "Send Q2 report to boss"
    assert "Q2 report" in goal["reason"]


async def test_redactor_between_triage_and_planner(make_server, tmp_path):
    rules = empty_rules(redaction=[{"field": "subject", "pattern": r"\d{8}"}])
    captured = {}

    async def triage_fn(_msg):
        from steward.triage import TriageResult
        return TriageResult(
            features={
                "deadline": None, "amount": None, "waiting_on_user": False,
                "category": "transaction", "urgency": "low",
            },
            snippet="Bank statement available.",
        )

    async def plan_fn(input_data):
        captured.update(input_data)
        return plan_goal(input_data["message"])

    fixture = await make_server(
        messages=[{
            "id": "m1", "from": "bank@hsbc.com",
            "subject": "Account 12345678 statement ready",
            "body": "Your statement for account 12345678 is ready.", "unread": True,
        }],
        rules=rules,
        triage=triage_fn,
        plan=plan_fn,
    )
    async with fixture.client.get("/card") as r:
        await r.json()
    assert captured["message"]["subject"] == "Account [REDACTED] statement ready"
    assert "12345678" not in captured["message"]["subject"]
    assert captured["features"]["category"] == "transaction"


def test_fake_gmail_search_returns_unread():
    from steward.gmail.fake import FakeGmail
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        g = FakeGmail(Path(d) / "inbox.json")
        g.save([
            {"id": "m1", "from": "a@test.com", "subject": "msg1", "body": "b1", "unread": True},
            {"id": "m2", "from": "b@test.com", "subject": "msg2", "body": "b2", "unread": True},
            {"id": "m3", "from": "c@test.com", "subject": "msg3", "body": "b3", "unread": False},
        ])
        results = g.search("is:unread")
    assert len(results) == 2
    assert [r["id"] for r in results] == ["m1", "m2"]


async def test_falls_back_to_default_triage(make_server, tmp_path):
    captured = {}

    async def plan_fn(input_data):
        captured.update(input_data)
        return plan_goal(input_data["message"])

    fixture = await make_server(
        messages=[{"id": "m1", "from": "a@test.com", "subject": "test", "body": "body", "unread": True}],
        plan=plan_fn,
    )
    async with fixture.client.get("/card") as r:
        await r.json()
    assert captured["features"]["category"] == "other"
    assert captured["features"]["urgency"] == "low"
    assert captured["snippet"] == "No triage available."
