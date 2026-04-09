## Parent PRD

`design.md`

## What to build

Exercise the mid-execution halt machinery on a safe (still-reversible) action: drafting a reply.

- Add `draft_reply` to the Gmail sub-agent's capability set (creates a Gmail draft, does not send)
- Declare `send_email` as irreversible in `principles.md` (preparing for slice 012); `draft_reply` is reversible
- Implement the executor's **halt-on-irreversible** machinery: when the planner emits a plan that contains an irreversible step, the executor halts before that step and surfaces a re-approval card showing exactly what is about to happen
- Verification step re-fetches the draft from Gmail to confirm it exists and matches the planned content
- Test the halt by adding a fake irreversible step type and watching the executor halt; then test the happy path with `draft_reply`

See §9, §17 of the PRD.

## Acceptance criteria

- [ ] `draft_reply` capability registered and verified
- [ ] `principles.md` declares irreversible step types
- [ ] Executor halts on any irreversible step and surfaces a re-approval card
- [ ] Re-approval card shows the goal, the plan, and the irreversible step about to run
- [ ] Drafting a reply on a real message produces a verifiable Gmail draft

## Blocked by

- Blocked by `issues/010-learned-ranker-with-exploration.md`

## User stories addressed

- Decision §9 (reversibility tier and halts)
- Decision §17 (verification + journal)
