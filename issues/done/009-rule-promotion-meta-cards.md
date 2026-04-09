## Parent PRD

`design.md`

## What to build

Pattern detection over `state/done/` that proposes standing rules to the user via meta-cards.

- A small detector greps `state/done/` for repeated swipe patterns (e.g. "user has approved 8 archives from substack.com in a row, with no rejects")
- When a threshold is hit, emit a meta-card: "Promote 'archive substack.com newsletters' to a standing rule in `gmail.md`?"
- Approval writes a diff to `gmail.md` (the agent edits the file directly; the user can review the diff)
- The rule then takes effect on the next refill cycle
- Rejection records the negative pattern so the meta-card isn't re-proposed for a cool-down period

See §3, §4 of the PRD.

## Acceptance criteria

- [ ] Pattern detector runs over `state/done/` on a slow cron
- [ ] Threshold and cool-down configurable in `principles.md`
- [ ] Meta-card displays the proposed rule diff
- [ ] Approval writes the diff to `gmail.md` and reloads rules
- [ ] Rejection prevents re-proposal for the cool-down window

## Blocked by

- Blocked by `issues/008-post-hoc-verifier-meta-cards.md`

## User stories addressed

- Decision §3 (rule promotion)
- Decision §4 (rules layout)
