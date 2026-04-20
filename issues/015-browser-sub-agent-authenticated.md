## Parent PRD

`design.md`

## What to build

Extend the browser sub-agent to handle authenticated sessions, while still avoiding irreversible writes.

- Browser sub-agent uses `op://` references to log in to a site (1Password CLI fills credentials at the moment of use)
- Resolved credentials never appear in screenshots, DOM dumps, or sub-agent logs — the redactor pipeline must cover browser artefacts as well as text payloads
- Reversible authenticated actions only: navigate authenticated pages, extract data, fill (but not submit) forms
- Verification confirms the extracted data
- HITL because credential handling in browser contexts is the highest-risk leakage surface; the user must review the redactor's coverage of browser artefacts before merge

See §6, §15 of the PRD.

## Acceptance criteria

- [ ] Browser sub-agent can resolve `op://` references at the moment of login
- [ ] Resolved credentials never appear in any artefact written to disk or sent to the LLM process
- [ ] Redactor extended to cover browser screenshots and DOM dumps
- [ ] Reversible-only restriction still enforced
- [ ] User has reviewed the redactor's coverage

## Blocked by

- Blocked by `issues/014-browser-sub-agent-read-only.md`

## User stories addressed

- Decision §6 (transport selection)
- Decision §15 (credentials, structural separation)
