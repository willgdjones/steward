import type { GmailMessage } from './gmail/fake.js';
import type { TriageResult } from './triage.js';

export interface TriagedCandidate {
  message: GmailMessage;
  result: TriageResult;
}

export interface Cluster {
  domain: string;
  category: string;
  candidates: TriagedCandidate[];
}

/**
 * Extract the sender domain from an email address.
 */
function senderDomain(from: string): string {
  const at = from.lastIndexOf('@');
  return at >= 0 ? from.slice(at + 1).toLowerCase() : from.toLowerCase();
}

/**
 * Group triaged candidates by (sender domain, category).
 * Returns clusters that meet the batch threshold alongside
 * the remaining unclustered candidates.
 */
export function clusterCandidates(
  candidates: TriagedCandidate[],
  batchThreshold: number,
): { batches: Cluster[]; remaining: TriagedCandidate[] } {
  const groups = new Map<string, TriagedCandidate[]>();

  for (const c of candidates) {
    const domain = senderDomain(c.message.from);
    const category = c.result.features.category;
    const key = `${domain}::${category}`;
    const group = groups.get(key) ?? [];
    group.push(c);
    groups.set(key, group);
  }

  const batches: Cluster[] = [];
  const remaining: TriagedCandidate[] = [];

  for (const [key, group] of groups) {
    if (group.length >= batchThreshold) {
      const [domain, category] = key.split('::');
      batches.push({ domain, category, candidates: group });
    } else {
      remaining.push(...group);
    }
  }

  return { batches, remaining };
}
