## Parent PRD

`design.md`

## What to build

Collapse N similar archive goals into a single batched-action card so the user can clear newsletters, promotions, etc. in one swipe.

- Frontier planner detects clusters of similar candidate goals (same sender domain, same category) and produces a batched goal
- The card shows the count and a small sample ("archive these 47 newsletters: substack.com, mailchimp, ...")
- Approval dispatches the batch to the Gmail sub-agent
- Verification re-fetches a sample (or the full set) and confirms the batch succeeded
- Single journal entry per batched goal, with the message-ID list recorded

This is the first viscerally satisfying card per §18 — the moment of the wow.

See §2 (on-ramp) and §18 of the PRD.

## Acceptance criteria

- [ ] Planner produces a batched goal when ≥N similar candidates exist
- [ ] Card displays count, sample, and reason
- [ ] One swipe triggers the whole batch
- [ ] Verification confirms the batch (sampled or full)
- [ ] Journal records the full message-ID list

## Blocked by

- Blocked by `issues/006-archive-via-gmail-api.md`

## User stories addressed

- Decision §2 (batched cards as on-ramp)
- Decision §18 (day-one wow)
