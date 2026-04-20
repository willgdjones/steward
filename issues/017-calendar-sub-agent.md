## Parent PRD

`design.md`

## What to build

Register a `calendar` sub-agent over the Google Calendar API and add `calendar.md` as a second per-surface rules file.

- Read events, create events, decline events
- Reversibility: read is reversible, create and decline are irreversible (use the halt from slice 011)
- Verification re-fetches the event after a write to confirm it matches
- `calendar.md` carries soft rules for which meeting types to auto-decline, which senders' meetings to prioritise, etc.
- Goals like "decline the dentist on the 15th" become a one-step plan handed to the calendar sub-agent

See §4, §5 of the PRD.

## Acceptance criteria

- [ ] Calendar sub-agent registered
- [ ] Read / create / decline operations available
- [ ] Create and decline declared irreversible and trigger halts
- [ ] `calendar.md` loaded alongside `gmail.md`
- [ ] Verification re-fetches events after writes
- [ ] End-to-end test: a goal "decline the test event" produces a card, halts, approval declines the right event

## Blocked by

- Blocked by `issues/012-send-email-1password-cli.md`

## User stories addressed

- Decision §4 (per-surface rules)
- Decision §5 (multiple sub-agents)
