## Parent PRD

`design.md`

## What to build

Promote the floor-only ranker from slice 005 to the full (δ) ranker:

- Structured scorer over explicit features (deadline, amount, sender importance, age, waiting-on-user, reversibility-risk)
- **Weights learned from swipes** in `state/done/`: quick approves raise weight on the features that scored high; defers/rejects lower them
- Weights **clamped** so a learned signal can never fully override a `principles.md` floor
- **Exploration slots**: a small fixed number of queue positions reserved for high-uncertainty candidates
- Ranker remains debuggable: each queue position has a per-feature score breakdown viewable in the activity view

See §14 of the PRD.

## Acceptance criteria

- [ ] Feature scorer evaluates each candidate across all declared features
- [ ] Weights update from swipe history with clamping
- [ ] Exploration slots reserved per refill cycle
- [ ] Per-card feature breakdown visible in the activity view
- [ ] Floor reservations from slice 005 still honoured

## Blocked by

- Blocked by `issues/009-rule-promotion-meta-cards.md`

## User stories addressed

- Decision §14 (learned-over-features ranker with floor and exploration)
