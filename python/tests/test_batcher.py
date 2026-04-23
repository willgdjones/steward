from steward.batcher import TriagedCandidate, cluster_candidates
from steward.triage import TriageResult


def make_candidate(id_: str, from_: str, category: str) -> TriagedCandidate:
    return TriagedCandidate(
        message={"id": id_, "from": from_, "subject": f"subj-{id_}", "body": "body", "unread": True},
        result=TriageResult(
            features={
                "deadline": None,
                "amount": None,
                "waiting_on_user": False,
                "category": category,
                "urgency": "low",
            },
            snippet=f"snippet for {id_}",
        ),
    )


def test_clusters_by_domain_category_above_threshold():
    candidates = [
        make_candidate("m1", "a@substack.com", "newsletter"),
        make_candidate("m2", "b@substack.com", "newsletter"),
        make_candidate("m3", "c@substack.com", "newsletter"),
        make_candidate("m4", "d@other.com", "personal"),
    ]
    batches, remaining = cluster_candidates(candidates, 3)
    assert len(batches) == 1
    assert batches[0].domain == "substack.com"
    assert batches[0].category == "newsletter"
    assert len(batches[0].candidates) == 3
    assert len(remaining) == 1
    assert remaining[0].message["id"] == "m4"


def test_does_not_batch_below_threshold():
    candidates = [
        make_candidate("m1", "a@substack.com", "newsletter"),
        make_candidate("m2", "b@substack.com", "newsletter"),
    ]
    batches, remaining = cluster_candidates(candidates, 3)
    assert len(batches) == 0
    assert len(remaining) == 2


def test_separates_different_categories_same_domain():
    candidates = [
        make_candidate("m1", "a@example.com", "newsletter"),
        make_candidate("m2", "b@example.com", "newsletter"),
        make_candidate("m3", "c@example.com", "newsletter"),
        make_candidate("m4", "d@example.com", "marketing"),
        make_candidate("m5", "e@example.com", "marketing"),
        make_candidate("m6", "f@example.com", "marketing"),
    ]
    batches, remaining = cluster_candidates(candidates, 3)
    assert len(batches) == 2
    categories = sorted(b.category for b in batches)
    assert categories == ["marketing", "newsletter"]
    assert remaining == []


def test_empty_input():
    batches, remaining = cluster_candidates([], 3)
    assert batches == []
    assert remaining == []


def test_case_insensitive_domain():
    candidates = [
        make_candidate("m1", "a@SubStack.com", "newsletter"),
        make_candidate("m2", "b@substack.COM", "newsletter"),
        make_candidate("m3", "c@SUBSTACK.com", "newsletter"),
    ]
    batches, _ = cluster_candidates(candidates, 3)
    assert len(batches) == 1
    assert batches[0].domain == "substack.com"
