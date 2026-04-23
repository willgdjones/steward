"""Slice 010 — learned ranker with exploration."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from steward.journal import append_journal
from steward.rules import FloorReservation, QueueConfig, ReversibilityDecl, load_rules
from steward.triage import TriageResult
from tests.conftest import empty_rules


def ranker_rules(exploration_slots=1, target_depth=5):
    qc = QueueConfig(target_depth=target_depth, low_water_mark=1, batch_threshold=999, exploration_slots=exploration_slots)
    r = empty_rules(queue=qc)
    r.reversibility = [ReversibilityDecl(action="archive", reversible=True)]
    return r


async def test_decision_journal_includes_features(make_server):
    fixture = await make_server(
        messages=[{"id": "m1", "from": "alice@example.com", "subject": "hello", "body": "body", "unread": True}],
        rules=ranker_rules(exploration_slots=0),
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "defer"}) as r:
        await r.json()
    lines = Path(fixture.journal_path).read_text().strip().split("\n")
    entry = json.loads(lines[0])
    assert entry["kind"] == "decision"
    assert entry["features"] is not None
    assert entry["features"]["urgency"] is not None


async def test_queue_exposes_breakdown(make_server):
    fixture = await make_server(
        messages=[{"id": "m1", "from": "alice@example.com", "subject": "hello", "body": "body", "unread": True}],
        rules=ranker_rules(exploration_slots=0),
    )
    async with fixture.client.post("/refill") as r:
        await r.json()
    async with fixture.client.get("/queue") as r:
        q = await r.json()
    assert q["depth"] >= 1
    card = q["cards"][0]
    assert card["breakdown"] is not None
    assert "total" in card["breakdown"]
    assert "urgency" in card["breakdown"]


async def test_exploration_slots_reserve_positions(make_server):
    fixture = await make_server(
        messages=[
            {"id": "m1", "from": "known@example.com", "subject": "msg1", "body": "body", "unread": True},
            {"id": "m2", "from": "known@example.com", "subject": "msg2", "body": "body", "unread": True},
            {"id": "m3", "from": "unknown@newdomain.com", "subject": "msg3", "body": "body", "unread": True},
        ],
        rules=ranker_rules(exploration_slots=1, target_depth=5),
    )
    for i in range(5):
        append_journal(fixture.journal_path, {
            "kind": "decision",
            "decision": "approve",
            "goalId": f"g-{i}",
            "messageId": "m1",
            "features": {
                "urgency": "low", "deadline": None, "amount": None,
                "waiting_on_user": False, "category": "other",
            },
        })
    async with fixture.client.post("/refill") as r:
        await r.json()
    async with fixture.client.get("/queue") as r:
        q = await r.json()
    exploration = [c for c in q["cards"] if c.get("exploration") is True]
    assert len(exploration) >= 1


async def test_learned_weights_affect_ranking(make_server):
    fixture = await make_server(
        messages=[
            {"id": "no-amount", "from": "alice@example.com", "subject": "no amount", "body": "body", "unread": True},
            {"id": "with-amount", "from": "bob@example.com", "subject": "has amount", "body": "body", "unread": True},
        ],
        rules=ranker_rules(exploration_slots=0, target_depth=5),
    )
    for i in range(20):
        append_journal(fixture.journal_path, {
            "kind": "decision",
            "decision": "approve",
            "goalId": f"g-{i}",
            "messageId": f"m-hist-{i}",
            "features": {
                "urgency": "low", "deadline": None, "amount": "£100",
                "waiting_on_user": False, "category": "transaction",
            },
        })

    async def triage_fn(msg):
        if msg["id"] == "with-amount":
            return TriageResult(
                features={"urgency": "low", "deadline": None, "amount": "£50",
                          "waiting_on_user": False, "category": "transaction"},
                snippet="has amount",
            )
        return TriageResult(
            features={"urgency": "medium", "deadline": None, "amount": None,
                      "waiting_on_user": False, "category": "other"},
            snippet="no amount",
        )

    # Rebuild with triage
    await fixture.close()
    fixture2 = await make_server.__wrapped__(
        None, None, None  # can't easily reuse; redo
    ) if False else None
    # Simpler: make a new fresh server
    from tests.conftest import start_server, trivial_plan
    from steward.executor.server import ServerDeps
    from steward.gmail.fake import FakeGmail
    from pathlib import Path as P

    # Reuse fixture.gmail & journal_path's tmp dir logic via fixture
    # Actually simpler: create a fresh fixture via make_server factory
    fresh = await make_server(
        messages=[
            {"id": "no-amount", "from": "alice@example.com", "subject": "no amount", "body": "body", "unread": True},
            {"id": "with-amount", "from": "bob@example.com", "subject": "has amount", "body": "body", "unread": True},
        ],
        rules=ranker_rules(exploration_slots=0, target_depth=5),
        triage=triage_fn,
    )
    # Copy the journal into the new fixture's path so learn_weights has data
    P(fresh.journal_path).write_text(P(fixture.journal_path).read_text())

    async with fresh.client.post("/refill") as r:
        await r.json()
    async with fresh.client.get("/queue") as r:
        q = await r.json()
    assert "with-amount" in q["cards"][0]["id"]


async def test_floor_reservations_with_learned_ranker(make_server):
    rules = ranker_rules(exploration_slots=0, target_depth=5)
    rules.floor = [FloorReservation(match={"deadline_within_hours": 72}, slots=1)]
    future = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat().replace("+00:00", "Z")

    async def triage_fn(msg):
        if msg["id"] == "m-deadline":
            return TriageResult(
                features={"urgency": "low", "deadline": future, "amount": None,
                          "waiting_on_user": False, "category": "work"},
                snippet="deadline item",
            )
        return TriageResult(
            features={"urgency": "high", "deadline": None, "amount": None,
                      "waiting_on_user": False, "category": "other"},
            snippet="high urgency",
        )

    fixture = await make_server(
        messages=[
            {"id": "m-high", "from": "alice@example.com", "subject": "urgent", "body": "body", "unread": True},
            {"id": "m-deadline", "from": "bob@example.com", "subject": "deadline", "body": "body", "unread": True},
        ],
        rules=rules,
        triage=triage_fn,
    )
    async with fixture.client.post("/refill") as r:
        await r.json()
    async with fixture.client.get("/queue") as r:
        q = await r.json()
    assert "m-deadline" in q["cards"][0]["id"]


def test_exploration_slots_config_parsed(tmp_path):
    (tmp_path / "principles.md").write_text(
        "queue:\n  target_depth: 5\n  low_water_mark: 2\n  batch_threshold: 3\n  exploration_slots: 2\n"
    )
    rules = load_rules(tmp_path)
    assert rules.queue.exploration_slots == 2
