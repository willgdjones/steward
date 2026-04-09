## Parent PRD

`design.md`

## What to build

The first real action: the Gmail sub-agent can archive a message after the user approves a card.

- Register the **Gmail sub-agent** as the first capability (free-form natural-language instruction in, structured outcome out)
- Implement `archive` as a reversible operation
- Reversibility declared in `principles.md`
- After the sub-agent reports success, a **verification step** re-fetches the message from Gmail and confirms it has the `archived` label
- Full goal → plan → sub-agent instruction → outcome → verification flow journalled by the executor

See §5, §7, §8, §9, §17 of the PRD.

## Acceptance criteria

- [ ] Gmail sub-agent dispatches a free-form instruction and returns a structured outcome
- [ ] Archive action declared reversible in `principles.md`
- [ ] Verification re-fetches the message and confirms state
- [ ] Journal entry includes goal, instruction, outcome, and verification result
- [ ] Approving an archive card on a real inbox archives the message and is verifiable in Gmail's UI

## Blocked by

- Blocked by `issues/005-queue-with-deterministic-floor.md`

## User stories addressed

- Decision §5 (multiple transports / sub-agents)
- Decision §7 (action representation — outcome verification at the boundary)
- Decision §8 (goal / plan / sub-agent layers)
- Decision §9 (reversibility tier)
- Decision §17 (verification + journal)
