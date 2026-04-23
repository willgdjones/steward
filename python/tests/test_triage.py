from steward.triage import default_triage_result


def test_default_triage_result_safe_defaults():
    r = default_triage_result()
    assert r.features["deadline"] is None
    assert r.features["amount"] is None
    assert r.features["waiting_on_user"] is False
    assert r.features["category"] == "other"
    assert r.features["urgency"] == "low"
    assert r.snippet == "No triage available."
