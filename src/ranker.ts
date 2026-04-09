import type { TriageFeatures } from './triage.js';
import type { FloorReservation } from './rules.js';
import type { JournalEntry } from './journal.js';

/** Normalised feature vector for scoring. All values in [0, 1]. */
export interface FeatureVector {
  deadline_proximity: number;
  has_amount: number;
  waiting_on_user: number;
  urgency: number;
}

const FEATURE_KEYS: (keyof FeatureVector)[] = ['deadline_proximity', 'has_amount', 'waiting_on_user', 'urgency'];

/** Per-feature weights used by the scorer. */
export type FeatureWeights = Record<keyof FeatureVector, number>;

/** Min/max bounds for weight clamping. */
export const WEIGHT_BOUNDS: Record<keyof FeatureVector, { min: number; max: number }> = {
  deadline_proximity: { min: 0.1, max: 3.0 },
  has_amount: { min: 0.1, max: 3.0 },
  waiting_on_user: { min: 0.1, max: 3.0 },
  urgency: { min: 0.2, max: 3.0 },
};

export const DEFAULT_WEIGHTS: FeatureWeights = {
  deadline_proximity: 1.0,
  has_amount: 0.5,
  waiting_on_user: 0.8,
  urgency: 1.0,
};

/** Extract a normalised feature vector from triage features. */
export function extractFeatureVector(features: TriageFeatures, now: Date = new Date()): FeatureVector {
  // Deadline proximity: 1.0 if <= 24h, linear decay to 0 at 168h (7 days), 0 if no deadline
  let deadline_proximity = 0;
  if (features.deadline) {
    const deadline = new Date(features.deadline);
    const hoursUntil = (deadline.getTime() - now.getTime()) / (1000 * 60 * 60);
    if (hoursUntil >= 0 && hoursUntil <= 168) {
      deadline_proximity = Math.max(0, 1 - hoursUntil / 168);
    }
  }

  const urgencyMap: Record<string, number> = { high: 1.0, medium: 0.5, low: 0.0 };

  return {
    deadline_proximity,
    has_amount: features.amount != null ? 1 : 0,
    waiting_on_user: features.waiting_on_user ? 1 : 0,
    urgency: urgencyMap[features.urgency] ?? 0,
  };
}

/** Compute a weighted score for a feature vector. Higher = more important. */
export function scoreCandidate(fv: FeatureVector, weights: FeatureWeights): number {
  let total = 0;
  for (const key of FEATURE_KEYS) {
    total += fv[key] * weights[key];
  }
  return total;
}

/** Per-feature score breakdown. */
export interface ScoreBreakdown {
  deadline_proximity: number;
  has_amount: number;
  waiting_on_user: number;
  urgency: number;
  total: number;
}

function computeBreakdown(fv: FeatureVector, weights: FeatureWeights): ScoreBreakdown {
  const breakdown: Record<string, number> = {};
  let total = 0;
  for (const key of FEATURE_KEYS) {
    const contribution = fv[key] * weights[key];
    breakdown[key] = contribution;
    total += contribution;
  }
  return { ...breakdown, total } as ScoreBreakdown;
}

/**
 * Learn weights from swipe history in journal entries.
 * Approvals increase weights for features that were present;
 * rejections/defers decrease them. Weights are clamped.
 */
export function learnWeights(entries: JournalEntry[]): FeatureWeights {
  const weights = { ...DEFAULT_WEIGHTS };

  // Filter to decision entries that have features
  const decisions = entries.filter(
    (e) => e.kind === 'decision' && (e.decision === 'approve' || e.decision === 'reject' || e.decision === 'defer') && e.features,
  );

  if (decisions.length === 0) return weights;

  const learningRate = 0.05;

  for (const entry of decisions) {
    const rawFeatures = entry.features as Record<string, unknown>;
    const triageFeatures: TriageFeatures = {
      deadline: (rawFeatures.deadline as string) ?? null,
      amount: (rawFeatures.amount as string) ?? null,
      waiting_on_user: rawFeatures.waiting_on_user === true,
      category: String(rawFeatures.category ?? 'other'),
      urgency: (rawFeatures.urgency as 'high' | 'medium' | 'low') ?? 'low',
    };
    const fv = extractFeatureVector(triageFeatures);
    const direction = entry.decision === 'approve' ? 1 : -1;

    for (const key of FEATURE_KEYS) {
      if (fv[key] > 0) {
        weights[key] += learningRate * direction * fv[key];
      }
    }
  }

  // Clamp
  for (const key of FEATURE_KEYS) {
    weights[key] = Math.max(WEIGHT_BOUNDS[key].min, Math.min(WEIGHT_BOUNDS[key].max, weights[key]));
  }

  return weights;
}

export interface RankedCandidate {
  messageId: string;
  features: TriageFeatures;
  /** Lower = higher priority (position in queue). */
  score: number;
  /** Whether this candidate filled a floor-reserved slot. */
  floor: boolean;
  /** Whether this candidate fills an exploration slot. */
  exploration?: boolean;
  /** Per-feature score breakdown for debuggability. */
  breakdown?: ScoreBreakdown;
}

/**
 * Check if a candidate matches a floor reservation condition.
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

export interface RankInput {
  messageId: string;
  features: TriageFeatures;
}

export interface RankOptions {
  weights: FeatureWeights;
  explorationSlots: number;
  journalEntries: JournalEntry[];
}

/**
 * Rank candidates with deterministic floor reservations, learned feature scoring,
 * and exploration slots.
 *
 * Algorithm:
 * 1. For each floor reservation, find matching candidates and reserve up to `slots` for them.
 * 2. If exploration slots requested, reserve positions for high-uncertainty candidates.
 * 3. Fill remaining slots with candidates sorted by learned feature score.
 * 4. Cap at targetDepth.
 */
export function rankCandidates(
  candidates: RankInput[],
  floor: FloorReservation[],
  targetDepth: number,
  now: Date = new Date(),
  options?: RankOptions,
): RankedCandidate[] {
  const weights = options?.weights ?? DEFAULT_WEIGHTS;
  const explorationSlots = options?.explorationSlots ?? 0;
  const journalEntries = options?.journalEntries ?? [];

  const result: RankedCandidate[] = [];
  const used = new Set<string>();

  // Step 1: Fill floor-reserved slots
  for (const reservation of floor) {
    let filled = 0;
    for (const candidate of candidates) {
      if (used.has(candidate.messageId)) continue;
      if (filled >= reservation.slots) break;
      if (matchesFloor(candidate.features, reservation.match, now)) {
        const fv = extractFeatureVector(candidate.features, now);
        result.push({
          messageId: candidate.messageId,
          features: candidate.features,
          score: result.length,
          floor: true,
          breakdown: computeBreakdown(fv, weights),
        });
        used.add(candidate.messageId);
        filled++;
      }
    }
  }

  // Step 2: Reserve exploration slots for high-uncertainty candidates
  const explorationCandidates: RankedCandidate[] = [];
  if (explorationSlots > 0) {
    // Count how many times each messageId appears in journal
    const seenCounts = new Map<string, number>();
    for (const entry of journalEntries) {
      const mid = entry.messageId as string | undefined;
      if (mid) {
        seenCounts.set(mid, (seenCounts.get(mid) ?? 0) + 1);
      }
    }

    // Sort unused candidates by least-seen (highest uncertainty)
    const unseenCandidates = candidates
      .filter((c) => !used.has(c.messageId))
      .map((c) => ({ ...c, seenCount: seenCounts.get(c.messageId) ?? 0 }))
      .sort((a, b) => a.seenCount - b.seenCount);

    let filled = 0;
    for (const candidate of unseenCandidates) {
      if (filled >= explorationSlots) break;
      if (result.length >= targetDepth) break;
      const fv = extractFeatureVector(candidate.features, now);
      const ranked: RankedCandidate = {
        messageId: candidate.messageId,
        features: candidate.features,
        score: result.length,
        floor: false,
        exploration: true,
        breakdown: computeBreakdown(fv, weights),
      };
      explorationCandidates.push(ranked);
      used.add(candidate.messageId);
      filled++;
    }
  }

  // Step 3: Score and sort remaining candidates by learned feature score
  const scored = candidates
    .filter((c) => !used.has(c.messageId))
    .map((c) => {
      const fv = extractFeatureVector(c.features, now);
      return {
        ...c,
        fv,
        totalScore: scoreCandidate(fv, weights),
        breakdown: computeBreakdown(fv, weights),
      };
    })
    .sort((a, b) => b.totalScore - a.totalScore); // Higher score = higher priority

  for (const candidate of scored) {
    if (result.length + explorationCandidates.length >= targetDepth) break;
    result.push({
      messageId: candidate.messageId,
      features: candidate.features,
      score: result.length,
      floor: false,
      breakdown: candidate.breakdown,
    });
  }

  // Insert exploration candidates (at the end of the scored section)
  result.push(...explorationCandidates);

  // Cap at targetDepth (floor slots might have already exceeded it)
  return result.slice(0, targetDepth);
}
