import json
from datetime import datetime, timedelta, timezone

from steward.journal import append_journal
from steward.promoter import detect_promotions
from steward.rules import PromotionConfig


def config(**kw):
    base = {"threshold": 3, "cooldown_minutes": 1440, "interval_minutes": 120}
    base.update(kw)
    return PromotionConfig(**base)


def test_empty_when_no_actions(tmp_path):
    jp = tmp_path / "journal.jsonl"
    append_journal(jp, {"kind": "decision", "decision": "defer", "goalId": "g1", "messageId": "m1"})
    assert detect_promotions(str(jp), config()) == []


def test_proposes_when_threshold_reached(tmp_path):
    jp = tmp_path / "journal.jsonl"
    for i in range(3):
        append_journal(jp, {
            "kind": "action",
            "goalId": f"g{i}",
            "messageId": f"m{i}",
            "title": "Archive newsletter from substack.com",
            "senderDomain": "substack.com",
            "action": "archive",
            "transport": "gmail",
            "outcomes": [{"success": True, "action_taken": "archive", "messageId": f"m{i}"}],
            "verification": {"verified": True, "sample": []},
        })
    promotions = detect_promotions(str(jp), config())
    assert len(promotions) == 1
    p = promotions[0]
    assert p.patternKey == "gmail::archive::substack.com"
    assert p.senderDomain == "substack.com"
    assert p.action == "archive"
    assert p.transport == "gmail"
    assert p.count == 3
    assert "substack.com" in p.proposedRule
    assert "archive" in p.proposedRule


def test_below_threshold_no_proposal(tmp_path):
    jp = tmp_path / "journal.jsonl"
    for i in range(2):
        append_journal(jp, {
            "kind": "action",
            "goalId": f"g{i}",
            "messageId": f"m{i}",
            "senderDomain": "substack.com",
            "action": "archive",
            "transport": "gmail",
        })
    assert detect_promotions(str(jp), config()) == []


def test_skips_already_promoted(tmp_path):
    jp = tmp_path / "journal.jsonl"
    for i in range(5):
        append_journal(jp, {
            "kind": "action",
            "goalId": f"g{i}",
            "messageId": f"m{i}",
            "senderDomain": "substack.com",
            "action": "archive",
            "transport": "gmail",
        })
    append_journal(jp, {"kind": "rule_promoted", "patternKey": "gmail::archive::substack.com"})
    assert detect_promotions(str(jp), config()) == []


def test_respects_cooldown(tmp_path):
    jp = tmp_path / "journal.jsonl"
    for i in range(5):
        append_journal(jp, {
            "kind": "action",
            "goalId": f"g{i}",
            "messageId": f"m{i}",
            "senderDomain": "substack.com",
            "action": "archive",
            "transport": "gmail",
        })
    append_journal(jp, {
        "kind": "promotion_rejected",
        "patternKey": "gmail::archive::substack.com",
    })
    assert detect_promotions(str(jp), config()) == []


def test_re_proposes_after_cooldown(tmp_path):
    jp = tmp_path / "journal.jsonl"
    for i in range(5):
        append_journal(jp, {
            "kind": "action",
            "goalId": f"g{i}",
            "messageId": f"m{i}",
            "senderDomain": "substack.com",
            "action": "archive",
            "transport": "gmail",
        })
    append_journal(jp, {
        "kind": "promotion_rejected",
        "patternKey": "gmail::archive::substack.com",
    })
    # Patch last line's ts to old
    lines = jp.read_text().strip().split("\n")
    last = json.loads(lines[-1])
    last["ts"] = (datetime.now(timezone.utc) - timedelta(minutes=1441)).isoformat().replace("+00:00", "Z")
    lines[-1] = json.dumps(last)
    jp.write_text("\n".join(lines) + "\n")

    promotions = detect_promotions(str(jp), config())
    assert len(promotions) == 1
    assert promotions[0].patternKey == "gmail::archive::substack.com"


def test_groups_different_domains_independently(tmp_path):
    jp = tmp_path / "journal.jsonl"
    for i in range(3):
        append_journal(jp, {
            "kind": "action",
            "goalId": f"gs{i}",
            "messageId": f"ms{i}",
            "senderDomain": "substack.com",
            "action": "archive",
            "transport": "gmail",
        })
        append_journal(jp, {
            "kind": "action",
            "goalId": f"gm{i}",
            "messageId": f"mm{i}",
            "senderDomain": "medium.com",
            "action": "archive",
            "transport": "gmail",
        })
    promotions = detect_promotions(str(jp), config())
    assert len(promotions) == 2
    keys = sorted(p.patternKey for p in promotions)
    assert keys == ["gmail::archive::medium.com", "gmail::archive::substack.com"]
