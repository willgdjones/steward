## Parent PRD

`design.md`

## What to build

Let users describe a new capability they want in natural language; the system drafts an implementation and surfaces it as a meta-card for review.

- A "request a capability" affordance in the web client where the user types a description (e.g. "I want to be able to cancel subscriptions for me")
- The frontier planner drafts an implementation: typically a browser sub-agent prompt template, occasionally a thin code module
- The draft is surfaced as a meta-card showing: the natural-language description, the proposed implementation (code or template), the credentials it would require, the irreversible steps it could take
- Approval lands the new capability in the registry
- Rejection records the request for later
- Approved capabilities follow the same rules as hand-written ones: they go through the executor, respect the principles gate, halt on irreversibility, write to the journal, and require verification

See §3 (rule promotion analogue) and the §7-revised discussion of capability registration.

## Acceptance criteria

- [ ] User can submit a natural-language capability request from the web client
- [ ] Planner drafts an implementation and surfaces a meta-card
- [ ] Meta-card shows description, implementation, required credentials, possible irreversible steps
- [ ] Approval registers the capability; rejection records the request
- [ ] Newly registered capabilities go through the same gates as hand-written ones

## Blocked by

- Blocked by `issues/012-send-email-1password-cli.md`

## User stories addressed

- Decision §3 (promotion mechanism, generalised)
- Decision §7 (capabilities as sub-agents, user-extensible)
