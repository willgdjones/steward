"""Tests for the spending-limits enforcer."""
from __future__ import annotations

import time

from steward.payments.limits import check_spending_limits


def _charge_entry(amount_pence: int, ts_ms: int | None = None) -> dict:
    return {
        "kind": "action",
        "action": "charge",
        "outcomes": [{
            "success": True,
            "action_taken": "charge",
            "amount_pence": amount_pence,
            "ts_ms": ts_ms if ts_ms is not None else int(time.time() * 1000),
        }],
    }


def test_allows_when_no_limits_set():
    r = check_spending_limits(1_000_000, [])
    assert r.allowed is True


def test_rejects_negative_or_zero():
    r = check_spending_limits(0, [], max_per_charge_pence=1000)
    assert r.allowed is False


def test_per_charge_cap_rejects_over_limit():
    r = check_spending_limits(5001, [], max_per_charge_pence=5000)
    assert r.allowed is False
    assert "per-charge limit exceeded" in r.reason


def test_per_charge_cap_accepts_exactly_at_limit():
    r = check_spending_limits(5000, [], max_per_charge_pence=5000)
    assert r.allowed is True


def test_per_day_cap_aggregates_recent_charges():
    # Three prior charges of 3000 each, all within last 24h → 9000 already spent.
    # A new 2000 would push to 11000, which exceeds 10000 limit.
    now = int(time.time() * 1000)
    entries = [_charge_entry(3000, ts_ms=now - 1000) for _ in range(3)]
    r = check_spending_limits(2000, entries, max_per_day_pence=10000, now_ms=now)
    assert r.allowed is False
    assert "per-day limit exceeded" in r.reason
    assert "9000" in r.reason


def test_per_day_cap_ignores_old_charges():
    # A charge from 2 days ago doesn't count toward today's cap.
    now = int(time.time() * 1000)
    old = _charge_entry(50000, ts_ms=now - 2 * 24 * 60 * 60 * 1000)
    r = check_spending_limits(2000, [old], max_per_day_pence=10000, now_ms=now)
    assert r.allowed is True


def test_per_week_cap_aggregates():
    now = int(time.time() * 1000)
    day = 24 * 60 * 60 * 1000
    entries = [_charge_entry(20000, ts_ms=now - 2 * day) for _ in range(4)]  # 80k in last 2 days
    r = check_spending_limits(25000, entries, max_per_week_pence=100000, now_ms=now)
    assert r.allowed is False
    assert "per-week" in r.reason


def test_ignores_failed_charges_in_aggregate():
    now = int(time.time() * 1000)
    failed = {
        "kind": "action",
        "action": "charge",
        "outcomes": [{"success": False, "action_taken": "charge", "amount_pence": 5000}],
    }
    r = check_spending_limits(6000, [failed], max_per_day_pence=10000, now_ms=now)
    assert r.allowed is True


def test_ignores_non_charge_entries():
    now = int(time.time() * 1000)
    archive = {
        "kind": "action",
        "action": "archive",
        "outcomes": [{"success": True, "action_taken": "archive"}],
    }
    r = check_spending_limits(5000, [archive], max_per_day_pence=10000, now_ms=now)
    assert r.allowed is True


def test_iso_ts_fallback_for_older_entries():
    # Pre-slice-016 journal entries won't have ts_ms in the outcome; we fall
    # back to the journal-level ts string.
    now_ms = int(time.time() * 1000)
    from datetime import datetime, timezone
    old_iso = datetime.fromtimestamp((now_ms - 1000) / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    entry = {
        "kind": "action",
        "action": "charge",
        "ts": old_iso,
        "outcomes": [{"success": True, "action_taken": "charge", "amount_pence": 5000}],
    }
    r = check_spending_limits(6000, [entry], max_per_day_pence=10000, now_ms=now_ms)
    assert r.allowed is False  # 5000 + 6000 = 11000 > 10000
