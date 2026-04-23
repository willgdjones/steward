# Changelog

## 2026-04-23

### Slice 016 — payments capability (fake provider; HITL pending)
- New `payments` transport with `charge` action. Declared irreversible — rides the existing halt machinery (slice 011), so every approve goes through a re-approval card showing amount + payee + card ref before the charge fires.
- `steward/payments/fake.py` (NEW): `FakePaymentProvider` — in-memory charge store with idempotency-key dedup and injectable issuer-failure for tests. Real Stripe Issuing adapter (or similar) is a separate slice; same `charge` / `get_charge` surface keeps the swap local.
- `steward/payments/subagent.py` (NEW): `FakePaymentsSubAgent` with `dispatch` + `verify`. Amounts are int minor units (pence) — no floats for money. Verify re-fetches the charge and checks amount + payee + status all match.
- `steward/payments/limits.py` (NEW): `check_spending_limits` — per-charge cap, per-day window, per-week window. Pure function over the journal so replay / test paths exercise it without the executor. Failed charges and non-charge entries are ignored in aggregation.
- `steward/rules.py`: new `SpendingLimits` type and `spending_limits:` block in `principles.md`. All three caps optional; None means no deterministic cap (issuer-side card limit is still a second defence layer).
- `steward/executor/server.py`: new `payments` dispatch path. Order of checks before a charge fires: blacklist → spending-limits → irreversibility halt → credential scope → dispatch → verify → journal. Re-approval cards now carry `amount_pence`, `currency`, `payee`, `cardRef`, `idempotencyKey` through the halt.
- Tests: 32 new (5 fake provider, 9 sub-agent, 10 limits, 8 executor e2e). Total: 226 passing. Coverage includes per-charge / per-day / per-week caps, stale-entry exclusion from day window, journal never contains resolved `sk_` values (op:// refs stored instead), idempotency dedup, invalid-amount-type 400, vault-locked 403, ISO-timestamp fallback for pre-slice-016 entries.
- HITL: end-to-end flow needs user review (acceptance criterion #7) before any real provider is wired up.

### Slice 020 — replay harness
- `steward/replay.py` (NEW): `replay_entry`, `replay_journal`, `format_report`. Takes historical journal entries carrying planner-input context and re-runs the planner against them with current rules. Divergence = `transport` or `action` differs between historical and new; free-form title/reason deliberately ignored to tolerate LLM wording variance.
- `steward/executor/server.py`: journal entries (kind: `decision` and `action`) now include `redactedMessage`, `snippet`, and `features` so replay can reconstruct the exact planner input. Added a `_replay_context(card)` helper; propagated via `**self._replay_context(card)` in every dispatch site (archive, draft_reply, send_draft, browser_read, browser_authenticated_read, generic decision). `CardState` carries `redacted_message` + `snippet` alongside `features`.
- CLI: `python -m steward.replay --journal PATH --rules-dir PATH --since ISO-DATE`. Exits non-zero on any divergence so the harness can sit in a CI gate for rule-change PRs.
- 194 tests (11 new): divergence detection for action+transport changes, non-divergence for same output, unreplayable-entry skipping (missing redactedMessage/features, wrong kind), `--since` filtering, redaction rules applied at replay time (tightening rules round-trip correctly), action-kind entries replayable (not just decisions), report formatting.
- Closes design.md §17's replay-harness bullet — the observability loop (journal → activity view → verifier → meta-cards → replay) is now end-to-end.

### Slice 015 — authenticated browser sub-agent
- New capability `browser_authenticated_read` on the browser sub-agent. Instruction carries `loginUrl`, `targetUrl`, `usernameRef`/`passwordRef` as `op://` references, plus form selectors. Declared reversible for pure read; submit-click side effects accepted as minimal.
- `steward/browser/redactor.py` (NEW): defense-in-depth scrub of resolved credential values from browser outcome fields (`pageTitle`, `textContent`, `error`, `url`, `actual_url`, `actual_title`, `extracted`). Case-sensitive exact-match replacement. `MIN_CRED_LEN=4` skips short strings to avoid false positives. Module docstring enumerates accepted limitations (case variants, fragment leaks, screenshots not yet wired).
- `steward/executor/server.py`: credential scope check now applies to browser transport (not just gmail). New `_dispatch_browser_authenticated_read`: resolves op:// refs at dispatch time in local scope only, calls sub-agent with `resolved_creds` bundle, redacts outcome before journal-write and HTTP response. Journal stores `usernameRef`/`passwordRef`/`targetUrl` for audit — never resolved values.
- `steward/browser/harness.py` (NEW): `BrowserHarnessSubAgent` shells out to the `browser-harness` CLI with a generated Python script on stdin. Resolved credentials injected via env vars (`STEWARD_CRED_USERNAME`, `STEWARD_CRED_PASSWORD`) — never baked into the script text. Form fill uses selector-based JS with explicit `input`/`change` event dispatch (framework-safe); submit click is selector-based. Stdout parsed for a `STEWARD_RESULT:` sentinel line.
- `steward/executor/__main__.py`: opt-in wiring via `STEWARD_BROWSER=harness` and `STEWARD_CREDENTIALS=op`. Default stays headless / Gmail-only, so no regressions for existing users.
- 183 tests across 22 files (19 new): 8 for redactor coverage, 4 for the fake authenticated sub-agent, 6 executor e2e (happy path with redaction, locked vault short-circuits, verify re-fetches target, journal records refs not values), 13 for the browser-harness integration (script generation, result parsing, dispatch paths via injectable runner).
- Live-browser integration is manual: with Chrome running and `browser-harness --setup` done, start the executor with `STEWARD_BROWSER=harness STEWARD_CREDENTIALS=op uv run python -m steward.executor`. Not exercised by pytest.

### Python port — parity with TS through slice 014
- Full port of steward from Node/TypeScript to Python, landed side-by-side in `python/`. Motivated by a preference for Python and compatibility with `browser-use/browser-harness` (Python-only).
- Stack: Python 3.12+, aiohttp (HTTP + WebSocket in one stack), anthropic SDK, pyyaml, websockets, pytest + pytest-asyncio, managed via `uv`.
- Modules ported 1:1: `journal`, `redactor`, `rules` (YAML loader + poll-based watcher), `principles_gate`, `credentials`, `triage`, `ranker` (learned weights, floor reservations, exploration slots), `batcher`, `verifier`, `promoter`, `gmail/fake`, `gmail/subagent`, `browser/subagent` (fake only — CDP deferred), `planner`, `executor/server` + `executor/__main__`, `executor/planner_client` with env sanitisation, `tui` (aiohttp WebSocket client with termios raw input).
- Wire-compat: JSON field names preserved exactly (`messageId`, `draftId`, `fromDomain`, `batchSize`, etc.) so the Python executor speaks the same protocol as the TS one. Journal format identical.
- Tests: 152 pytest tests across 21 files, covering all behaviours from the 153-test TS suite (one TS test collapsed into a dataclass equality assertion). All green.
- TS tree (`src/`, `tests/`) retained as legacy reference; will be removed once the Python path proves out.

## 2026-04-11

### Slice 014 — browser sub-agent (read-only)
- Registered `browser` as a second sub-agent capability, restricted to read-only page extraction. Inspired by browser-harness-js: direct CDP, "the protocol is the API."
- `src/browser/cdp.ts` (NEW): minimal CDP client over WebSocket. Connect, navigate, evaluate JS, get page content/title/URL. No framework, one WebSocket to Chrome.
- `src/browser/subagent.ts` (NEW): `BrowserSubAgent` interface (dispatch + verify) following the same contract as `GmailSubAgent`. Only `browser_read` capability accepted — no form submits, no clicks on irreversible elements. `createFakeBrowserSubAgent()` for testing with canned responses.
- `src/executor/server.ts`: wired `browser_read` dispatch path. Goals with `transport: 'browser'` and `action: 'browser_read'` dispatch to the browser sub-agent. Verification re-fetches the URL. Journal records instruction and outcome.
- 153 tests across 13 files; 8 new tests covering browser dispatch/verify, read-only enforcement, failure handling, and executor integration.

### Slice 013 — terminal client and WebSocket live updates
- Added WebSocket server to executor for live queue updates. All connected clients receive `queue_update` messages whenever the queue changes (decisions, refills, verifier/promoter runs).
- `src/executor/server.ts`: `WebSocketServer` (ws library) attached to the HTTP server. Broadcasts after every queue mutation. New clients receive current queue state on connect.
- `src/tui.ts` (NEW): terminal client with `j`/`k` navigation, `y`/`n`/`d` for approve/reject/defer, `q` to quit. Renders cards with title, reason, transport/action, irreversible badge. Connects via WebSocket for live updates, reconnects on disconnect.
- `package.json`: added `ws` dependency, `@types/ws` dev dependency, `tui` script.
- 145 tests across 12 files; 4 new WebSocket e2e tests covering initial state on connect, live updates on decision, multi-client broadcast, and card detail rendering.

### Slice 012 — send_draft and 1Password credential gating
- Added `send_draft` capability to the Gmail sub-agent: sends an existing draft, verifies it was sent, declared irreversible in principles.md.
- `src/gmail/fake.ts`: added `sendDraft()` method and `sent` field on `GmailDraft`.
- `src/gmail/subagent.ts`: extended `dispatch()` for `send_draft` (sends via FakeGmail, returns draftId). Extended `verify()` for `send_draft` (confirms draft.sent).
- `src/credentials.ts` (NEW): `resolveOpReference()` calls `op read <ref>` at dispatch time, never logs resolved value. `isVaultUnlocked()` checks vault state via `op whoami`. `checkCredentialScopes()` validates required refs before dispatch. Injectable `CredentialResolver` interface for testing.
- `src/rules.ts`: added `CredentialScopeDecl` type and `credential_scopes` field to Rules. Parsed from principles.md YAML.
- `src/executor/server.ts`: credential check gate before dispatch — refuses with 403 + `credential_refused` when vault is locked or refs fail. `ServerDeps.credentialResolver` injectable for testing. `send_draft` dispatch path with verification. Re-approval cards now carry over extra goal properties (draftId, draftBody).
- Fixed pre-existing test fragility: floor reservation tests used hardcoded deadlines that expired; replaced with relative future dates.
- 141 tests across 12 files; 15 new tests covering send_draft dispatch/verify, credential scope checking, vault-locked refusal, unlocked passthrough, and config parsing.

## 2026-04-09

### Slice 011 — irreversibility halts and draft reply
- Added `draft_reply` capability to the Gmail sub-agent: creates a Gmail draft in reply to a message, returns the draft ID, and verifies the draft exists.
- `src/gmail/fake.ts`: added `GmailDraft` type, `createDraft()`, `getDraft()`, `listDrafts()` methods to FakeGmail.
- `src/gmail/subagent.ts`: extended `dispatch()` to handle `draft_reply` (creates draft via FakeGmail). Extended `verify()` with optional `meta` parameter for draft verification. Added `draftBody` to `SubAgentInstruction` and `draftId` to `SubAgentOutcome`.
- `src/executor/server.ts`: implemented halt-on-irreversible machinery. When an approved goal's action is declared irreversible in `principles.md`, the executor halts before dispatching, journals a `kind: 'halt'` entry, and surfaces a re-approval card showing the original goal, the irreversible action, and the reason. Re-approval cards dispatch normally when approved. Added `draft_reply` dispatch path with verification.
- 126 tests across 11 files; 10 new tests covering draft_reply dispatch/verify, halt on irreversible actions, re-approval card content, reversible action passthrough, re-approval rejection, and reversibility config parsing.

### Slice 010 — learned ranker with exploration
- Promoted the floor-only ranker to a full feature scorer with weights learned from swipe history, clamping, exploration slots, and per-card score breakdowns.
- `src/ranker.ts`: added `FeatureVector` (deadline_proximity, has_amount, waiting_on_user, urgency), `FeatureWeights` with clamped bounds, `extractFeatureVector()`, `scoreCandidate()`, `learnWeights()` from journal decision entries. `rankCandidates()` now uses learned weights, reserves exploration slots for high-uncertainty candidates, and attaches `ScoreBreakdown` to each ranked candidate.
- `src/rules.ts`: `QueueConfig` extended with `exploration_slots` (default 1, configurable in `principles.md`).
- `src/executor/server.ts`: refill pipeline learns weights from journal, passes exploration slots config to ranker. Decision journal entries now include `features` for weight learning. `/queue` endpoint exposes per-card `breakdown` and `exploration` flag.
- 116 tests across 11 files; 21 new tests covering feature extraction, scoring, weight learning, clamping, exploration slots, journal feature storage, breakdown visibility, and config parsing.

### Slice 009 — rule promotion meta-cards
- Pattern detector scans the decision journal for repeated swipe patterns (e.g. "user approved 5 archives from substack.com") and proposes standing rules via meta-cards.
- `src/promoter.ts` (NEW): `detectPromotions()` groups `kind: 'action'` journal entries by (transport, action, senderDomain). When count >= threshold, returns `Promotion[]` with proposed rule text. Respects cooldowns after rejection and skips already-promoted patterns.
- `src/rules.ts`: added `PromotionConfig` type with `threshold` (default 5), `cooldown_minutes` (default 1440/24h), `interval_minutes` (default 120). Parsed from `principles.md` YAML.
- `src/executor/server.ts`: promoter cron runs on configurable interval. `POST /promoter/run` for manual trigger. Promotions become meta-cards in the queue. Approving writes the rule to `gmail.md` and journals `kind: 'rule_promoted'`. Rejecting journals `kind: 'promotion_rejected'` with cooldown enforcement.
- 95 tests across 11 files; 13 new tests covering pattern detection, threshold, cooldown, dedup, approval writing to gmail.md, rejection cooldown, and config parsing.

### Slice 008 — post-hoc verifier meta-cards
- Post-hoc verifier detects anomalies in recently executed actions: user-unarchive (message archived by agent but unarchived by user) and reply-after-archive (new unread message from same sender with matching subject).
- `src/verifier.ts` (NEW): `detectAnomalies()` reads the journal for `kind: 'action'` entries, checks Gmail state for each archived message, returns typed `Anomaly[]`. Deduplicates via `kind: 'verifier_anomaly'` journal entries.
- `src/journal.ts`: added `readJournal()` to parse JSONL entries for the verifier.
- `src/rules.ts`: added `VerifierConfig` type with `interval_minutes` (default 60, configurable in `principles.md`).
- `src/executor/server.ts`: verifier cron runs on configurable interval. `POST /verifier/run` for manual trigger. Anomalies become meta-cards (category: 'meta') inserted into the queue. `GET /activity` returns recent action + anomaly entries. `POST /activity/:goalId/wrong` lets users flag actions as incorrect, emitting a meta-card.
- 82 tests across 10 files; 12 new tests covering anomaly detection, meta-card insertion, deduplication, activity view, wrong button, and verifier config parsing.

### Slice 007 — batched action card
- Collapse N similar archive goals into a single batched-action card. Clusters messages by (sender domain, category) and produces a batched card when ≥`batch_threshold` similar candidates exist.
- `src/batcher.ts` (NEW): `clusterCandidates()` groups triaged candidates by domain+category, returns batches meeting threshold alongside unclustered remainder.
- `src/planner/index.ts`: `Goal` extended with optional `messageIds: string[]` and `batchSize: number` for batched goals.
- `src/rules.ts`: `QueueConfig` extended with `batch_threshold` (default 3, configurable in `principles.md`).
- `src/executor/server.ts`: refill pipeline clusters candidates before ranking; batched cards carry all messages. Approval dispatches archive for every message in the batch, verifies a sample (first, middle, last), journals with full message-ID list.
- 70 tests across 9 files; 11 new tests covering clustering, batch dispatch, sample verification, journal ID list, config parsing, and below-threshold fallback.

### Slice 006 — archive via Gmail sub-agent
- First real action: approving an archive card dispatches to the Gmail sub-agent, archives the message, verifies the result, and journals the full flow.
- `src/gmail/subagent.ts` (NEW): `GmailSubAgent` with `dispatch()` and `verify()` methods. Takes free-form `SubAgentInstruction`, returns structured `SubAgentOutcome`. Verification re-fetches the message and confirms archived state.
- `src/gmail/fake.ts`: added `archived` field (optional), `getById()` for verification lookups, `archive()` method. `search()` now excludes archived messages.
- `src/rules.ts`: added `ReversibilityDecl` type and `reversibility[]` to `Rules`. Parsed from `principles.md` YAML.
- `src/executor/server.ts`: on approve of `gmail/archive`, dispatches to sub-agent → verifies → journals as `kind: 'action'` with instruction, outcome, and verification. Non-archive approvals still journal as `kind: 'decision'`.
- 59 tests across 8 files; 10 new tests covering sub-agent dispatch, verification success/failure, archived message exclusion from queue, reversibility parsing, and full e2e flow.

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
