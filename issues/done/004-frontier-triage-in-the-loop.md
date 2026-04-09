## Parent PRD

`design.md`

## What to build

Replace the trivial goal generation from slice 002 with the real two-stage pipeline:

- Executor queries Gmail directly via the authenticated CLI/API search (no local index)
- For each candidate message, a **cheap frontier model** (e.g. Haiku) extracts structured features
- The **deterministic redactor** strips sensitive fields
- An **expensive frontier model** (Sonnet/Opus) receives the small redacted slice and produces real goals as free-form descriptions

Phase 1 uses frontier APIs for both stages per the §16 deferral; the local-triage variant is target architecture, not a phase-1 requirement. The two-stage split is preserved so the local model can drop in later without re-architecting.

The card the user sees should now reflect a real Gmail message with a real reason. No execution still — the goal is rendered, swiped, journalled, but no action runs.

See §10, §16 of the PRD.

## Acceptance criteria

- [ ] Executor queries Gmail with a configurable search string; no local index involved
- [ ] Cheap frontier model runs the triage stage and outputs structured features
- [ ] Redactor sits between triage and planner stages
- [ ] Expensive frontier model produces a free-form goal description with a `reason`
- [ ] First-run on a real inbox produces at least one sensible goal

## Blocked by

- Blocked by `issues/003-rules-files-principles-gate-redactor.md`

## User stories addressed

- Decision §10 (Gmail as the index)
- Decision §16 (cheap-local + expensive-frontier split)
