"""Rule promoter: scan the journal for repeat patterns and propose rules."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from steward.journal import read_journal
from steward.rules import PromotionConfig


@dataclass
class Promotion:
    patternKey: str
    senderDomain: str
    action: str
    transport: str
    count: int
    proposedRule: str


def _parse_ts(ts: str) -> float:
    try:
        s = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp() * 1000
    except Exception:
        return 0.0


def detect_promotions(journal_path: str, config: PromotionConfig) -> list[Promotion]:
    entries = read_journal(journal_path)
    now_ms = time.time() * 1000

    promoted: set[str] = set()
    rejection_times: dict[str, float] = {}

    for e in entries:
        if e.get("kind") == "rule_promoted" and isinstance(e.get("patternKey"), str):
            promoted.add(e["patternKey"])
        if e.get("kind") == "promotion_rejected" and isinstance(e.get("patternKey"), str):
            ts = _parse_ts(e.get("ts", ""))
            key = e["patternKey"]
            if key not in rejection_times or ts > rejection_times[key]:
                rejection_times[key] = ts

    counts: dict[str, dict] = {}
    for e in entries:
        if e.get("kind") != "action":
            continue
        transport = e["transport"] if isinstance(e.get("transport"), str) else "gmail"
        if isinstance(e.get("action"), str):
            action = e["action"]
        else:
            title = e.get("title", "")
            action = "archive" if isinstance(title, str) and title.lower().startswith("archive") else "unknown"
        sender_domain = e.get("senderDomain") if isinstance(e.get("senderDomain"), str) else None
        if not sender_domain:
            continue
        key = f"{transport}::{action}::{sender_domain}"
        if key in counts:
            counts[key]["count"] += 1
        else:
            counts[key] = {"count": 1, "senderDomain": sender_domain, "action": action, "transport": transport}

    promotions: list[Promotion] = []
    for key, data in counts.items():
        if data["count"] < config.threshold:
            continue
        if key in promoted:
            continue
        rejected_at = rejection_times.get(key)
        if rejected_at:
            cooldown_ms = config.cooldown_minutes * 60 * 1000
            if now_ms - rejected_at < cooldown_ms:
                continue
        proposed = f'- sender: "*@{data["senderDomain"]}"\n  action: {data["action"]}\n  auto: true'
        promotions.append(
            Promotion(
                patternKey=key,
                senderDomain=data["senderDomain"],
                action=data["action"],
                transport=data["transport"],
                count=data["count"],
                proposedRule=proposed,
            )
        )
    return promotions
