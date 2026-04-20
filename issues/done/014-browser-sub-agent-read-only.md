## Parent PRD

`design.md`

## What to build

Register `browser` as a second sub-agent capability, restricted to read-only operations.

- Browser sub-agent accepts a free-form natural-language instruction (e.g. "navigate to this invoice URL and extract the amount due and the due date")
- Implementation can wrap browser-use, Playwright, or computer-use
- No clicks on submit buttons, no form fills, no logins — read and extract only
- Returns a structured outcome that the parent can verify
- Parent verification re-fetches or cross-checks the extracted data against the original goal

Establishes the sub-agent contract pattern for browser work without taking any irreversible action.

See §5, §7, §8 of the PRD.

## Acceptance criteria

- [ ] Browser sub-agent registered in the executor
- [ ] Read-only restriction enforced (no submit / click on irreversible elements)
- [ ] Free-form instruction in, structured outcome out
- [ ] Verification step on the parent confirms the extracted data
- [ ] Decision journal records the instruction and outcome

## Blocked by

- Blocked by `issues/012-send-email-1password-cli.md`

## User stories addressed

- Decision §5 (multiple sub-agents)
- Decision §7 (verification at the boundary)
- Decision §8 (goal / plan / sub-agent layers)
