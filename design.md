# Steward — Design

A personal AI agent for life admin. Phase 1 is Gmail-only. The destination is exception-only autonomy: the agent silently handles routine work and only interrupts the user for genuine judgment calls.

This document records the decisions that came out of the design interview. Each decision has a one-line rationale; the full reasoning lives in the conversation that produced it.

---

## 1. Wedge

**Phase 1 is Gmail-only.** Every other surface (calendar, shopping, forms, payments) is deferred until the core loop is proven on email. Gmail is the only surface where the official API is unambiguously sanctioned, so day-one work isn't blocked on browser-automation reliability.

## 2. Card altitude

**Cards represent goals, not messages.** The destination is exception-only — the agent acts silently on the routine and surfaces a card only when something needs judgment, carries risk, or matches an explicit "interrupt me" rule. The on-ramp is **batched-action cards** ("archive these 247 newsletters in one swipe") which collapse many low-value decisions into one approval. Per-message swiping is explicitly rejected — it recreates the 1,892-todo failure mode with nicer graphics.

## 3. Rules — promotion

Rules are created via a **hybrid promotion mechanism**: the agent watches swipes and, after a consistent pattern, surfaces a *meta-card* asking the user to promote the pattern to a standing rule. The user approves the rule itself, not every instance. Rules are never silently inferred.

## 4. Rules — layout

Rules are stored as **plain text the user owns**, in a layered layout:

- `principles.md` — global, hard limits, deterministic where possible (spending caps, "never email strangers", transport blacklists, queue parameters, irreversibility declarations, redaction rules).
- `gmail.md`, `calendar.md`, … — per-surface soft rules (which senders matter, which newsletters to archive, which meeting types to auto-decline).

`principles.md` is a hard gate enforced in code; per-surface files are the soft routing layer underneath. Three pillars of the system are all human-readable: rules files, in-flight state files, capability code.

## 5. Execution transports

**Multiple transports coexist**: Gmail API, browser-use, and future others (Outlook, calendar APIs, computer-use). The system is pluggable — adding a transport is local to its adapter.

## 6. Transport selection

**Agent-chosen per call (c-strict)**: the planner picks which transport fits a given instruction. `principles.md` holds an optional `(transport, action_class)` blacklist that the runtime enforces deterministically before dispatch (e.g. "never send email via browser-use"). The LLM has freedom; the deterministic layer has veto. Blacklist starts empty and grows when the user gets burned.

## 7 + 8. Action representation — three layers

After several revisions, the model is:

- **Goal** — free-form natural-language description, what the user swipes. *"Apply to the ARIA grant by Friday."* *"Do my weekly Waitrose shop."* No type enumeration; goals are infinitely varied.
- **Plan** — a short sequence (often just one step) of natural-language instructions, each tagged with the sub-agent that executes it. *`[browser: "do my weekly Waitrose shop using op://Personal/Virtual Card 1, max £80, Saturday delivery"]`*
- **Sub-agent execution** — the sub-agent iterates in the background until it produces a **structured outcome** (`{ordered: [...], total: £74.20, delivery: 2026-04-12}`), halts on an irreversible step, or halts on unresolvable ambiguity.

**Capabilities are sub-agents, not bespoke adapters.** The system ships with a small fixed set: `browser`, `gmail`, `calendar`, `shell`, eventually `computer-use`. Adding "Waitrose support" or "Audible cancellation" is *not* a code change — the browser sub-agent already covers it. The product's power grows with sub-agent quality, not with a capability registry count.

The discipline that survives from earlier framings: **verification at the outcome boundary**. Every sub-agent must return a structured outcome the parent can verify against the goal (re-read the calendar, re-fetch the order confirmation) before the step is marked done. This is the structural answer to the brief's *"sub-agents bubble up errors"* concern — verification is mandatory and cheap, not optional and trusting.

## 9. Mid-execution deviation

**Tiered by reversibility.** Reversible steps (read, navigate, draft, add-to-basket) re-plan silently. Irreversible steps (send, pay, submit, delete, place-order) always halt for re-approval, even if the agent is confident. Reversibility is declared per step-class in `principles.md` — a deterministic property, not a judgment call. Sub-agents enforce this internally and bubble irreversible halts up to the parent goal.

## 10. Inbox context

**No local index, no embeddings, no triage table.** The agent queries Gmail directly via its authenticated CLI/API search (`from:`, `has:attachment`, `is:unread newer_than:7d`, etc.). Gmail's server-side search is already a maintained authoritative index; reproducing it locally would rot. Goal formation is "LLM picks the right Gmail query, reads results, proposes goals."

## 11. In-flight state

Plain files in `state/in-flight/` (one markdown/JSON file per active goal) and `state/done/` (completed goals, also the audit log and the substrate for rule-promotion pattern detection). Crash recovery is `ls state/in-flight/*` on startup. No SQLite, no database — same `cat`/`grep`/`diff` ethos as the rules files.

## 12. Surfaces

**Single local HTTP API on the agent**, three thin clients over the same API:

1. **Local web app** on `localhost` — phase 1, lowest tax, zero deployment.
2. **Terminal client** — small binary hitting the same API, an afternoon's work once the API exists.
3. **iOS app** — phase 2, reaches the agent over Tailscale (or equivalent) so the phone never exposes anything to the public internet.

All state lives in the agent core; clients only render and submit approve/reject/defer. **Websockets** for live updates between the agent and foregrounded clients. **APNs is deferred** until the iOS client is real; even then, push payloads contain only "you have a new card", with the actual content fetched from the agent over Tailscale so no third party sees personal data.

## 13. Trigger

**Queue-depth-based, not time-based.** The agent maintains a swipe queue with a **target depth** (e.g. 8) and a **low-water mark** (e.g. 3). When the visible queue drops below low-water, the agent wakes in the background and refills toward target. Both parameters live in `principles.md`. Manual "show me now" forces an immediate refill cycle. A slow safety-net cron (e.g. hourly) survives only as a heartbeat for the case where the user hasn't opened the app in a long time. **Urgent-senders escalation** (declared in `principles.md`) bypasses the queue and surfaces a card immediately.

The queue cannot exceed target depth by construction. The 1,892-todo failure mode is structurally unavailable.

## 14. Ranking

**Learned-over-features scorer with deterministic floor and forced exploration** (the δ option):

- A small structured scorer evaluates each candidate goal across explicit features (deadline proximity, monetary stakes, sender importance, age, waiting-on-user, reversibility-risk).
- **Weights are learned from swipes** (quick approves raise weight, defers/rejects lower it), clamped so a learned signal can never fully override an explicit principle.
- **Deterministic floor**: `principles.md` reserves slots for high-stakes categories (e.g. "always reserve 2 slots for items with deadline <72h", "always 1 slot for money >£X"). Cold-start works because the floor alone produces a sensible day-one queue.
- **Forced exploration**: a small number of slots (e.g. 2 of 8) are reserved for candidates the model is uncertain about, to prevent filter-bubble collapse.

The ranker is debuggable: every queue position has an honest answer to "why is this here?" expressed in feature scores. The LLM is *not* in the ranking loop — its job is to *generate candidate goals* and *plan their execution*, not to decide which the user sees.

## 15. Credentials

**1Password CLI is the only credential source**, with **structural separation** between processes:

- The **executor process** holds and resolves `op://` references at the moment of use. It never logs the resolved value.
- The **LLM process** (planner and sub-agent reasoning) has no filesystem or network access to credentials, ever. It refers to credentials only by `op://` reference. Information flows one way: credentials → executor → outcome; the LLM sees outcomes only.
- `principles.md` declares which action classes require which `op` scopes. The executor refuses to dispatch any plan whose required scopes exceed what's currently unlocked in `op`. Idle-locking the vault automatically degrades the agent to read-only. This is the brief's *"dynamic permissioning based on context"* in its simplest deterministic form.
- **Virtual payment cards** are `op` items; per-payment-class card pinning lives in `principles.md`. The card issuer's own limits are the second deterministic layer of defence.

The leaked-keys failure mode the brief names is structurally unavailable: the credential is not in the LLM's context because it physically cannot be read by the LLM's process.

## 16. Models

**Phase 1 deferral**: local model deployment is deferred. Both the triage stage and the planner stage use frontier APIs (e.g. cheap model like Haiku for triage, Sonnet/Opus for planning). The deterministic redactor still sits between the two stages so triage outputs are filtered before reaching the planner. The architecture below describes the eventual split; phase 1 just substitutes "local triage model" with "cheap frontier model".

**Cheap-local + expensive-frontier split** (target architecture):

- **Local triage model** (small, fast, e.g. Llama 3.1 8B / Qwen 2.5 7B class via Ollama or llama.cpp) — sees the firehose: every Gmail message, every classification, every feature extraction. The model that touches the most data is the one that never leaves the machine.
- **Frontier planner model** (Claude / GPT-5 / Gemini) — sees only the small redacted slice the local model produced. Used for goal formation, plan synthesis, sub-agent reasoning, judging tone of replies, anything where capability matters and local models still fall short.
- **Deterministic redactor** between the two — *not* an LLM. Strips fields declared sensitive in `principles.md` (account numbers, names not on a known-safe list, regex-matched secrets) before any payload leaves the machine. Auditable as a fixed pipeline.
- **Graceful degradation**: if the frontier API is unreachable or budget-exhausted, the local model alone produces a degraded queue. The product never stops working.
- **Path to fully local**: as local models improve, the planner slot is the only thing that needs swapping. Triage is already local; the redactor is already deterministic. Becoming fully private is a one-component upgrade.

The honest caveat: with the split, the *volume* of data leaving the machine is small and pre-redacted, but it's not zero. Fully zero requires local-only and the current capability hit. The split is the right tradeoff for phase 1.

## 17. Failure & observability

**Append-only decision journal + activity view + post-hoc verifier + meta-cards + replay harness.**

- **Decision journal**: every non-trivial decision (goal selection, plan synthesis, sub-agent dispatch, verification result) appended as JSONL by the *executor*, never by the LLM (so a confused planner can't tamper with it). Records inputs seen, alternatives considered, choice made, reason given, credentials touched (by reference, not value), scopes used.
- **Activity view**: a tab in the local web UI showing the last N executed actions with their reasons, results, and a "this was wrong" button. Flagging an action emits a meta-card: "what should I have done instead?" → rule diff.
- **Post-hoc verifier**: slow background cron that re-checks the agent's own work ("I archived these 47 newsletters yesterday — any replies, bounces, or user un-archives?"). Anomalies become meta-cards. Catches the *unnoticed* failure mode.
- **Meta-cards**: every detected anomaly, every flagged action, every consistent swipe-pattern flows into the same swipe queue as a meta-card. The user is the only path by which the system improves itself, but the system actively *finds* improvements rather than waiting to be told.
- **Replay harness**: a function that takes a journal entry and re-runs the planner on its inputs. Used to validate model upgrades and rule changes against historical decisions before rolling them forward. Direct answer to the brief's *"breaks frequently with updates"*.

This is the first part of the system that adds non-trivial code beyond "files + LLM + small server" — the verifier and replay harness are real components.

## 18. Day-one wow moment

**(a) for first launch, (d) within the first week.**

First-run experience: install → OAuth Gmail → wait ~30 seconds → see a queue of 8 cards including one batched-archive card and 5–7 specific high-stakes goals (a real grant deadline, a real unpaid invoice, a real waiting-on-you reply). Swipe through them. Inbox visibly cleaner. The wow is **recognition**: *"it actually understood what mattered."*

The wow is *judgment*, not capability. Execution wow comes in session 2 or 3, after the user has seen the agent be right repeatedly on lower-stakes calls. The user will not approve a card that pays an invoice on first run, no matter how good the agent is.

**Build sequencing implication**: build inbox-in / queue-out **before any execution**. Skip sending, drafting, paying entirely for the first week. Prove the queue is right. Execution capabilities accrete after the queue earns trust.

**The single biggest technical risk** is whether the local triage model is good enough on day one to extract `{deadline, amount, waiting_on_user, sender_known}` from raw Gmail messages reliably. If it's not, the queue is junk and the wow doesn't fire. **First thing to build and benchmark, before any UI:** "can a small local model reliably extract those features from real email?" If no, the planner has to do triage too and the cost/privacy tradeoff in §16 shifts.

---

## Architecture summary

```
                        ┌─────────────────────────┐
                        │       Rules files        │
                        │  principles.md           │
                        │  gmail.md, calendar.md…  │
                        └────────────┬─────────────┘
                                     │ (read by everything)
                                     ▼
┌──────────────┐    queries    ┌─────────────┐    candidate goals    ┌──────────────┐
│    Gmail     │◀──────────────│  Local      │──────────────────────▶│  Redactor    │
│  (search)    │               │  triage     │                        │ (deterministic)│
└──────────────┘──messages────▶│  model      │                        └──────┬───────┘
                                └─────────────┘                                │
                                                                               ▼
                                                                       ┌──────────────┐
                                                                       │  Frontier    │
                                                                       │  planner     │
                                                                       │  (LLM)       │
                                                                       └──────┬───────┘
                                                                              │ plans
                                                                              ▼
                  ┌──────────────────────────────────────────────────┐
                  │                  Executor                         │
                  │  ─ resolves op:// references                      │
                  │  ─ enforces principles.md (transport blacklist,   │
                  │      reversibility halts, scope checks)           │
                  │  ─ writes decision journal                        │
                  │  ─ dispatches to sub-agents                       │
                  └──┬──────────────┬─────────────┬─────────────┬─────┘
                     │              │             │             │
                     ▼              ▼             ▼             ▼
               ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
               │  Gmail   │  │ Browser  │  │ Calendar │  │   …      │
               │ sub-agent│  │ sub-agent│  │ sub-agent│  │          │
               └──────────┘  └──────────┘  └──────────┘  └──────────┘
                     │
                     │ structured outcome
                     ▼
               ┌──────────────┐
               │ Verification │  ─ re-read state, confirm match
               └──────┬───────┘
                      │
                      ▼
               ┌──────────────────────────────────┐      ┌──────────────┐
               │   Queue (target N, low-water M)  │◀─────│  Ranker      │
               │   ─ deterministic floor slots    │      │  (features + │
               │   ─ exploration slots            │      │  learned     │
               │   ─ ranked main slots            │      │  weights)    │
               └──────────────┬───────────────────┘      └──────────────┘
                              │ HTTP / websocket
                              ▼
                  ┌────────────────────────────┐
                  │  Local HTTP API            │
                  └──┬──────────┬──────────┬───┘
                     ▼          ▼          ▼
                  ┌─────┐    ┌─────┐    ┌─────┐
                  │ Web │    │ TUI │    │ iOS │
                  └─────┘    └─────┘    └─────┘
                                  ▲
                                  │ Tailscale (no public surface)
```

**Three pillars, all human-readable:**
1. Rules files (`principles.md`, `gmail.md`, …)
2. In-flight state (`state/in-flight/`, `state/done/`)
3. Capability code (sub-agent implementations)

**One strict separation**: the LLM process never holds credentials. The executor does. Information flows one way.

---

## Phase 1 build order

1. **Local triage benchmark** — can a small local model extract `{deadline, amount, waiting_on_user, sender_known}` from real email reliably? Decides whether the architecture survives intact.
2. **Gmail OAuth + authenticated CLI** for read/search.
3. **Executor + decision journal** (write-only at this stage; nothing irreversible runs yet).
4. **Local triage model integration** + redactor + frontier planner for goal formation.
5. **Ranker** (deterministic floor first, learned weights deferred until swipe data exists).
6. **Queue + local HTTP API** (target/low-water, urgent escalation).
7. **Web client** for swiping. End of phase 1a — read-only triage product.
8. **Gmail sub-agent for reversible actions** (archive, label, draft). Still no send/pay.
9. **Verification step** + meta-cards from post-hoc verifier.
10. **Rule promotion mechanism** (meta-cards that emit rule diffs).
11. **Irreversible-step halts** + first send-email capability behind reversibility gate.
12. End of phase 1.

Phase 2 begins when phase 1 is reliably good: terminal client, iOS client, browser sub-agent, virtual-card payments, calendar, the long tail of capabilities the user finds they want.
