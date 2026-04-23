"""Learned-over-features ranker with deterministic floor and exploration slots."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TypedDict

from steward.rules import FloorReservation
from steward.triage import TriageFeatures


class FeatureVector(TypedDict):
    deadline_proximity: float
    has_amount: float
    waiting_on_user: float
    urgency: float


FEATURE_KEYS: tuple[str, ...] = (
    "deadline_proximity",
    "has_amount",
    "waiting_on_user",
    "urgency",
)

WEIGHT_BOUNDS: dict[str, tuple[float, float]] = {
    "deadline_proximity": (0.1, 3.0),
    "has_amount": (0.1, 3.0),
    "waiting_on_user": (0.1, 3.0),
    "urgency": (0.2, 3.0),
}

DEFAULT_WEIGHTS: dict[str, float] = {
    "deadline_proximity": 1.0,
    "has_amount": 0.5,
    "waiting_on_user": 0.8,
    "urgency": 1.0,
}


class ScoreBreakdown(TypedDict):
    deadline_proximity: float
    has_amount: float
    waiting_on_user: float
    urgency: float
    total: float


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_deadline(deadline: str | None) -> datetime | None:
    if not deadline:
        return None
    try:
        s = deadline.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def extract_feature_vector(features: TriageFeatures, now: datetime | None = None) -> FeatureVector:
    n = now or _now()
    deadline_proximity = 0.0
    deadline_dt = _parse_deadline(features.get("deadline"))
    if deadline_dt is not None:
        hours_until = (deadline_dt - n).total_seconds() / 3600
        if 0 <= hours_until <= 168:
            deadline_proximity = max(0.0, 1.0 - hours_until / 168)

    urgency_map = {"high": 1.0, "medium": 0.5, "low": 0.0}
    return {
        "deadline_proximity": deadline_proximity,
        "has_amount": 1.0 if features.get("amount") is not None else 0.0,
        "waiting_on_user": 1.0 if features.get("waiting_on_user") else 0.0,
        "urgency": urgency_map.get(features.get("urgency", "low"), 0.0),
    }


def score_candidate(fv: FeatureVector, weights: dict[str, float]) -> float:
    return sum(fv[k] * weights[k] for k in FEATURE_KEYS)  # type: ignore[literal-required]


def compute_breakdown(fv: FeatureVector, weights: dict[str, float]) -> ScoreBreakdown:
    breakdown: dict[str, float] = {}
    total = 0.0
    for key in FEATURE_KEYS:
        c = fv[key] * weights[key]  # type: ignore[literal-required]
        breakdown[key] = c
        total += c
    breakdown["total"] = total
    return breakdown  # type: ignore[return-value]


def learn_weights(entries: list[dict[str, Any]]) -> dict[str, float]:
    weights = dict(DEFAULT_WEIGHTS)
    decisions = [
        e for e in entries
        if e.get("kind") == "decision"
        and e.get("decision") in ("approve", "reject", "defer")
        and e.get("features")
    ]
    if not decisions:
        return weights

    learning_rate = 0.05
    for entry in decisions:
        raw = entry["features"]
        tf: TriageFeatures = {
            "deadline": raw.get("deadline"),
            "amount": raw.get("amount"),
            "waiting_on_user": raw.get("waiting_on_user") is True,
            "category": raw.get("category") or "other",
            "urgency": raw.get("urgency") or "low",
        }
        fv = extract_feature_vector(tf)
        direction = 1 if entry["decision"] == "approve" else -1
        for key in FEATURE_KEYS:
            if fv[key] > 0:  # type: ignore[literal-required]
                weights[key] += learning_rate * direction * fv[key]  # type: ignore[literal-required]

    for key, (lo, hi) in WEIGHT_BOUNDS.items():
        weights[key] = max(lo, min(hi, weights[key]))
    return weights


@dataclass
class RankInput:
    messageId: str
    features: TriageFeatures


@dataclass
class RankedCandidate:
    messageId: str
    features: TriageFeatures
    score: int
    floor: bool
    exploration: bool = False
    breakdown: ScoreBreakdown | None = None


def matches_floor(
    features: TriageFeatures,
    match: dict[str, Any],
    now: datetime | None = None,
) -> bool:
    n = now or _now()
    if isinstance(match.get("deadline_within_hours"), (int, float)) and features.get("deadline"):
        deadline_dt = _parse_deadline(features["deadline"])
        if deadline_dt is None:
            return False
        hours_until = (deadline_dt - n).total_seconds() / 3600
        return 0 <= hours_until <= match["deadline_within_hours"]
    if isinstance(match.get("category"), str):
        return features.get("category") == match["category"]
    if isinstance(match.get("urgency"), str):
        return features.get("urgency") == match["urgency"]
    return False


@dataclass
class RankOptions:
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    exploration_slots: int = 0
    journal_entries: list[dict[str, Any]] = field(default_factory=list)


def rank_candidates(
    candidates: list[RankInput],
    floor: list[FloorReservation],
    target_depth: int,
    now: datetime | None = None,
    options: RankOptions | None = None,
) -> list[RankedCandidate]:
    opts = options or RankOptions()
    weights = opts.weights
    exploration_slots = opts.exploration_slots
    journal_entries = opts.journal_entries
    n = now or _now()

    result: list[RankedCandidate] = []
    used: set[str] = set()

    # Step 1: Fill floor-reserved slots
    for reservation in floor:
        filled = 0
        for candidate in candidates:
            if candidate.messageId in used:
                continue
            if filled >= reservation.slots:
                break
            if matches_floor(candidate.features, reservation.match, n):
                fv = extract_feature_vector(candidate.features, n)
                result.append(
                    RankedCandidate(
                        messageId=candidate.messageId,
                        features=candidate.features,
                        score=len(result),
                        floor=True,
                        breakdown=compute_breakdown(fv, weights),
                    )
                )
                used.add(candidate.messageId)
                filled += 1

    # Step 2: Exploration slots
    exploration: list[RankedCandidate] = []
    if exploration_slots > 0:
        seen_counts: dict[str, int] = {}
        for entry in journal_entries:
            mid = entry.get("messageId")
            if isinstance(mid, str):
                seen_counts[mid] = seen_counts.get(mid, 0) + 1

        unseen = sorted(
            (c for c in candidates if c.messageId not in used),
            key=lambda c: seen_counts.get(c.messageId, 0),
        )
        filled = 0
        for candidate in unseen:
            if filled >= exploration_slots:
                break
            if len(result) >= target_depth:
                break
            fv = extract_feature_vector(candidate.features, n)
            exploration.append(
                RankedCandidate(
                    messageId=candidate.messageId,
                    features=candidate.features,
                    score=len(result),
                    floor=False,
                    exploration=True,
                    breakdown=compute_breakdown(fv, weights),
                )
            )
            used.add(candidate.messageId)
            filled += 1

    # Step 3: Score remaining
    remaining = [c for c in candidates if c.messageId not in used]
    scored = []
    for c in remaining:
        fv = extract_feature_vector(c.features, n)
        scored.append((c, fv, score_candidate(fv, weights), compute_breakdown(fv, weights)))
    scored.sort(key=lambda t: t[2], reverse=True)

    for c, fv, total, breakdown in scored:
        if len(result) + len(exploration) >= target_depth:
            break
        result.append(
            RankedCandidate(
                messageId=c.messageId,
                features=c.features,
                score=len(result),
                floor=False,
                breakdown=breakdown,
            )
        )

    result.extend(exploration)
    return result[:target_depth]
