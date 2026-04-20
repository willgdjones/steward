## Parent PRD

`design.md`

## What to build

Add APNs push notifications to the iOS client.

- Apple developer account, push certificates, a tiny relay that the agent pokes which then pokes APNs
- Push payloads contain only "you have a new card" — **no card content**
- The phone fetches the actual card from the agent over Tailscale once foregrounded
- This preserves the §12 privacy stance: no third party (Apple included) sees personal data
- HITL because of the developer-account / cert / relay setup

See §12 of the PRD.

## Acceptance criteria

- [ ] Apple developer account and push certificates set up
- [ ] Relay server forwards "new card" pings from the agent to APNs
- [ ] Push payloads contain no personal content
- [ ] Foregrounding the iOS app fetches the card from the agent over Tailscale
- [ ] No personal data observable in the APNs payload

## Blocked by

- Blocked by `issues/018-ios-client-tailscale.md`

## User stories addressed

- Decision §12 (push notifications, deferred)
