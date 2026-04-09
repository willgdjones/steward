import { describe, it, expect } from 'vitest';
import { detectPromotions, type Promotion } from '../src/promoter.js';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { appendJournal } from '../src/journal.js';

function tmpDir() {
  return mkdtempSync(join(tmpdir(), 'steward-promoter-'));
}

describe('promoter — detectPromotions', () => {
  it('returns empty when journal has no action entries', () => {
    const dir = tmpDir();
    const journalPath = join(dir, 'journal.jsonl');
    appendJournal(journalPath, { kind: 'decision', decision: 'defer', goalId: 'g1', messageId: 'm1' });

    const promotions = detectPromotions(journalPath, { threshold: 3, cooldown_minutes: 1440 });
    expect(promotions).toEqual([]);
  });

  it('proposes a rule when threshold is reached for a sender domain + action', () => {
    const dir = tmpDir();
    const journalPath = join(dir, 'journal.jsonl');
    // 3 consecutive archive actions from substack.com
    for (let i = 0; i < 3; i++) {
      appendJournal(journalPath, {
        kind: 'action',
        goalId: `g${i}`,
        messageId: `m${i}`,
        title: `Archive newsletter from substack.com`,
        senderDomain: 'substack.com',
        action: 'archive',
        transport: 'gmail',
        outcomes: [{ success: true, action_taken: 'archive', messageId: `m${i}` }],
        verification: { verified: true, sample: [] },
      });
    }

    const promotions = detectPromotions(journalPath, { threshold: 3, cooldown_minutes: 1440 });
    expect(promotions).toHaveLength(1);
    expect(promotions[0]).toMatchObject({
      patternKey: 'gmail::archive::substack.com',
      senderDomain: 'substack.com',
      action: 'archive',
      transport: 'gmail',
      count: 3,
    });
    expect(promotions[0].proposedRule).toContain('substack.com');
    expect(promotions[0].proposedRule).toContain('archive');
  });

  it('does not propose when below threshold', () => {
    const dir = tmpDir();
    const journalPath = join(dir, 'journal.jsonl');
    for (let i = 0; i < 2; i++) {
      appendJournal(journalPath, {
        kind: 'action',
        goalId: `g${i}`,
        messageId: `m${i}`,
        senderDomain: 'substack.com',
        action: 'archive',
        transport: 'gmail',
      });
    }

    const promotions = detectPromotions(journalPath, { threshold: 3, cooldown_minutes: 1440 });
    expect(promotions).toEqual([]);
  });

  it('skips patterns that have already been promoted', () => {
    const dir = tmpDir();
    const journalPath = join(dir, 'journal.jsonl');
    for (let i = 0; i < 5; i++) {
      appendJournal(journalPath, {
        kind: 'action',
        goalId: `g${i}`,
        messageId: `m${i}`,
        senderDomain: 'substack.com',
        action: 'archive',
        transport: 'gmail',
      });
    }
    // Already promoted
    appendJournal(journalPath, {
      kind: 'rule_promoted',
      patternKey: 'gmail::archive::substack.com',
    });

    const promotions = detectPromotions(journalPath, { threshold: 3, cooldown_minutes: 1440 });
    expect(promotions).toEqual([]);
  });

  it('skips patterns within the cooldown window after rejection', () => {
    const dir = tmpDir();
    const journalPath = join(dir, 'journal.jsonl');
    for (let i = 0; i < 5; i++) {
      appendJournal(journalPath, {
        kind: 'action',
        goalId: `g${i}`,
        messageId: `m${i}`,
        senderDomain: 'substack.com',
        action: 'archive',
        transport: 'gmail',
      });
    }
    // Rejected recently
    appendJournal(journalPath, {
      kind: 'promotion_rejected',
      patternKey: 'gmail::archive::substack.com',
    });

    const promotions = detectPromotions(journalPath, { threshold: 3, cooldown_minutes: 1440 });
    expect(promotions).toEqual([]);
  });

  it('re-proposes after cooldown window expires', () => {
    const dir = tmpDir();
    const journalPath = join(dir, 'journal.jsonl');
    for (let i = 0; i < 5; i++) {
      appendJournal(journalPath, {
        kind: 'action',
        goalId: `g${i}`,
        messageId: `m${i}`,
        senderDomain: 'substack.com',
        action: 'archive',
        transport: 'gmail',
      });
    }
    // Rejected long ago (cooldown expired)
    appendJournal(journalPath, {
      kind: 'promotion_rejected',
      patternKey: 'gmail::archive::substack.com',
      // Override ts to be old
    });
    // Manually patch the last line to have an old timestamp
    const fs = require('node:fs');
    const lines = fs.readFileSync(journalPath, 'utf8').trim().split('\n');
    const lastEntry = JSON.parse(lines[lines.length - 1]);
    lastEntry.ts = new Date(Date.now() - 1440 * 60 * 1000 - 1000).toISOString();
    lines[lines.length - 1] = JSON.stringify(lastEntry);
    fs.writeFileSync(journalPath, lines.join('\n') + '\n');

    const promotions = detectPromotions(journalPath, { threshold: 3, cooldown_minutes: 1440 });
    expect(promotions).toHaveLength(1);
    expect(promotions[0].patternKey).toBe('gmail::archive::substack.com');
  });

  it('groups by different sender domains independently', () => {
    const dir = tmpDir();
    const journalPath = join(dir, 'journal.jsonl');
    // 3 from substack.com, 3 from medium.com
    for (let i = 0; i < 3; i++) {
      appendJournal(journalPath, {
        kind: 'action',
        goalId: `gs${i}`,
        messageId: `ms${i}`,
        senderDomain: 'substack.com',
        action: 'archive',
        transport: 'gmail',
      });
      appendJournal(journalPath, {
        kind: 'action',
        goalId: `gm${i}`,
        messageId: `mm${i}`,
        senderDomain: 'medium.com',
        action: 'archive',
        transport: 'gmail',
      });
    }

    const promotions = detectPromotions(journalPath, { threshold: 3, cooldown_minutes: 1440 });
    expect(promotions).toHaveLength(2);
    const keys = promotions.map((p) => p.patternKey).sort();
    expect(keys).toEqual(['gmail::archive::medium.com', 'gmail::archive::substack.com']);
  });
});
