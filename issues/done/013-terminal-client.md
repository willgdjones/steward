## Parent PRD

`design.md`

## What to build

A small terminal client that hits the same local HTTP API as the web client.

- `j`/`k` to navigate cards, `y`/`n`/`d` to approve / reject / defer
- Renders cards with goal, reason, irreversible-step badge, and a small sample of the underlying Gmail content
- No new state, no new logic — purely a second client over the existing API
- Validates that the local HTTP API is genuinely client-agnostic (any drift would surface here)

See §12 of the PRD.

## Acceptance criteria

- [ ] Terminal client connects to the local HTTP API
- [ ] Keybindings work for navigate / approve / reject / defer
- [ ] Cards render with goal, reason, and irreversible-step badge
- [ ] Live updates via websocket (cards appear without manual refresh)

## Blocked by

- Blocked by `issues/012-send-email-1password-cli.md`

## User stories addressed

- Decision §12 (single API, three thin clients)
