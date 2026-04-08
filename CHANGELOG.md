# Changelog

## 2026-04-08

### Dev infrastructure
- Initialised Node/TypeScript project (strict tsconfig, ESM, vitest).
- Added `npm test` and `npm run typecheck` scripts as the canonical feedback loops.
- Added `src/journal.ts` with append-only JSONL writer (load-bearing for issue 002) plus tests.

### Slice 002 — end-to-end skeleton
- Two-process spine: executor (`src/executor/`) spawns the planner (`src/planner/`) as a child process over stdio.
- Credential separation enforced structurally via `sanitiseEnvForPlanner` — `CRED`/`TOKEN`/`SECRET`/`KEY`/`PASSWORD` and `STEWARD_CREDENTIALS_DIR` are stripped before spawning the planner.
- `FakeGmail` provider stands in for real Gmail OAuth (deferred to issue 004); same surface so the swap is local.
- Deterministic redactor strips message body and reduces `from` to a domain before any LLM-side process sees it.
- Local HTTP API: `GET /card`, `POST /card/:id/decision`, plus a minimal embedded web client at `/`.
- Approve/reject/defer all journalled via `appendJournal` to `state/journal.jsonl`.
