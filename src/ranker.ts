import type { TriageFeatures } from './triage.js';
import type { FloorReservation } from './rules.js';

export interface RankedCandidate {
  messageId: string;
  features: TriageFeatures;
  /** Lower = higher priority. */
  score: number;
  /** Whether this candidate filled a floor-reserved slot. */
  floor: boolean;
}

/**
 * Check if a candidate matches a floor reservation condition.
 * Currently supports: deadline_within_hours (checks features.deadline).
 */
export function matchesFloor(
  features: TriageFeatures,
  match: Record<string, unknown>,
  now: Date = new Date(),
): boolean {
  if (typeof match.deadline_within_hours === 'number' && features.deadline) {
    const deadline = new Date(features.deadline);
    const hoursUntil = (deadline.getTime() - now.getTime()) / (1000 * 60 * 60);
    return hoursUntil >= 0 && hoursUntil <= match.deadline_within_hours;
  }
  if (typeof match.category === 'string') {
    return features.category === match.category;
  }
  if (typeof match.urgency === 'string') {
    return features.urgency === match.urgency;
  }
  return false;
}

/**
 * Score a candidate for the age/recency tiebreaker.
 * Higher urgency → lower score (higher priority).
 * Within same urgency, earlier items (lower index) win.
 */
function urgencyScore(features: TriageFeatures): number {
  const urgencyMap = { high: 0, medium: 1, low: 2 };
  return urgencyMap[features.urgency] ?? 2;
}

export interface RankInput {
  messageId: string;
  features: TriageFeatures;
}

/**
 * Rank candidates with deterministic floor reservations, then urgency tiebreaker.
 * Returns at most `targetDepth` candidates.
 *
 * Algorithm:
 * 1. For each floor reservation, find matching candidates and reserve up to `slots` for them.
 * 2. Fill remaining slots with non-floor candidates sorted by urgency tiebreaker.
 * 3. Cap at targetDepth.
 */
export function rankCandidates(
  candidates: RankInput[],
  floor: FloorReservation[],
  targetDepth: number,
  now: Date = new Date(),
): RankedCandidate[] {
  const result: RankedCandidate[] = [];
  const used = new Set<string>();

  // Step 1: Fill floor-reserved slots
  for (const reservation of floor) {
    let filled = 0;
    for (const candidate of candidates) {
      if (used.has(candidate.messageId)) continue;
      if (filled >= reservation.slots) break;
      if (matchesFloor(candidate.features, reservation.match, now)) {
        result.push({
          messageId: candidate.messageId,
          features: candidate.features,
          score: result.length,
          floor: true,
        });
        used.add(candidate.messageId);
        filled++;
      }
    }
  }

  // Step 2: Fill remaining slots with urgency tiebreaker
  const remaining = candidates
    .filter((c) => !used.has(c.messageId))
    .sort((a, b) => urgencyScore(a.features) - urgencyScore(b.features));

  for (const candidate of remaining) {
    if (result.length >= targetDepth) break;
    result.push({
      messageId: candidate.messageId,
      features: candidate.features,
      score: result.length,
      floor: false,
    });
  }

  // Cap at targetDepth (floor slots might have already exceeded it)
  return result.slice(0, targetDepth);
}
