## Parent PRD

`design.md`

## What to build

A native iOS app that hits the same local HTTP API over Tailscale (foreground only, no APNs).

- iOS client renders cards, supports approve / reject / defer
- Reaches the agent over Tailscale so the API never has a public surface
- Foreground-only: no push notifications yet; the user opens the app and sees the current queue
- Validates the API surface beyond the Tailnet boundary
- HITL because Tailscale setup is one-time user infrastructure and the API surface deserves a review before it's exposed beyond localhost

See §12 of the PRD.

## Acceptance criteria

- [ ] iOS app installed on the user's device (TestFlight or local build)
- [ ] Connects to the agent over Tailscale
- [ ] Cards render with goal, reason, irreversible badge
- [ ] Approve / reject / defer round-trip to the executor and journal correctly
- [ ] Live updates via websocket while foregrounded
- [ ] User has reviewed the API surface exposed beyond localhost

## Blocked by

- Blocked by `issues/012-send-email-1password-cli.md`

## User stories addressed

- Decision §12 (single API, three thin clients)
