import json

from steward.journal import append_journal, read_journal


def test_appends_entry_as_jsonl_line_with_timestamp(tmp_path):
    path = tmp_path / "journal.jsonl"
    entry = append_journal(path, {"kind": "approve", "goalId": "g1"})
    assert "T" in entry["ts"]
    assert entry["ts"].endswith("Z")
    lines = path.read_text().strip().split("\n")
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["kind"] == "approve"
    assert parsed["goalId"] == "g1"


def test_appends_multiple_entries_without_overwriting(tmp_path):
    path = tmp_path / "journal.jsonl"
    append_journal(path, {"kind": "a"})
    append_journal(path, {"kind": "b"})
    lines = path.read_text().strip().split("\n")
    assert len(lines) == 2


def test_read_journal_returns_empty_for_missing_file(tmp_path):
    assert read_journal(tmp_path / "nope.jsonl") == []


def test_read_journal_parses_all_entries(tmp_path):
    path = tmp_path / "journal.jsonl"
    append_journal(path, {"kind": "a", "x": 1})
    append_journal(path, {"kind": "b", "x": 2})
    entries = read_journal(path)
    assert len(entries) == 2
    assert entries[0]["x"] == 1
    assert entries[1]["x"] == 2
