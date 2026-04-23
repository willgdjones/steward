from datetime import datetime, timezone

from steward.ranker import (
    DEFAULT_WEIGHTS,
    WEIGHT_BOUNDS,
    RankInput,
    RankOptions,
    extract_feature_vector,
    learn_weights,
    matches_floor,
    rank_candidates,
    score_candidate,
)
from steward.rules import FloorReservation


def features(**overrides):
    base = {
        "deadline": None,
        "amount": None,
        "waiting_on_user": False,
        "category": "other",
        "urgency": "low",
    }
    base.update(overrides)
    return base


def test_matches_deadline_within_hours_when_in_range():
    now = datetime(2026, 4, 9, 12, 0, 0, tzinfo=timezone.utc)
    f = features(deadline="2026-04-10T12:00:00Z")
    assert matches_floor(f, {"deadline_within_hours": 72}, now) is True


def test_does_not_match_deadline_out_of_range():
    now = datetime(2026, 4, 9, 12, 0, 0, tzinfo=timezone.utc)
    f = features(deadline="2026-04-20T12:00:00Z")
    assert matches_floor(f, {"deadline_within_hours": 72}, now) is False


def test_does_not_match_when_no_deadline():
    f = features(deadline=None)
    assert matches_floor(f, {"deadline_within_hours": 72}) is False


def test_matches_category_and_urgency():
    assert matches_floor(features(category="work"), {"category": "work"}) is True
    assert matches_floor(features(category="work"), {"category": "newsletter"}) is False
    assert matches_floor(features(urgency="high"), {"urgency": "high"}) is True
    assert matches_floor(features(urgency="high"), {"urgency": "low"}) is False


def test_feature_vector_urgency():
    assert extract_feature_vector(features(urgency="high"))["urgency"] == 1.0
    assert extract_feature_vector(features(urgency="medium"))["urgency"] == 0.5
    assert extract_feature_vector(features(urgency="low"))["urgency"] == 0.0


def test_feature_vector_deadline_proximity():
    now = datetime(2026, 4, 9, 12, 0, 0, tzinfo=timezone.utc)
    v = extract_feature_vector(features(deadline="2026-04-10T00:00:00Z"), now)
    assert v["deadline_proximity"] > 0.5
    assert extract_feature_vector(features(deadline=None), now)["deadline_proximity"] == 0


def test_feature_vector_has_amount_and_waiting_on_user():
    assert extract_feature_vector(features(amount="£50"))["has_amount"] == 1
    assert extract_feature_vector(features(amount=None))["has_amount"] == 0
    assert extract_feature_vector(features(waiting_on_user=True))["waiting_on_user"] == 1
    assert extract_feature_vector(features(waiting_on_user=False))["waiting_on_user"] == 0


def test_score_candidate_higher_for_high_urgency():
    high = score_candidate(extract_feature_vector(features(urgency="high")), DEFAULT_WEIGHTS)
    low = score_candidate(extract_feature_vector(features(urgency="low")), DEFAULT_WEIGHTS)
    assert high > low


def test_learn_weights_empty_returns_default():
    assert learn_weights([]) == DEFAULT_WEIGHTS


def test_learn_weights_approvals_increase_weight():
    entries = [
        {
            "ts": "2026-04-09T12:00:00Z",
            "kind": "decision",
            "decision": "approve",
            "goalId": f"g-{i}",
            "messageId": f"m-{i}",
            "features": {
                "urgency": "high",
                "deadline": None,
                "amount": None,
                "waiting_on_user": False,
                "category": "work",
            },
        }
        for i in range(10)
    ]
    weights = learn_weights(entries)
    assert weights["urgency"] > DEFAULT_WEIGHTS["urgency"]


def test_learn_weights_rejects_decrease_weight():
    entries = [
        {
            "ts": "2026-04-09T12:00:00Z",
            "kind": "decision",
            "decision": "reject",
            "goalId": f"g-{i}",
            "messageId": f"m-{i}",
            "features": {
                "urgency": "high",
                "deadline": None,
                "amount": None,
                "waiting_on_user": False,
                "category": "work",
            },
        }
        for i in range(10)
    ]
    weights = learn_weights(entries)
    assert weights["urgency"] < DEFAULT_WEIGHTS["urgency"]


def test_learn_weights_clamped():
    entries = [
        {
            "ts": "2026-04-09T12:00:00Z",
            "kind": "decision",
            "decision": "approve",
            "goalId": f"g-{i}",
            "messageId": f"m-{i}",
            "features": {
                "urgency": "high",
                "deadline": "2026-04-10T00:00:00Z",
                "amount": "£500",
                "waiting_on_user": True,
                "category": "work",
            },
        }
        for i in range(100)
    ]
    weights = learn_weights(entries)
    for key, (lo, hi) in WEIGHT_BOUNDS.items():
        assert lo <= weights[key] <= hi


def test_learn_weights_ignores_non_decision():
    entries = [
        {"ts": "2026-04-09T12:00:00Z", "kind": "action", "goalId": "g1", "messageId": "m1"},
        {"ts": "2026-04-09T12:00:00Z", "kind": "verifier_anomaly", "goalId": "g2", "messageId": "m2"},
    ]
    assert learn_weights(entries) == DEFAULT_WEIGHTS


def test_rank_caps_at_target_depth():
    candidates = [RankInput(messageId=f"m{i}", features=features()) for i in range(10)]
    assert len(rank_candidates(candidates, [], 5)) == 5


def test_rank_sorts_by_urgency():
    candidates = [
        RankInput(messageId="low1", features=features(urgency="low")),
        RankInput(messageId="high1", features=features(urgency="high")),
        RankInput(messageId="med1", features=features(urgency="medium")),
    ]
    result = rank_candidates(candidates, [], 5)
    assert [r.messageId for r in result] == ["high1", "med1", "low1"]


def test_rank_honours_floor_before_tiebreaker():
    now = datetime(2026, 4, 9, 12, 0, 0, tzinfo=timezone.utc)
    candidates = [
        RankInput(messageId="high-no-deadline", features=features(urgency="high")),
        RankInput(messageId="low-with-deadline", features=features(urgency="low", deadline="2026-04-10T00:00:00Z")),
        RankInput(messageId="med-no-deadline", features=features(urgency="medium")),
    ]
    floor = [FloorReservation(match={"deadline_within_hours": 72}, slots=1)]
    result = rank_candidates(candidates, floor, 3, now)
    assert result[0].messageId == "low-with-deadline"
    assert result[0].floor is True
    assert result[1].messageId == "high-no-deadline"
    assert result[1].floor is False
    assert result[2].messageId == "med-no-deadline"


def test_rank_floor_cannot_exceed_target_depth():
    now = datetime(2026, 4, 9, 12, 0, 0, tzinfo=timezone.utc)
    candidates = [
        RankInput(messageId=f"m{i}", features=features(deadline="2026-04-10T00:00:00Z"))
        for i in range(5)
    ]
    floor = [FloorReservation(match={"deadline_within_hours": 72}, slots=10)]
    result = rank_candidates(candidates, floor, 3, now)
    assert len(result) == 3


def test_rank_returns_empty_when_no_candidates():
    assert rank_candidates([], [], 5) == []


def test_rank_multiple_floor_reservations():
    now = datetime(2026, 4, 9, 12, 0, 0, tzinfo=timezone.utc)
    candidates = [
        RankInput(messageId="work1", features=features(category="work", urgency="low")),
        RankInput(messageId="deadline1", features=features(deadline="2026-04-10T00:00:00Z", urgency="low")),
        RankInput(messageId="high1", features=features(urgency="high")),
    ]
    floor = [
        FloorReservation(match={"deadline_within_hours": 72}, slots=1),
        FloorReservation(match={"category": "work"}, slots=1),
    ]
    result = rank_candidates(candidates, floor, 5, now)
    assert result[0].messageId == "deadline1"
    assert result[0].floor is True
    assert result[1].messageId == "work1"
    assert result[1].floor is True
    assert result[2].messageId == "high1"
    assert result[2].floor is False


def test_rank_breakdown_attached():
    candidates = [RankInput(messageId="m1", features=features(urgency="high", amount="£50"))]
    result = rank_candidates(candidates, [], 5)
    assert result[0].breakdown is not None
    assert result[0].breakdown["urgency"] > 0
    assert result[0].breakdown["has_amount"] > 0


def test_rank_exploration_slot():
    candidates = [RankInput(messageId=f"m{i}", features=features(urgency="low")) for i in range(10)]
    result = rank_candidates(
        candidates,
        [],
        5,
        options=RankOptions(weights=DEFAULT_WEIGHTS, exploration_slots=1, journal_entries=[]),
    )
    assert len(result) == 5
    exploration = [r for r in result if r.exploration]
    assert len(exploration) == 1


def test_rank_exploration_picks_least_seen():
    candidates = [
        RankInput(messageId="seen-a-lot", features=features(urgency="high")),
        RankInput(messageId="never-seen", features=features(urgency="low")),
    ]
    journal_entries = [
        {"ts": "2026-04-09T12:00:00Z", "kind": "decision", "decision": "approve",
         "goalId": f"g-{i}", "messageId": "seen-a-lot"}
        for i in range(5)
    ]
    result = rank_candidates(
        candidates,
        [],
        5,
        options=RankOptions(weights=DEFAULT_WEIGHTS, exploration_slots=1, journal_entries=journal_entries),
    )
    exploration = [r for r in result if r.exploration]
    assert len(exploration) == 1
    assert exploration[0].messageId == "never-seen"


def test_rank_with_learned_weights():
    journal_entries = [
        {
            "ts": "2026-04-09T12:00:00Z",
            "kind": "decision",
            "decision": "approve",
            "goalId": f"g-{i}",
            "messageId": f"m-{i}",
            "features": {
                "urgency": "low",
                "deadline": None,
                "amount": "£100",
                "waiting_on_user": False,
                "category": "transaction",
            },
        }
        for i in range(20)
    ]
    weights = learn_weights(journal_entries)
    candidates = [
        RankInput(messageId="no-amount", features=features(urgency="medium")),
        RankInput(messageId="with-amount", features=features(urgency="low", amount="£50")),
    ]
    result = rank_candidates(
        candidates,
        [],
        5,
        options=RankOptions(weights=weights, exploration_slots=0, journal_entries=[]),
    )
    assert result[0].messageId == "with-amount"
