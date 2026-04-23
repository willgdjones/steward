from steward.gmail.fake import FakeGmail
from steward.journal import append_journal
from steward.verifier import detect_anomalies


async def test_empty_journal(tmp_path):
    gmail = FakeGmail(tmp_path / "inbox.json")
    gmail.save([])
    journal_path = str(tmp_path / "journal.jsonl")
    append_journal(journal_path, {"kind": "decision", "decision": "reject", "goalId": "g1", "messageId": "m1"})
    assert await detect_anomalies(journal_path, gmail) == []


async def test_detects_unarchive(tmp_path):
    gmail = FakeGmail(tmp_path / "inbox.json")
    gmail.save([{"id": "m1", "from": "alice@example.com", "subject": "hello", "body": "b", "unread": True, "archived": False}])
    journal_path = str(tmp_path / "journal.jsonl")
    append_journal(journal_path, {
        "kind": "action",
        "goalId": "g1",
        "messageId": "m1",
        "title": "Archive newsletter",
        "outcomes": [{"success": True, "action_taken": "archive", "messageId": "m1"}],
        "verification": {"verified": True, "sample": []},
    })
    anomalies = await detect_anomalies(journal_path, gmail)
    assert len(anomalies) == 1
    assert anomalies[0].type == "unarchive"
    assert anomalies[0].messageId == "m1"
    assert anomalies[0].goalId == "g1"
    assert "unarchived" in anomalies[0].description


async def test_no_flag_for_still_archived(tmp_path):
    gmail = FakeGmail(tmp_path / "inbox.json")
    gmail.save([{"id": "m1", "from": "alice@example.com", "subject": "hello", "body": "b", "unread": False, "archived": True}])
    journal_path = str(tmp_path / "journal.jsonl")
    append_journal(journal_path, {
        "kind": "action",
        "goalId": "g1",
        "messageId": "m1",
        "title": "Archive",
        "outcomes": [{"success": True, "action_taken": "archive", "messageId": "m1"}],
        "verification": {"verified": True, "sample": []},
    })
    assert await detect_anomalies(journal_path, gmail) == []


async def test_detects_reply_after_archive(tmp_path):
    gmail = FakeGmail(tmp_path / "inbox.json")
    gmail.save([
        {"id": "m1", "from": "alice@example.com", "subject": "hello", "body": "b", "unread": False, "archived": True},
        {"id": "m2", "from": "alice@example.com", "subject": "Re: hello", "body": "follow-up", "unread": True},
    ])
    journal_path = str(tmp_path / "journal.jsonl")
    append_journal(journal_path, {
        "kind": "action",
        "goalId": "g1",
        "messageId": "m1",
        "title": "Archive newsletter from alice",
        "outcomes": [{"success": True, "action_taken": "archive", "messageId": "m1"}],
        "verification": {"verified": True, "sample": []},
    })
    anomalies = await detect_anomalies(journal_path, gmail)
    assert len(anomalies) == 1
    assert anomalies[0].type == "reply_after_archive"
    assert "reply" in anomalies[0].description


async def test_batch_checks_all_messages(tmp_path):
    gmail = FakeGmail(tmp_path / "inbox.json")
    gmail.save([
        {"id": "m1", "from": "news@sub.com", "subject": "Issue 1", "body": "", "unread": False, "archived": True},
        {"id": "m2", "from": "news@sub.com", "subject": "Issue 2", "body": "", "unread": True, "archived": False},
        {"id": "m3", "from": "news@sub.com", "subject": "Issue 3", "body": "", "unread": False, "archived": True},
    ])
    journal_path = str(tmp_path / "journal.jsonl")
    append_journal(journal_path, {
        "kind": "action",
        "goalId": "g-batch",
        "messageId": "m1",
        "messageIds": ["m1", "m2", "m3"],
        "batchSize": 3,
        "title": "Archive 3 newsletter from sub.com",
        "outcomes": [
            {"success": True, "action_taken": "archive", "messageId": "m1"},
            {"success": True, "action_taken": "archive", "messageId": "m2"},
            {"success": True, "action_taken": "archive", "messageId": "m3"},
        ],
        "verification": {"verified": True, "sample": []},
    })
    anomalies = await detect_anomalies(journal_path, gmail)
    assert len(anomalies) == 1
    assert anomalies[0].type == "unarchive"
    assert anomalies[0].messageId == "m2"
    assert anomalies[0].goalId == "g-batch"


async def test_dedup_already_reported(tmp_path):
    gmail = FakeGmail(tmp_path / "inbox.json")
    gmail.save([{"id": "m1", "from": "a@b.com", "subject": "x", "body": "", "unread": True, "archived": False}])
    journal_path = str(tmp_path / "journal.jsonl")
    append_journal(journal_path, {
        "kind": "action",
        "goalId": "g1",
        "messageId": "m1",
        "title": "Archive",
        "outcomes": [{"success": True, "action_taken": "archive", "messageId": "m1"}],
        "verification": {"verified": True, "sample": []},
    })
    append_journal(journal_path, {
        "kind": "verifier_anomaly",
        "goalId": "g1",
        "messageId": "m1",
        "anomalyType": "unarchive",
    })
    assert await detect_anomalies(journal_path, gmail) == []
