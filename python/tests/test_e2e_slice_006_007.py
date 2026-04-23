"""Slices 006 (archive via sub-agent) + 007 (batched action card)."""
import json
from pathlib import Path

from steward.rules import QueueConfig, ReversibilityDecl, load_rules
from tests.conftest import empty_rules


# -------- Slice 006 --------


def with_reversibility(*decls):
    r = empty_rules()
    r.reversibility = list(decls)
    return r


async def test_approving_archive_dispatches_and_journals(make_server, tmp_path):
    rules = with_reversibility(ReversibilityDecl(action="archive", reversible=True))
    fixture = await make_server(
        messages=[{
            "id": "m1", "from": "newsletter@substack.com",
            "subject": "Weekly digest", "body": "content", "unread": True,
        }],
        rules=rules,
    )
    async with fixture.client.get("/card") as r:
        assert r.status == 200
        goal = await r.json()
    assert goal["action"] == "archive"
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        assert r.status == 200
        body = await r.json()
    assert body["ok"] is True
    assert body["outcomes"][0]["success"] is True
    assert body["outcomes"][0]["action_taken"] == "archive"
    assert body["verification"]["verified"] is True
    msg = fixture.gmail.get_by_id("m1")
    assert msg["archived"] is True

    entries = [json.loads(line) for line in Path(fixture.journal_path).read_text().strip().split("\n")]
    action = next(e for e in entries if e["kind"] == "action")
    assert action["goalId"] == goal["id"]
    assert action["outcomes"][0]["success"] is True
    assert action["verification"]["verified"] is True


async def test_archived_messages_no_longer_in_queue(make_server):
    rules = with_reversibility(ReversibilityDecl(action="archive", reversible=True))
    fixture = await make_server(
        messages=[{"id": "m1", "from": "a@test.com", "subject": "msg1", "body": "b", "unread": True}],
        rules=rules,
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        await r.json()
    async with fixture.client.get("/card") as r:
        assert r.status == 204


async def test_verification_failure_journalled(make_server, tmp_path, monkeypatch):
    rules = with_reversibility(ReversibilityDecl(action="archive", reversible=True))
    fixture = await make_server(
        messages=[{"id": "m1", "from": "a@test.com", "subject": "msg1", "body": "b", "unread": True}],
        rules=rules,
    )
    # Stub archive so it returns True (message found) but doesn't persist
    fixture.gmail.archive = lambda mid: True
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        assert r.status == 200
        body = await r.json()
    assert body["verification"]["verified"] is False

    entries = [json.loads(line) for line in Path(fixture.journal_path).read_text().strip().split("\n")]
    action = next(e for e in entries if e["kind"] == "action")
    assert action["verification"]["verified"] is False


def test_reversibility_parsed_from_principles(tmp_path):
    (tmp_path / "principles.md").write_text(
        "reversibility:\n  - action: archive\n    reversible: true\n  - action: send\n    reversible: false\n"
    )
    rules = load_rules(tmp_path)
    assert rules.reversibility == [
        ReversibilityDecl(action="archive", reversible=True),
        ReversibilityDecl(action="send", reversible=False),
    ]


# -------- Slice 007 --------


def batch_rules(batch_threshold=3, target_depth=10):
    qc = QueueConfig(target_depth=target_depth, low_water_mark=1, batch_threshold=batch_threshold, exploration_slots=0)
    r = empty_rules(queue=qc)
    r.reversibility = [ReversibilityDecl(action="archive", reversible=True)]
    return r


async def test_clusters_similar_into_batched_card(make_server):
    fixture = await make_server(
        messages=[
            {"id": "m1", "from": "a@newsletters.com", "subject": "Newsletter 1", "body": "b", "unread": True},
            {"id": "m2", "from": "b@newsletters.com", "subject": "Newsletter 2", "body": "b", "unread": True},
            {"id": "m3", "from": "c@newsletters.com", "subject": "Newsletter 3", "body": "b", "unread": True},
            {"id": "m4", "from": "user@other.com", "subject": "Personal msg", "body": "b", "unread": True},
        ],
        rules=batch_rules(batch_threshold=3),
    )
    async with fixture.client.get("/card") as r:
        await r.json()
    async with fixture.client.get("/queue") as r:
        q = await r.json()
    assert q["depth"] == 2
    batch_card = next((c for c in q["cards"] if c.get("batchSize")), None)
    assert batch_card is not None
    assert batch_card["batchSize"] == 3
    assert len(batch_card["messageIds"]) == 3
    assert "newsletters.com" in batch_card["title"]
    assert "3" in batch_card["title"]
    single_card = next((c for c in q["cards"] if not c.get("batchSize")), None)
    assert single_card is not None


async def test_one_swipe_archives_batch(make_server):
    fixture = await make_server(
        messages=[
            {"id": f"m{i}", "from": f"x{i}@promo.com", "subject": f"Promo {i}", "body": "b", "unread": True}
            for i in range(1, 5)
        ],
        rules=batch_rules(batch_threshold=3),
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    assert goal["batchSize"] == 4
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        assert r.status == 200
        body = await r.json()
    assert body["ok"] is True
    assert body["batchSize"] == 4
    assert len(body["outcomes"]) == 4
    assert all(o["success"] for o in body["outcomes"])
    assert body["verification"]["verified"] is True
    for i in range(1, 5):
        m = fixture.gmail.get_by_id(f"m{i}")
        assert m["archived"] is True
    async with fixture.client.get("/card") as r:
        assert r.status == 204


async def test_journal_records_full_message_id_list(make_server):
    fixture = await make_server(
        messages=[
            {"id": f"m{i}", "from": f"x{i}@bulk.com", "subject": f"Bulk {i}", "body": "b", "unread": True}
            for i in range(1, 4)
        ],
        rules=batch_rules(batch_threshold=3),
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        await r.json()
    lines = Path(fixture.journal_path).read_text().strip().split("\n")
    entry = json.loads(lines[0])
    assert entry["kind"] == "action"
    assert entry["messageIds"] == ["m1", "m2", "m3"]
    assert entry["batchSize"] == 3
    assert len(entry["outcomes"]) == 3


async def test_verification_samples_first_middle_last(make_server):
    fixture = await make_server(
        messages=[
            {"id": f"m{i}", "from": f"x{i}@biglist.com", "subject": f"Item {i}", "body": "b", "unread": True}
            for i in range(7)
        ],
        rules=batch_rules(batch_threshold=3),
    )
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    assert goal["batchSize"] == 7
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        body = await r.json()
    assert len(body["verification"]["sample"]) == 3
    sampled = [v["messageId"] for v in body["verification"]["sample"]]
    assert "m0" in sampled
    assert "m3" in sampled
    assert "m6" in sampled


def test_batch_threshold_configurable(tmp_path):
    (tmp_path / "principles.md").write_text(
        "queue:\n  target_depth: 10\n  low_water_mark: 3\n  batch_threshold: 5\n"
    )
    rules = load_rules(tmp_path)
    assert rules.queue.batch_threshold == 5


async def test_below_threshold_produces_individual_cards(make_server):
    fixture = await make_server(
        messages=[
            {"id": "m1", "from": "a@news.com", "subject": "News 1", "body": "b", "unread": True},
            {"id": "m2", "from": "b@news.com", "subject": "News 2", "body": "b", "unread": True},
        ],
        rules=batch_rules(batch_threshold=3),
    )
    async with fixture.client.get("/card") as r:
        await r.json()
    async with fixture.client.get("/queue") as r:
        q = await r.json()
    assert q["depth"] == 2
    assert all(not c.get("batchSize") for c in q["cards"])
