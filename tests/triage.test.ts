import { describe, it, expect } from 'vitest';
import { defaultTriageResult, type TriageResult } from '../src/triage.js';

describe('triage', () => {
  it('defaultTriageResult returns safe defaults', () => {
    const result = defaultTriageResult();
    expect(result.features.deadline).toBeNull();
    expect(result.features.amount).toBeNull();
    expect(result.features.waiting_on_user).toBe(false);
    expect(result.features.category).toBe('other');
    expect(result.features.urgency).toBe('low');
    expect(result.snippet).toBe('No triage available.');
  });

  it('TriageResult shape is correct', () => {
    const result: TriageResult = {
      features: {
        deadline: '2026-04-15',
        amount: '£50.00',
        waiting_on_user: true,
        category: 'work',
        urgency: 'high',
      },
      snippet: 'Invoice due next week.',
    };
    expect(result.features.deadline).toBe('2026-04-15');
    expect(result.features.amount).toBe('£50.00');
    expect(result.features.waiting_on_user).toBe(true);
    expect(result.features.category).toBe('work');
    expect(result.features.urgency).toBe('high');
    expect(result.snippet).toBe('Invoice due next week.');
  });
});
