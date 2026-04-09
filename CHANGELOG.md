# Changelog

## 2026-04-09

### Slice 003 — rules files, principles gate, redactor pipeline
- Rules loader (`src/rules.ts`) parses `principles.md` as YAML, extracting blacklist entries and redaction rules. Watches the directory for file changes and reloads automatically.
- Principles gate (`src/principlesGate.ts`) enforces a `(transport, action_class)` blacklist before dispatching any approved action. Blocked actions return 403 and are journalled as `kind: blocked`.
- Redactor enhanced with `applyRedactionRules`: field-level drops and regex-pattern replacement on top of the existing hard-coded body stripping.
- Goal now carries optional `transport` and `action` fields so the blacklist can be checked at dispatch time.
- Executor entry point loads rules from disk at startup and watches for live reloads.
- 24 tests across 5 files covering all three layers with synthetic rules.

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
