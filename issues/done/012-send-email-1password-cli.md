## Parent PRD

`design.md`

## What to build

The first genuinely irreversible action, gated end-to-end by the credential machinery. Closes phase 1.

- Add `send_draft` to the Gmail sub-agent (sends an existing draft)
- `send_draft` is declared irreversible in `principles.md` and triggers the halt from slice 011
- **1Password CLI integration**: Gmail OAuth refresh token is stored as an `op://` reference, not on disk
- Executor resolves `op://` references at the moment of dispatch and never logs the resolved value
- `principles.md` declares which `op` scopes `send_draft` requires
- Executor refuses to dispatch `send_draft` if the required scope is not currently unlocked in `op` (so an idle-locked vault automatically degrades the agent to read-only)
- End-to-end test on a real draft, with the user reviewing the credential flow before merge

This is HITL because it's the first money/identity-affecting action and the credential flow is the most security-sensitive part of the system.

See §9, §15 of the PRD.

## Acceptance criteria

- [ ] `send_draft` capability registered, declared irreversible
- [ ] Sending a draft halts and shows a re-approval card with the recipient and subject
- [ ] Gmail token stored as `op://` reference; not present on disk in plaintext
- [ ] Executor resolves `op://` at dispatch and never logs the value
- [ ] Locking the vault causes the executor to refuse `send_draft` deterministically
- [ ] User has reviewed the end-to-end credential flow

## Blocked by

- Blocked by `issues/011-irreversibility-halts-draft-reply.md`

## User stories addressed

- Decision §9 (irreversibility halts)
- Decision §15 (1Password CLI + structural separation)
