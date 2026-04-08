import type { GmailMessage } from './gmail/fake.js';

/**
 * Deterministic, non-LLM redactor. Slice 002 only strips the message
 * body and reduces `from` to its domain so the planner sees the smallest
 * useful slice. Issue 003 generalises this to a rule-driven pipeline.
 */
export interface RedactedMessage {
  id: string;
  fromDomain: string;
  subject: string;
}

export function redact(message: GmailMessage): RedactedMessage {
  const at = message.from.lastIndexOf('@');
  const fromDomain = at >= 0 ? message.from.slice(at + 1) : message.from;
  return { id: message.id, fromDomain, subject: message.subject };
}
