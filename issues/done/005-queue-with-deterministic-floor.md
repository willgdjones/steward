## Parent PRD

`design.md`

## What to build

Promote the single-card flow into a real queue with the queue-depth trigger model:

- Configurable `target_depth` and `low_water_mark` in `principles.md`
- Background refill loop wakes when the visible queue drops below low-water and tops up to target
- Manual "show me now" forces an immediate refill cycle
- Slow safety-net cron (e.g. hourly) as a heartbeat
- **Urgent-senders escalation**: senders declared in `principles.md` bypass the queue and surface immediately
- **Deterministic floor**: `principles.md` declares slot reservations (e.g. "always 2 slots for items with deadline <72h"); the floor is enforced before any other ranking
- A simple recency / age tiebreaker for everything else (no learned ranker yet — that's slice 010)

By construction the queue cannot exceed target depth. The 1,892-todo failure mode is structurally unavailable.

See §13, §14 (deterministic floor only) of the PRD.

## Acceptance criteria

- [ ] Queue maintains target depth and refills on low-water
- [ ] Manual refill works
- [ ] Urgent-sender rule causes immediate-surfacing of a matching message
- [ ] Floor reservations are honoured even when other candidates score higher on the tiebreaker
- [ ] Queue size never exceeds target depth

## Blocked by

- Blocked by `issues/004-local-triage-in-the-loop.md`

## User stories addressed

- Decision §13 (queue-depth trigger)
- Decision §14 (deterministic floor; learned weights deferred)
