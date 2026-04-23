from steward.redactor import apply_redaction_rules, redact


def test_drops_body_and_reduces_from_to_domain():
    r = redact({
        "id": "m1",
        "from": "alice@example.com",
        "subject": "hi",
        "body": "secret",
        "unread": True,
    })
    assert r == {"id": "m1", "fromDomain": "example.com", "subject": "hi"}
    assert "body" not in r


def test_drops_a_named_field_entirely_when_no_pattern_given():
    rules = [{"field": "subject"}]
    result = apply_redaction_rules(
        {"id": "m1", "fromDomain": "example.com", "subject": "sensitive"},
        rules,
    )
    assert result["subject"] == "[REDACTED]"


def test_replaces_regex_matches_when_pattern_given():
    rules = [{"field": "subject", "pattern": r"\d{4}[- ]?\d{4}"}]
    result = apply_redaction_rules(
        {"id": "m1", "fromDomain": "example.com", "subject": "card 1234-5678 info"},
        rules,
    )
    assert result["subject"] == "card [REDACTED] info"


def test_applies_multiple_rules_in_order():
    rules = [
        {"field": "subject", "pattern": r"\d+"},
        {"field": "fromDomain"},
    ]
    result = apply_redaction_rules(
        {"id": "m1", "fromDomain": "bank.com", "subject": "invoice 42 payment"},
        rules,
    )
    assert result["subject"] == "invoice [REDACTED] payment"
    assert result["fromDomain"] == "[REDACTED]"


def test_empty_rules_is_identity():
    input_ = {"id": "m1", "fromDomain": "example.com", "subject": "hello"}
    assert apply_redaction_rules(input_, []) == input_


def test_ignores_rules_for_missing_fields():
    rules = [{"field": "nonexistent"}]
    input_ = {"id": "m1", "fromDomain": "example.com", "subject": "hello"}
    assert apply_redaction_rules(input_, rules) == input_
