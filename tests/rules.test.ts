import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { mkdtempSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { loadRules, watchRules, type Rules } from '../src/rules.js';

describe('loadRules', () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), 'steward-rules-'));
  });

  it('loads blacklist and redaction rules from principles.md', () => {
    writeFileSync(
      join(dir, 'principles.md'),
      `blacklist:
  - transport: gmail
    action: send

redaction:
  - field: body
  - field: subject
    pattern: "\\\\d{4}[- ]?\\\\d{4}"
`,
    );
    writeFileSync(join(dir, 'gmail.md'), '');

    const rules = loadRules(dir);
    expect(rules.blacklist).toEqual([{ transport: 'gmail', action: 'send' }]);
    expect(rules.redaction).toHaveLength(2);
    expect(rules.redaction[0]).toEqual({ field: 'body' });
    expect(rules.redaction[1]).toMatchObject({ field: 'subject', pattern: '\\d{4}[- ]?\\d{4}' });
  });

  it('returns empty rules when files do not exist', () => {
    const rules = loadRules(dir);
    expect(rules.blacklist).toEqual([]);
    expect(rules.redaction).toEqual([]);
  });

  it('returns empty rules when files are empty', () => {
    writeFileSync(join(dir, 'principles.md'), '');
    writeFileSync(join(dir, 'gmail.md'), '');
    const rules = loadRules(dir);
    expect(rules.blacklist).toEqual([]);
    expect(rules.redaction).toEqual([]);
  });
});

describe('watchRules', () => {
  let dir: string;
  let stop: () => void;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), 'steward-watch-'));
    writeFileSync(join(dir, 'principles.md'), '');
    writeFileSync(join(dir, 'gmail.md'), '');
  });

  afterEach(() => {
    stop?.();
  });

  it('reloads rules when a file changes', async () => {
    const versions: Rules[] = [];
    const watcher = watchRules(dir, (r) => versions.push(r));
    stop = watcher.stop;

    // Write a blacklist rule
    writeFileSync(
      join(dir, 'principles.md'),
      `blacklist:
  - transport: gmail
    action: delete
`,
    );

    // fs.watch is async; poll until the callback fires
    for (let i = 0; i < 20 && versions.length === 0; i++) {
      await new Promise((r) => setTimeout(r, 100));
    }

    expect(versions.length).toBeGreaterThanOrEqual(1);
    const latest = versions[versions.length - 1];
    expect(latest.blacklist).toEqual([{ transport: 'gmail', action: 'delete' }]);
  });
});
