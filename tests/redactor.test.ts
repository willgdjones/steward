import { describe, it, expect } from 'vitest';
import { redact, applyRedactionRules } from '../src/redactor.js';
import type { RedactionRule } from '../src/rules.js';

describe('redact (base behavior)', () => {
  it('drops the body and reduces from to a domain', () => {
    const r = redact({
      id: 'm1',
      from: 'alice@example.com',
      subject: 'hi',
      body: 'secret',
      unread: true,
    });
    expect(r).toEqual({ id: 'm1', fromDomain: 'example.com', subject: 'hi' });
    expect((r as unknown as Record<string, unknown>).body).toBeUndefined();
  });
});

describe('applyRedactionRules', () => {
  it('drops a named field entirely when no pattern is given', () => {
    const rules: RedactionRule[] = [{ field: 'subject' }];
    const result = applyRedactionRules(
      { id: 'm1', fromDomain: 'example.com', subject: 'sensitive' },
      rules,
    );
    expect(result.subject).toBe('[REDACTED]');
  });

  it('replaces regex matches in a field when a pattern is given', () => {
    const rules: RedactionRule[] = [
      { field: 'subject', pattern: '\\d{4}[- ]?\\d{4}' },
    ];
    const result = applyRedactionRules(
      { id: 'm1', fromDomain: 'example.com', subject: 'card 1234-5678 info' },
      rules,
    );
    expect(result.subject).toBe('card [REDACTED] info');
  });

  it('applies multiple rules in order', () => {
    const rules: RedactionRule[] = [
      { field: 'subject', pattern: '\\d+' },
      { field: 'fromDomain' },
    ];
    const result = applyRedactionRules(
      { id: 'm1', fromDomain: 'bank.com', subject: 'invoice 42 payment' },
      rules,
    );
    expect(result.subject).toBe('invoice [REDACTED] payment');
    expect(result.fromDomain).toBe('[REDACTED]');
  });

  it('does nothing when rules are empty', () => {
    const input = { id: 'm1', fromDomain: 'example.com', subject: 'hello' };
    const result = applyRedactionRules(input, []);
    expect(result).toEqual(input);
  });

  it('ignores rules targeting fields not present on the message', () => {
    const rules: RedactionRule[] = [{ field: 'nonexistent' }];
    const input = { id: 'm1', fromDomain: 'example.com', subject: 'hello' };
    const result = applyRedactionRules(input, rules);
    expect(result).toEqual(input);
  });
});
