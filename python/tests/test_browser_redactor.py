from steward.browser.redactor import MIN_CRED_LEN, redact_browser_outcome, redact_string


def test_redact_exact_match_in_text_and_title():
    outcome = {
        "success": True,
        "action_taken": "browser_authenticated_read",
        "url": "https://bank.example/account",
        "pageTitle": "Welcome, alice@example.com",
        "textContent": "Logged in as alice@example.com with token SuperSecret99",
    }
    redacted = redact_browser_outcome(outcome, ["alice@example.com", "SuperSecret99"])
    assert redacted["pageTitle"] == "Welcome, [REDACTED]"
    assert "alice@example.com" not in redacted["textContent"]
    assert "SuperSecret99" not in redacted["textContent"]
    assert redacted["textContent"].count("[REDACTED]") == 2


def test_short_credentials_not_scrubbed():
    # A 3-char cred should be skipped — the word "abc" appears in ordinary prose
    # and would create too many false positives to be useful.
    result = redact_string("Your password is abc, that's all", ["abc"])
    assert result == "Your password is abc, that's all"
    assert MIN_CRED_LEN == 4


def test_multiple_credentials_all_scrubbed():
    text = "user=alice@example.com pass=SuperSecret99 token=abcd1234"
    result = redact_string(text, ["alice@example.com", "SuperSecret99", "abcd1234"])
    assert "alice@example.com" not in result
    assert "SuperSecret99" not in result
    assert "abcd1234" not in result
    assert result.count("[REDACTED]") == 3


def test_case_sensitive_first_pass():
    # Documented limitation: case variants are NOT scrubbed. This test documents
    # the behaviour — change it if we decide to normalize usernames.
    result = redact_string("Welcome PassWord1, your real password PASSWORD1", ["PassWord1"])
    assert "PassWord1" not in result
    assert "PASSWORD1" in result  # not scrubbed — different case


def test_missing_fields_noop():
    outcome = {"success": True, "action_taken": "browser_authenticated_read"}
    redacted = redact_browser_outcome(outcome, ["SuperSecret99"])
    assert redacted == outcome


def test_extracted_dict_string_values_scrubbed():
    outcome = {
        "success": True,
        "action_taken": "browser_authenticated_read",
        "extracted": {"username": "alice@example.com", "amount": "£250"},
    }
    redacted = redact_browser_outcome(outcome, ["alice@example.com"])
    assert redacted["extracted"]["username"] == "[REDACTED]"
    assert redacted["extracted"]["amount"] == "£250"


def test_error_field_also_scrubbed():
    # If the sub-agent returns an error message that reflects the credential,
    # it must also be scrubbed. This is a defense-in-depth property.
    outcome = {
        "success": False,
        "action_taken": "browser_authenticated_read",
        "error": "Form field #password rejected value 'SuperSecret99'",
    }
    redacted = redact_browser_outcome(outcome, ["SuperSecret99"])
    assert "SuperSecret99" not in redacted["error"]
    assert "[REDACTED]" in redacted["error"]


def test_url_field_scrubbed_query_param_leak():
    # Query-param token leaks are a real risk. The `url` field gets scrubbed too.
    outcome = {
        "success": True,
        "action_taken": "browser_authenticated_read",
        "url": "https://example.com/callback?token=abcd1234xyz",
    }
    redacted = redact_browser_outcome(outcome, ["abcd1234xyz"])
    assert "abcd1234xyz" not in redacted["url"]
