import type { BlacklistEntry } from './rules.js';

export interface GateResult {
  allowed: boolean;
  reason?: string;
}

/**
 * Deterministic blacklist check. Returns denied with a reason if the
 * (transport, action) pair appears in the blacklist.
 */
export function checkBlacklist(
  blacklist: BlacklistEntry[],
  transport: string,
  action: string,
): GateResult {
  const t = transport.toLowerCase();
  const a = action.toLowerCase();
  const match = blacklist.find(
    (e) => e.transport.toLowerCase() === t && e.action.toLowerCase() === a,
  );
  if (match) {
    return { allowed: false, reason: `Blacklisted: (${match.transport}, ${match.action})` };
  }
  return { allowed: true };
}
