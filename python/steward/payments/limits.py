"""Deterministic spending limits — second layer under the issuer's own card limits.

Enforced in the executor *before* resolving credentials or dispatching. If
the requested charge would push daily or weekly spend over the configured
cap, the dispatch is refused with 403 and journaled as `limit_exceeded`.

Amounts are always minor units (pence) — int, never float.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class LimitsResult:
    allowed: bool
    reason: str | None = None


def _charge_amount(entry: dict[str, Any]) -> int | None:
    """Pull the amount from an 'action' entry with action='charge'. Returns
    None if this entry doesn't represent a successful charge."""
    if entry.get("kind") != "action" or entry.get("action") != "charge":
        return None
    outcomes = entry.get("outcomes")
    if not isinstance(outcomes, list) or not outcomes:
        return None
    first = outcomes[0]
    if not isinstance(first, dict) or not first.get("success"):
        return None
    amount = first.get("amount_pence")
    if not isinstance(amount, int):
        return None
    return amount


def _entry_ts_ms(entry: dict[str, Any]) -> int | None:
    """Best-effort timestamp for an entry in ms since epoch."""
    outcomes = entry.get("outcomes")
    if isinstance(outcomes, list) and outcomes:
        ts = outcomes[0].get("ts_ms") if isinstance(outcomes[0], dict) else None
        if isinstance(ts, int):
            return ts
    # Fall back to the journal-level `ts` ISO string.
    from datetime import datetime
    ts = entry.get("ts")
    if isinstance(ts, str):
        try:
            s = ts.replace("Z", "+00:00")
            return int(datetime.fromisoformat(s).timestamp() * 1000)
        except ValueError:
            return None
    return None


def check_spending_limits(
    requested_amount_pence: int,
    journal_entries: list[dict[str, Any]],
    *,
    max_per_charge_pence: int | None = None,
    max_per_day_pence: int | None = None,
    max_per_week_pence: int | None = None,
    now_ms: int | None = None,
) -> LimitsResult:
    """Pure function — takes the full journal and a requested amount, returns
    allow/deny with a readable reason."""
    if requested_amount_pence <= 0:
        return LimitsResult(allowed=False, reason=f"invalid amount: {requested_amount_pence}")

    if max_per_charge_pence is not None and requested_amount_pence > max_per_charge_pence:
        return LimitsResult(
            allowed=False,
            reason=(
                f"per-charge limit exceeded: "
                f"requested {requested_amount_pence} pence > max {max_per_charge_pence} pence"
            ),
        )

    now = now_ms if now_ms is not None else int(time.time() * 1000)
    day_ago = now - 24 * 60 * 60 * 1000
    week_ago = now - 7 * 24 * 60 * 60 * 1000

    spent_day = 0
    spent_week = 0
    for e in journal_entries:
        amount = _charge_amount(e)
        if amount is None:
            continue
        ts = _entry_ts_ms(e) or 0
        if ts >= week_ago:
            spent_week += amount
        if ts >= day_ago:
            spent_day += amount

    projected_day = spent_day + requested_amount_pence
    projected_week = spent_week + requested_amount_pence

    if max_per_day_pence is not None and projected_day > max_per_day_pence:
        return LimitsResult(
            allowed=False,
            reason=(
                f"per-day limit exceeded: "
                f"would reach {projected_day} pence > max {max_per_day_pence} pence "
                f"(already spent {spent_day} in last 24h)"
            ),
        )

    if max_per_week_pence is not None and projected_week > max_per_week_pence:
        return LimitsResult(
            allowed=False,
            reason=(
                f"per-week limit exceeded: "
                f"would reach {projected_week} pence > max {max_per_week_pence} pence "
                f"(already spent {spent_week} in last 7d)"
            ),
        )

    return LimitsResult(allowed=True)
