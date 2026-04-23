## Parent PRD

`design.md`

## What to build

A replay harness over the decision journal, used to validate model upgrades and rule changes against historical decisions before rolling them forward.

- Function takes a journal entry and re-runs the planner on its inputs
- Compares the new decision against the historical one and flags divergences
- Used as a regression test suite when bumping the local triage model, the frontier planner, or the rules files
- Output is a diff report: "for these N historical entries, the new system would have made a different choice; here are the diffs"
- Direct answer to the brief's "breaks frequently with updates"

Phase 2 because it's only valuable once a real journal exists with enough entries to be informative.

See §17 of the PRD.

## Acceptance criteria

- [ ] Replay harness re-runs the planner on a journal entry's inputs
- [ ] Diff report shows divergences between new and historical decisions
- [ ] CLI: `steward replay --since=<date> --against=<new-config>` produces the diff
- [ ] Used at least once to validate a real model or rule change

## Blocked by

- Blocked by `issues/012-send-email-1password-cli.md`

## User stories addressed

- Decision §17 (replay harness for safe upgrades)
