# Changelog

## 2026-04-09

### Slice 005 — queue with deterministic floor
- Promoted single-card flow to a real queue with configurable `target_depth` and `low_water_mark` in `principles.md`.
- `src/ranker.ts` (NEW): deterministic floor reservations (e.g. "always 2 slots for items with deadline <72h"), then urgency tiebreaker for remaining slots. Queue size structurally capped at `target_depth`.
- `src/rules.ts`: extended `Rules` type with `QueueConfig`, `urgent_senders`, and `FloorReservation[]`. Parsed from `principles.md` YAML with sensible defaults (target_depth=5, low_water_mark=2).
- `src/executor/server.ts`: refactored from `current: CardState | null` to `queue: CardState[]`. Refill logic triages all candidates, ranks them via the ranker, and fills up to `target_depth`. Low-water trigger on `GET /card`. Urgent senders bypass ranking and insert at front. `GET /queue` endpoint for queue inspection. `POST /refill` for manual refill.
- 49 tests across 7 files; 8 new tests for queue depth, refill, urgent senders, floor reservations, deduplication, and rules parsing.

### Slice 004 — frontier triage in the loop
- Two-stage pipeline: cheap model (Haiku) extracts structured features from full message, expensive model (Sonnet/Opus) produces real goals from redacted slice + features.
- `src/triage.ts`: `TriageFeatures` type (deadline, amount, waiting_on_user, category, urgency), `createTriage()` factory backed by Anthropic SDK, `defaultTriageResult()` fallback.
- `src/planner/index.ts`: `PlannerInput` type carries redacted message + triage features + snippet. `createPlanner()` factory for frontier model. Trivial `planGoal()` preserved as fallback.
- `src/executor/server.ts`: pipeline now runs triage → redact → plan. `ServerDeps` accepts injectable `triage` function and `searchQuery`.
- `src/gmail/fake.ts`: added `search(query)` method (returns unread messages; real Gmail API query deferred).
- `src/executor/index.ts`: auto-detects `ANTHROPIC_API_KEY` and activates frontier pipeline; falls back to trivial planner otherwise.
- `@anthropic-ai/sdk` added as runtime dependency.
- 30 tests across 6 files; 4 new tests for the two-stage pipeline covering data flow, redaction ordering, search, and fallback.

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
