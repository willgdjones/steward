"""Replay harness — re-run the planner against historical journal entries.

Used to validate rule changes and planner/model upgrades before rolling them
forward. For each replayable journal entry (one that carries the planner input
snapshot), re-runs the supplied planner against that snapshot and compares the
new goal's (transport, action) pair against the historical one.

A divergence means: a new config would have chosen differently for the same
inputs. Whether that's good or bad is a judgment call — the harness only
surfaces the set of divergences; the operator decides.

Free-form fields (title, reason) are intentionally *not* compared — small
wording changes from LLM variance aren't real behavior changes. Only
structured fields count.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from steward.journal import read_journal
from steward.planner import Goal, PlannerInput, plan_goal
from steward.redactor import apply_redaction_rules
from steward.rules import Rules, load_rules

PlanFn = Callable[[PlannerInput], Awaitable[Goal]]


@dataclass
class ReplayResult:
    entry: dict[str, Any]
    historical: dict[str, Any]
    new: dict[str, Any]
    diverged: bool
    reason: str | None = None


def _is_replayable(entry: dict[str, Any]) -> bool:
    return (
        entry.get("kind") in ("decision", "action")
        and isinstance(entry.get("features"), dict)
        and isinstance(entry.get("redactedMessage"), dict)
    )


def _historical_slots(entry: dict[str, Any]) -> dict[str, Any]:
    """Pull (transport, action, title) from a journal entry. Action entries
    record them at the top level; decision entries may or may not."""
    return {
        "transport": entry.get("transport"),
        "action": entry.get("action"),
        "title": entry.get("title"),
    }


async def replay_entry(
    entry: dict[str, Any],
    plan: PlanFn,
    rules: Rules,
) -> ReplayResult | None:
    """Re-run the planner on a single journal entry's inputs. Returns None if
    the entry doesn't carry enough context (missing redactedMessage / features)."""
    if not _is_replayable(entry):
        return None

    redacted_snapshot: dict[str, Any] = dict(entry["redactedMessage"])
    # Apply the current redaction rules on top of the historical redaction.
    # If rules haven't changed this is a no-op; if they tightened, the new
    # planner sees a more-redacted input, which is the right semantics.
    redacted = apply_redaction_rules(redacted_snapshot, rules.redaction)

    planner_input: PlannerInput = {
        "message": redacted,  # type: ignore[typeddict-item]
        "features": entry["features"],
        "snippet": entry.get("snippet", ""),
    }

    new_goal = await plan(planner_input)
    new_dict: dict[str, Any] = new_goal.to_dict() if hasattr(new_goal, "to_dict") else dict(new_goal)

    historical = _historical_slots(entry)
    new_slots = {
        "transport": new_dict.get("transport"),
        "action": new_dict.get("action"),
        "title": new_dict.get("title"),
    }

    reason: str | None = None
    diverged = False
    if historical["transport"] != new_slots["transport"]:
        diverged = True
        reason = f"transport: {historical['transport']} → {new_slots['transport']}"
    elif historical["action"] != new_slots["action"]:
        diverged = True
        reason = f"action: {historical['action']} → {new_slots['action']}"

    return ReplayResult(
        entry=entry,
        historical=historical,
        new=new_slots,
        diverged=diverged,
        reason=reason,
    )


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    s = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _entry_ts(entry: dict[str, Any]) -> datetime | None:
    ts = entry.get("ts")
    if not isinstance(ts, str):
        return None
    try:
        s = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


async def replay_journal(
    journal_path: str | Path,
    plan: PlanFn,
    rules: Rules,
    since: datetime | None = None,
) -> list[ReplayResult]:
    entries = read_journal(journal_path)
    if since is not None:
        entries = [e for e in entries if (_entry_ts(e) or datetime.min.replace(tzinfo=timezone.utc)) >= since]
    results: list[ReplayResult] = []
    for entry in entries:
        r = await replay_entry(entry, plan, rules)
        if r is not None:
            results.append(r)
    return results


def format_report(results: list[ReplayResult]) -> str:
    """Produce the CLI-friendly diff report."""
    total = len(results)
    divergent = [r for r in results if r.diverged]
    lines: list[str] = []
    lines.append(f"Replayed {total} entries, {len(divergent)} divergent ({100 * len(divergent) / total:.1f}%)" if total else "No replayable entries found.")
    lines.append("")
    for r in divergent:
        ts = r.entry.get("ts", "?")
        mid = r.entry.get("messageId", "?")
        lines.append(f"DIVERGENCE  ts={ts}  messageId={mid}")
        lines.append(f"  reason:     {r.reason}")
        lines.append(f"  historical: transport={r.historical['transport']!r}  action={r.historical['action']!r}  title={r.historical['title']!r}")
        lines.append(f"  new:        transport={r.new['transport']!r}  action={r.new['action']!r}  title={r.new['title']!r}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="steward.replay",
        description="Replay the decision journal against a new rules/planner config",
    )
    parser.add_argument(
        "--journal",
        default="state/journal.jsonl",
        help="Path to the JSONL journal file (default: state/journal.jsonl)",
    )
    parser.add_argument(
        "--rules-dir",
        default="state",
        help="Directory containing principles.md (default: state)",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="Only replay entries at or after this ISO timestamp",
    )
    args = parser.parse_args()

    rules = load_rules(args.rules_dir)
    since = _parse_since(args.since)

    async def trivial_plan(input_: PlannerInput) -> Goal:
        return plan_goal(input_["message"])

    results = asyncio.run(replay_journal(args.journal, trivial_plan, rules, since=since))
    print(format_report(results))
    # Exit non-zero if any divergences — makes this usable in CI gates.
    sys.exit(1 if any(r.diverged for r in results) else 0)


if __name__ == "__main__":
    main()
