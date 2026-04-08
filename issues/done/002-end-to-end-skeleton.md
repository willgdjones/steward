## Parent PRD

`design.md`

## What to build

The spine of the system, end-to-end, with no real intelligence or execution. Establishes the load-bearing structural properties before anything is built on top of them.

- Gmail OAuth flow with token storage outside any LLM-readable location
- **Two processes from day one**: an executor process and an LLM process, with no shared filesystem path for credentials
- The executor reads one unread Gmail message and hands a redacted reference to the LLM process
- The LLM process produces a single trivial goal (e.g. "review this message")
- A local HTTP API on the executor process serves the goal as a card
- A minimal local web client renders the card and supports approve / reject / defer
- An approve writes a structured entry to the decision journal (`state/journal.jsonl`)

No real action is taken on Gmail. No rules. No ranker. No queue. The point is to prove the spine works and the process split holds.

See §1, §11, §12, §15 (structural separation), §17 of the PRD.

## Acceptance criteria

- [ ] OAuth completes and refresh token is stored outside the LLM process's reach
- [ ] Two separate processes; LLM process has no read access to the credential location
- [ ] Reading one Gmail message → producing a goal → rendering a card → swiping → journal entry works end-to-end
- [ ] Journal entries are append-only JSONL written by the executor

## Blocked by

None - can start immediately.

## User stories addressed

- Decision §1 (Gmail-only wedge)
- Decision §11 (in-flight state as plain files)
- Decision §12 (single local HTTP API)
- Decision §15 (structural credential separation)
- Decision §17 (decision journal)
