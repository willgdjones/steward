from steward.principles_gate import check_blacklist


def test_allows_when_blacklist_empty():
    r = check_blacklist([], "gmail", "archive")
    assert r.allowed is True


def test_blocks_matching_transport_action_pair():
    r = check_blacklist([{"transport": "gmail", "action": "send"}], "gmail", "send")
    assert r.allowed is False
    assert "Blacklisted" in r.reason


def test_case_insensitive_matching():
    r = check_blacklist([{"transport": "Gmail", "action": "Send"}], "gmail", "send")
    assert r.allowed is False


def test_allows_non_matching():
    r = check_blacklist([{"transport": "gmail", "action": "send"}], "gmail", "archive")
    assert r.allowed is True
