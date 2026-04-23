"""Slice 005 — queue with deterministic floor."""
from datetime import datetime, timedelta, timezone

from steward.rules import FloorReservation, QueueConfig, Rules
from steward.triage import TriageResult
from tests.conftest import empty_rules


def make_messages(count):
    return [
        {"id": f"m{i}", "from": f"user{i}@example.com", "subject": f"msg {i}",
         "body": f"body {i}", "unread": True}
        for i in range(count)
    ]


def queue_rules(**qc_overrides) -> Rules:
    qc = QueueConfig(target_depth=3, low_water_mark=1, batch_threshold=999, exploration_slots=0)
    for k, v in qc_overrides.items():
        setattr(qc, k, v)
    return empty_rules(queue=qc)


async def test_fills_queue_to_target_depth(make_server):
    fixture = await make_server(
        messages=make_messages(5),
        rules=queue_rules(target_depth=3, low_water_mark=1),
    )
    async with fixture.client.get("/card") as r:
        assert r.status == 200
    async with fixture.client.get("/queue") as r:
        q = await r.json()
    assert q["depth"] == 3


async def test_queue_never_exceeds_target_depth(make_server):
    fixture = await make_server(
        messages=make_messages(10),
        rules=queue_rules(target_depth=2, low_water_mark=1),
    )
    async with fixture.client.get("/card") as r:
        await r.json()
    async with fixture.client.get("/queue") as r:
        q = await r.json()
    assert q["depth"] <= 2


async def test_refills_when_below_low_water(make_server):
    fixture = await make_server(
        messages=make_messages(6),
        rules=queue_rules(target_depth=3, low_water_mark=2),
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.get("/queue") as r:
        q = await r.json()
    assert q["depth"] == 3
    # Approve two cards
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        await r.json()
    async with fixture.client.get("/card") as r:
        goal2 = await r.json()
    async with fixture.client.post(f"/card/{goal2['id']}/decision", json={"decision": "approve"}) as r:
        await r.json()
    async with fixture.client.get("/card") as r:
        await r.json()
    async with fixture.client.get("/queue") as r:
        q = await r.json()
    assert 2 <= q["depth"] <= 3


async def test_manual_refill(make_server, tmp_path):
    fixture = await make_server(
        messages=[],
        rules=queue_rules(target_depth=3, low_water_mark=3),
    )
    async with fixture.client.get("/card") as r:
        assert r.status == 204
    fixture.gmail.save(make_messages(5))
    async with fixture.client.post("/refill") as r:
        assert r.status == 200
        body = await r.json()
    assert body["ok"] is True
    assert body["depth"] == 3


async def test_urgent_senders_bypass_queue(make_server):
    rules = queue_rules(target_depth=3, low_water_mark=1)
    rules.urgent_senders = ["boss@important.com"]
    fixture = await make_server(
        messages=[
            {"id": "m0", "from": "normal@example.com", "subject": "normal", "body": "b", "unread": True},
            {"id": "m1", "from": "normal2@example.com", "subject": "normal2", "body": "b", "unread": True},
            {"id": "urgent", "from": "boss@important.com", "subject": "urgent", "body": "b", "unread": True},
            {"id": "m3", "from": "normal3@example.com", "subject": "normal3", "body": "b", "unread": True},
        ],
        rules=rules,
    )
    async with fixture.client.get("/card") as r:
        assert r.status == 200
        goal = await r.json()
    assert goal["messageId"] == "urgent"


async def test_floor_reservations_honoured(make_server):
    rules = queue_rules(target_depth=3, low_water_mark=1)
    rules.floor = [FloorReservation(match={"deadline_within_hours": 72}, slots=1)]
    future = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat().replace("+00:00", "Z")

    triage_map = {
        "high": TriageResult(
            features={"deadline": None, "amount": None, "waiting_on_user": False, "category": "work", "urgency": "high"},
            snippet="high urgency",
        ),
        "deadline": TriageResult(
            features={"deadline": future, "amount": None, "waiting_on_user": False, "category": "other", "urgency": "low"},
            snippet="has deadline",
        ),
        "low": TriageResult(
            features={"deadline": None, "amount": None, "waiting_on_user": False, "category": "other", "urgency": "low"},
            snippet="low urgency",
        ),
    }

    async def triage_fn(msg):
        return triage_map[msg["id"]]

    fixture = await make_server(
        messages=[
            {"id": "high", "from": "a@test.com", "subject": "high urgency", "body": "b", "unread": True},
            {"id": "deadline", "from": "b@test.com", "subject": "has deadline", "body": "b", "unread": True},
            {"id": "low", "from": "c@test.com", "subject": "low urgency", "body": "b", "unread": True},
        ],
        rules=rules,
        triage=triage_fn,
    )
    async with fixture.client.get("/card") as r:
        await r.json()
    async with fixture.client.get("/queue") as r:
        q = await r.json()
    assert q["depth"] == 3
    assert q["cards"][0]["messageId"] == "deadline"
    assert q["cards"][1]["messageId"] == "high"


async def test_queue_does_not_duplicate(make_server):
    fixture = await make_server(
        messages=make_messages(3),
        rules=queue_rules(target_depth=5, low_water_mark=3),
    )
    async with fixture.client.get("/card") as r:
        await r.json()
    async with fixture.client.get("/card") as r:
        await r.json()
    async with fixture.client.get("/queue") as r:
        q = await r.json()
    ids = [c["messageId"] for c in q["cards"]]
    assert len(set(ids)) == len(ids)
