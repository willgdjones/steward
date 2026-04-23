"""Slices 008 (post-hoc verifier meta-cards) + 009 (rule promotion meta-cards)."""
import json
from pathlib import Path

from steward.journal import append_journal
from steward.rules import PromotionConfig, QueueConfig, ReversibilityDecl, Rules, load_rules
from tests.conftest import empty_rules


# -------- Slice 008 --------


async def test_verifier_run_detects_unarchived_inserts_meta_card(make_server, tmp_path):
    fixture = await make_server(
        messages=[
            {"id": "m1", "from": "news@sub.com", "subject": "Newsletter",
             "body": "", "unread": True, "archived": False},
        ],
    )
    append_journal(fixture.journal_path, {
        "kind": "action",
        "goalId": "g1",
        "messageId": "m1",
        "title": "Archive newsletter",
        "outcomes": [{"success": True, "action_taken": "archive", "messageId": "m1"}],
        "verification": {"verified": True, "sample": []},
    })
    async with fixture.client.post("/verifier/run") as r:
        assert r.status == 200
    async with fixture.client.get("/queue") as r:
        q = await r.json()
    assert q["depth"] == 1
    assert "unarchived" in q["cards"][0]["title"]
    assert "meta-" in q["cards"][0]["id"]
    # Idempotent
    async with fixture.client.post("/verifier/run") as r:
        await r.json()
    async with fixture.client.get("/queue") as r:
        q2 = await r.json()
    assert q2["depth"] == 1


async def test_verifier_detects_reply_after_archive(make_server):
    fixture = await make_server(
        messages=[
            {"id": "m1", "from": "alice@example.com", "subject": "Project update",
             "body": "", "unread": False, "archived": True},
            {"id": "m2", "from": "alice@example.com", "subject": "Re: Project update",
             "body": "follow-up", "unread": True},
        ],
    )
    append_journal(fixture.journal_path, {
        "kind": "action",
        "goalId": "g1",
        "messageId": "m1",
        "title": "Archive project update",
        "outcomes": [{"success": True, "action_taken": "archive", "messageId": "m1"}],
        "verification": {"verified": True, "sample": []},
    })
    async with fixture.client.post("/verifier/run") as r:
        await r.json()
    async with fixture.client.get("/queue") as r:
        q = await r.json()
    assert q["depth"] == 1
    assert "reply" in q["cards"][0]["title"]


async def test_activity_returns_action_entries(make_server):
    fixture = await make_server(
        messages=[{"id": "m1", "from": "a@b.com", "subject": "test", "body": "", "unread": True}],
    )
    append_journal(fixture.journal_path, {
        "kind": "action",
        "goalId": "g1",
        "messageId": "m1",
        "title": "Archive test",
        "outcomes": [{"success": True, "action_taken": "archive", "messageId": "m1"}],
        "verification": {"verified": True, "sample": []},
    })
    append_journal(fixture.journal_path, {"kind": "decision", "decision": "reject", "goalId": "g2", "messageId": "m2"})
    async with fixture.client.get("/activity") as r:
        assert r.status == 200
        body = await r.json()
    assert len(body["entries"]) == 1
    assert body["entries"][0]["kind"] == "action"
    assert body["entries"][0]["goalId"] == "g1"


async def test_activity_wrong_emits_meta_card(make_server):
    fixture = await make_server(
        messages=[{"id": "m1", "from": "a@b.com", "subject": "newsletter",
                   "body": "", "unread": False, "archived": True}],
    )
    append_journal(fixture.journal_path, {
        "kind": "action",
        "goalId": "g1",
        "messageId": "m1",
        "title": "Archive newsletter",
        "outcomes": [{"success": True, "action_taken": "archive", "messageId": "m1"}],
        "verification": {"verified": True, "sample": []},
    })
    async with fixture.client.post("/activity/g1/wrong") as r:
        assert r.status == 200
        body = await r.json()
    assert body["ok"] is True
    assert "meta-wrong-" in body["metaCardId"]
    async with fixture.client.get("/queue") as r:
        q = await r.json()
    assert q["depth"] == 1
    assert "wrong" in q["cards"][0]["title"]
    async with fixture.client.post("/activity/g1/wrong") as r:
        body2 = await r.json()
    assert body2.get("alreadyQueued") is True
    async with fixture.client.get("/queue") as r:
        q2 = await r.json()
    assert q2["depth"] == 1


def test_verifier_interval_parsed(tmp_path):
    (tmp_path / "principles.md").write_text("verifier:\n  interval_minutes: 30\n")
    rules = load_rules(tmp_path)
    assert rules.verifier.interval_minutes == 30


def test_verifier_interval_default(tmp_path):
    (tmp_path / "principles.md").write_text("blacklist: []\n")
    rules = load_rules(tmp_path)
    assert rules.verifier.interval_minutes == 60


# -------- Slice 009 --------


def promotion_rules(threshold=3):
    qc = QueueConfig(target_depth=10, low_water_mark=1, batch_threshold=999, exploration_slots=0)
    r = empty_rules(queue=qc)
    r.reversibility = [ReversibilityDecl(action="archive", reversible=True)]
    r.promotion = PromotionConfig(threshold=threshold, cooldown_minutes=1440, interval_minutes=120)
    return r


async def test_promoter_detects_pattern_surfaces_meta(make_server, tmp_path):
    fixture = await make_server(
        messages=[],
        rules=promotion_rules(threshold=3),
        rules_dir=str(tmp_path),
    )
    for i in range(3):
        append_journal(fixture.journal_path, {
            "kind": "action",
            "goalId": f"g{i}",
            "messageId": f"m{i}",
            "senderDomain": "substack.com",
            "action": "archive",
            "transport": "gmail",
            "title": "Archive newsletter",
        })
    async with fixture.client.post("/promoter/run") as r:
        assert r.status == 200
    async with fixture.client.get("/queue") as r:
        q = await r.json()
    assert q["depth"] == 1
    assert "meta-promote-" in q["cards"][0]["id"]
    assert "substack.com" in q["cards"][0]["title"]
    assert "3" in q["cards"][0]["reason"]


async def test_approving_promotion_writes_rule_to_gmail_md(make_server, tmp_path):
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    fixture = await make_server(
        messages=[],
        rules=promotion_rules(threshold=3),
        rules_dir=str(rules_dir),
    )
    for i in range(3):
        append_journal(fixture.journal_path, {
            "kind": "action",
            "goalId": f"g{i}",
            "messageId": f"m{i}",
            "senderDomain": "substack.com",
            "action": "archive",
            "transport": "gmail",
        })
    async with fixture.client.post("/promoter/run") as r:
        await r.json()
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        assert r.status == 200
    gmail_md = (rules_dir / "gmail.md").read_text()
    assert "substack.com" in gmail_md
    assert "archive" in gmail_md
    entries = [json.loads(line) for line in Path(fixture.journal_path).read_text().strip().split("\n")]
    promoted = next(e for e in entries if e.get("kind") == "rule_promoted")
    assert promoted["patternKey"] == "gmail::archive::substack.com"


async def test_rejecting_promotion_journals_and_prevents_reproposal(make_server, tmp_path):
    fixture = await make_server(
        messages=[],
        rules=promotion_rules(threshold=3),
        rules_dir=str(tmp_path),
    )
    for i in range(5):
        append_journal(fixture.journal_path, {
            "kind": "action",
            "goalId": f"g{i}",
            "messageId": f"m{i}",
            "senderDomain": "substack.com",
            "action": "archive",
            "transport": "gmail",
        })
    async with fixture.client.post("/promoter/run") as r:
        await r.json()
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "reject"}) as r:
        await r.json()
    entries = [json.loads(line) for line in Path(fixture.journal_path).read_text().strip().split("\n")]
    rejected = next(e for e in entries if e.get("kind") == "promotion_rejected")
    assert rejected["patternKey"] == "gmail::archive::substack.com"
    async with fixture.client.post("/promoter/run") as r:
        await r.json()
    async with fixture.client.get("/queue") as r:
        q = await r.json()
    assert q["depth"] == 0


async def test_promoter_does_not_re_propose(make_server, tmp_path):
    fixture = await make_server(
        messages=[],
        rules=promotion_rules(threshold=3),
        rules_dir=str(tmp_path),
    )
    for i in range(5):
        append_journal(fixture.journal_path, {
            "kind": "action",
            "goalId": f"g{i}",
            "messageId": f"m{i}",
            "senderDomain": "substack.com",
            "action": "archive",
            "transport": "gmail",
        })
    async with fixture.client.post("/promoter/run") as r:
        await r.json()
    async with fixture.client.get("/card") as r:
        goal = await r.json()
    async with fixture.client.post(f"/card/{goal['id']}/decision", json={"decision": "approve"}) as r:
        await r.json()
    async with fixture.client.post("/promoter/run") as r:
        await r.json()
    async with fixture.client.get("/queue") as r:
        q = await r.json()
    assert q["depth"] == 0


def test_promotion_config_parsed(tmp_path):
    (tmp_path / "principles.md").write_text(
        "promotion:\n  threshold: 10\n  cooldown_minutes: 720\n  interval_minutes: 60\n"
    )
    rules = load_rules(tmp_path)
    assert rules.promotion.threshold == 10
    assert rules.promotion.cooldown_minutes == 720
    assert rules.promotion.interval_minutes == 60


def test_promotion_config_defaults(tmp_path):
    (tmp_path / "principles.md").write_text("blacklist: []\n")
    rules = load_rules(tmp_path)
    assert rules.promotion.threshold == 5
    assert rules.promotion.cooldown_minutes == 1440
    assert rules.promotion.interval_minutes == 120
