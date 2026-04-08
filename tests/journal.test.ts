import { describe, it, expect } from 'vitest';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { appendJournal } from '../src/journal.js';

describe('journal', () => {
  it('appends an entry as a JSONL line with timestamp', () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-'));
    const path = join(dir, 'journal.jsonl');
    const entry = appendJournal(path, { kind: 'approve', goalId: 'g1' });
    expect(entry.ts).toMatch(/T.*Z$/);
    const lines = readFileSync(path, 'utf8').trim().split('\n');
    expect(lines).toHaveLength(1);
    const parsed = JSON.parse(lines[0]);
    expect(parsed.kind).toBe('approve');
    expect(parsed.goalId).toBe('g1');
  });

  it('appends multiple entries without overwriting', () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-'));
    const path = join(dir, 'journal.jsonl');
    appendJournal(path, { kind: 'a' });
    appendJournal(path, { kind: 'b' });
    const lines = readFileSync(path, 'utf8').trim().split('\n');
    expect(lines).toHaveLength(2);
  });
});
