## Parent PRD

`design.md`

## What to build

A payment capability built on a real payment API (e.g. Stripe Issuing or similar virtual-card API), not browser-driven form filling.

- Register a `payments` sub-agent that issues / charges virtual cards via the chosen API
- Card credentials and API keys are `op://` references; the executor resolves at the moment of charge
- `principles.md` declares per-payment-class limits (e.g. max £50 per single charge, max £200/day) on top of the issuer's own limits
- Every charge is irreversible and triggers the halt from slice 011
- Verification re-fetches the charge from the API and confirms amount, payee, and timestamp
- HITL because it's the first money-moving capability and the user must review the credential flow + spending limits end-to-end

See §15, §9 of the PRD.

## Acceptance criteria

- [ ] Payments sub-agent registered, charge action declared irreversible
- [ ] Per-charge halt with clear re-approval card showing amount and payee
- [ ] API keys and card details stored as `op://` references; never logged
- [ ] Spending limits in `principles.md` enforced before dispatch
- [ ] Issuer-side card limits set as a second deterministic layer
- [ ] Verification re-fetches the charge from the API
- [ ] User has reviewed the end-to-end flow

## Blocked by

- Blocked by `issues/012-send-email-1password-cli.md`

## User stories addressed

- Decision §15 (virtual cards + 1Password)
- Decision §9 (irreversibility halts)
