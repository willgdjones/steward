## Parent PRD

`design.md`

## What to build

Introduce the three deterministic layers that gate everything the LLM produces, even if they start mostly empty:

- **Rules files**: load `principles.md` and `gmail.md` from disk on executor startup. Both are plain markdown / YAML the user owns.
- **Principles gate**: the executor enforces a `(transport, action_class)` blacklist declared in `principles.md` before dispatching any action. The blacklist starts empty.
- **Redactor**: a deterministic (non-LLM) pipeline that strips fields declared in `principles.md` from any payload before it leaves the executor for the frontier planner. Redaction rules start empty.

End-to-end test: add a junk rule to the blacklist, watch the executor refuse to dispatch the matching action; add a regex to the redactor, watch the matching field disappear from what the planner sees.

See §3, §4, §6 (blacklist), §16 (redactor) of the PRD.

## Acceptance criteria

- [ ] `principles.md` and `gmail.md` loaded at startup; reload on file change
- [ ] Executor enforces blacklist before dispatch and refuses blacklisted actions deterministically
- [ ] Redactor pipeline applies regex / field rules to outgoing payloads
- [ ] All three layers covered by tests with synthetic rules

## Blocked by

- Blocked by `issues/002-end-to-end-skeleton.md`

## User stories addressed

- Decision §3 (rules promotion mechanism, prerequisites)
- Decision §4 (rules layout)
- Decision §6 (transport blacklist)
- Decision §16 (deterministic redactor)
