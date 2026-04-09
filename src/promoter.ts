import { readJournal, type JournalEntry } from './journal.js';

export interface PromotionConfig {
  /** Number of approved actions needed before proposing a rule. Defaults to 5. */
  threshold: number;
  /** Minutes to wait after a rejection before re-proposing. Defaults to 1440 (24h). */
  cooldown_minutes: number;
}

export interface Promotion {
  patternKey: string;
  senderDomain: string;
  action: string;
  transport: string;
  count: number;
  /** The proposed rule text to add to gmail.md. */
  proposedRule: string;
}

/**
 * Scan the journal for repeated action patterns and propose standing rules.
 *
 * Groups action entries by (transport, action, senderDomain).
 * When a group hits the threshold, returns a Promotion unless:
 * - The pattern was already promoted (kind: 'rule_promoted')
 * - The pattern was rejected within the cooldown window (kind: 'promotion_rejected')
 */
export function detectPromotions(
  journalPath: string,
  config: PromotionConfig,
): Promotion[] {
  const entries = readJournal(journalPath);
  const now = Date.now();

  // Collect already-promoted pattern keys
  const promotedKeys = new Set<string>();
  // Collect rejection timestamps per pattern key
  const rejectionTimes = new Map<string, number>();

  for (const e of entries) {
    if (e.kind === 'rule_promoted' && typeof e.patternKey === 'string') {
      promotedKeys.add(e.patternKey);
    }
    if (e.kind === 'promotion_rejected' && typeof e.patternKey === 'string') {
      const ts = new Date(e.ts).getTime();
      const existing = rejectionTimes.get(e.patternKey);
      if (!existing || ts > existing) {
        rejectionTimes.set(e.patternKey, ts);
      }
    }
  }

  // Count action entries by pattern key
  const counts = new Map<string, { count: number; senderDomain: string; action: string; transport: string }>();

  for (const e of entries) {
    if (e.kind !== 'action') continue;
    const transport = typeof e.transport === 'string' ? e.transport : 'gmail';
    const action = typeof e.action === 'string' ? e.action : (typeof e.title === 'string' && (e.title as string).toLowerCase().startsWith('archive') ? 'archive' : 'unknown');
    const senderDomain = typeof e.senderDomain === 'string' ? e.senderDomain : null;
    if (!senderDomain) continue;

    const key = `${transport}::${action}::${senderDomain}`;
    const existing = counts.get(key);
    if (existing) {
      existing.count++;
    } else {
      counts.set(key, { count: 1, senderDomain, action, transport });
    }
  }

  const promotions: Promotion[] = [];

  for (const [key, data] of counts) {
    if (data.count < config.threshold) continue;
    if (promotedKeys.has(key)) continue;

    // Check cooldown
    const rejectedAt = rejectionTimes.get(key);
    if (rejectedAt) {
      const cooldownMs = config.cooldown_minutes * 60 * 1000;
      if (now - rejectedAt < cooldownMs) continue;
    }

    const proposedRule = `- sender: "*@${data.senderDomain}"\n  action: ${data.action}\n  auto: true`;

    promotions.push({
      patternKey: key,
      senderDomain: data.senderDomain,
      action: data.action,
      transport: data.transport,
      count: data.count,
      proposedRule,
    });
  }

  return promotions;
}
