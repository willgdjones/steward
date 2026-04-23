import time

from steward.rules import load_rules, watch_rules


def test_loads_blacklist_and_redaction_from_principles(tmp_path):
    (tmp_path / "principles.md").write_text(
        """blacklist:
  - transport: gmail
    action: send

redaction:
  - field: body
  - field: subject
    pattern: "\\\\d{4}[- ]?\\\\d{4}"
"""
    )
    (tmp_path / "gmail.md").write_text("")
    rules = load_rules(tmp_path)
    assert rules.blacklist == [{"transport": "gmail", "action": "send"}]
    assert len(rules.redaction) == 2
    assert rules.redaction[0] == {"field": "body"}
    assert rules.redaction[1]["field"] == "subject"
    assert rules.redaction[1]["pattern"] == r"\d{4}[- ]?\d{4}"


def test_loads_queue_urgent_floor(tmp_path):
    (tmp_path / "principles.md").write_text(
        """queue:
  target_depth: 7
  low_water_mark: 3

urgent_senders:
  - boss@company.com
  - CEO@company.com

floor:
  - match:
      deadline_within_hours: 72
    slots: 2
  - match:
      category: work
    slots: 1
"""
    )
    rules = load_rules(tmp_path)
    assert rules.queue.target_depth == 7
    assert rules.queue.low_water_mark == 3
    assert rules.queue.batch_threshold == 3
    assert rules.queue.exploration_slots == 1
    assert rules.urgent_senders == ["boss@company.com", "ceo@company.com"]
    assert len(rules.floor) == 2
    assert rules.floor[0].match == {"deadline_within_hours": 72}
    assert rules.floor[0].slots == 2
    assert rules.floor[1].match == {"category": "work"}
    assert rules.floor[1].slots == 1


def test_empty_rules_when_files_missing(tmp_path):
    rules = load_rules(tmp_path)
    assert rules.blacklist == []
    assert rules.redaction == []
    assert rules.queue.target_depth == 5
    assert rules.queue.low_water_mark == 2
    assert rules.urgent_senders == []
    assert rules.floor == []


def test_empty_rules_when_files_empty(tmp_path):
    (tmp_path / "principles.md").write_text("")
    (tmp_path / "gmail.md").write_text("")
    rules = load_rules(tmp_path)
    assert rules.blacklist == []
    assert rules.redaction == []


def test_watch_reloads_on_change(tmp_path):
    (tmp_path / "principles.md").write_text("")
    (tmp_path / "gmail.md").write_text("")
    versions = []
    watcher = watch_rules(tmp_path, versions.append, poll_interval=0.1)
    try:
        time.sleep(0.2)
        (tmp_path / "principles.md").write_text(
            """blacklist:
  - transport: gmail
    action: delete
"""
        )
        for _ in range(30):
            if versions:
                break
            time.sleep(0.1)
        assert len(versions) >= 1
        assert versions[-1].blacklist == [{"transport": "gmail", "action": "delete"}]
    finally:
        watcher.stop()
