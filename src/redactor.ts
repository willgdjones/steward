import type { GmailMessage } from './gmail/fake.js';
import type { RedactionRule } from './rules.js';

/**
 * Deterministic, non-LLM redactor. The base `redact` function strips the
 * message body and reduces `from` to its domain. `applyRedactionRules`
 * applies additional field-level and regex rules from principles.md.
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

/**
 * Apply rule-driven redaction on top of the base redaction.
 * - Rules with no pattern replace the entire field with [REDACTED].
 * - Rules with a pattern replace all regex matches with [REDACTED].
 * Returns a new object; does not mutate the input.
 */
export function applyRedactionRules(
  message: RedactedMessage,
  rules: RedactionRule[],
): RedactedMessage {
  if (rules.length === 0) return message;

  const result: Record<string, unknown> = { ...message };

  for (const rule of rules) {
    const field = rule.field as keyof RedactedMessage;
    if (!(field in result) || field === 'id') continue;

    const value = result[field];
    if (typeof value !== 'string') continue;

    if (rule.pattern) {
      result[field] = value.replace(new RegExp(rule.pattern, 'g'), '[REDACTED]');
    } else {
      result[field] = '[REDACTED]';
    }
  }

  return result as unknown as RedactedMessage;
}
