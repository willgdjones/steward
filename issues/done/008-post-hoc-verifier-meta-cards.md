## Parent PRD

`design.md`

## What to build

A slow background cron that re-checks the agent's recent work and surfaces anomalies as meta-cards.

- For each recently archived message, check Gmail for: replies in the thread, bounces, user-initiated unarchive
- For each recently completed goal in `state/done/`, run any goal-specific verifier
- Anomalies become meta-cards in the same swipe queue: "I archived this newsletter yesterday but you replied to the sender today — should I stop archiving them?"
- Activity view in the web UI shows the last N executed actions with reasons, results, and a "this was wrong" button; clicking it also emits a meta-card

This is the structural answer to the brief's "models decay quietly" failure: mistakes the user *doesn't* notice are caught by the verifier and surfaced for correction.

See §17 of the PRD.

## Acceptance criteria

- [ ] Post-hoc verifier runs on a slow cron (interval declared in `principles.md`)
- [ ] Verifier detects at least: replies after archive, user-unarchive
- [ ] Anomalies appear as meta-cards in the queue
- [ ] Activity view in the web UI lists recent actions with a "wrong" button
- [ ] Clicking "wrong" emits a meta-card

## Blocked by

- Blocked by `issues/007-batched-action-card.md`

## User stories addressed

- Decision §17 (observability + post-hoc verifier)
