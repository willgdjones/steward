import { describe, it, expect } from 'vitest';
import { checkBlacklist } from '../src/principlesGate.js';
import type { BlacklistEntry } from '../src/rules.js';

describe('principlesGate', () => {
  const blacklist: BlacklistEntry[] = [
    { transport: 'gmail', action: 'send' },
    { transport: 'calendar', action: 'delete' },
  ];

  it('blocks a matching (transport, action) pair', () => {
    const result = checkBlacklist(blacklist, 'gmail', 'send');
    expect(result.allowed).toBe(false);
    expect(result.reason).toContain('gmail');
    expect(result.reason).toContain('send');
  });

  it('allows a non-matching pair', () => {
    const result = checkBlacklist(blacklist, 'gmail', 'archive');
    expect(result.allowed).toBe(true);
  });

  it('allows anything when the blacklist is empty', () => {
    const result = checkBlacklist([], 'gmail', 'send');
    expect(result.allowed).toBe(true);
  });

  it('matches are case-insensitive', () => {
    const result = checkBlacklist(blacklist, 'Gmail', 'Send');
    expect(result.allowed).toBe(false);
  });
});
