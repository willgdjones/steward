import { describe, it, expect } from 'vitest';
import { rankCandidates, matchesFloor, extractFeatureVector, learnWeights, scoreCandidate, type RankInput, type FeatureVector, type FeatureWeights, DEFAULT_WEIGHTS, WEIGHT_BOUNDS } from '../src/ranker.js';
import type { TriageFeatures } from '../src/triage.js';
import type { FloorReservation } from '../src/rules.js';
import type { JournalEntry } from '../src/journal.js';

function features(overrides: Partial<TriageFeatures> = {}): TriageFeatures {
  return {
    deadline: null,
    amount: null,
    waiting_on_user: false,
    category: 'other',
    urgency: 'low',
    ...overrides,
  };
}

describe('matchesFloor', () => {
  it('matches deadline_within_hours when deadline is within range', () => {
    const now = new Date('2026-04-09T12:00:00Z');
    const f = features({ deadline: '2026-04-10T12:00:00Z' }); // 24h away
    expect(matchesFloor(f, { deadline_within_hours: 72 }, now)).toBe(true);
  });

  it('does not match deadline_within_hours when deadline is too far', () => {
    const now = new Date('2026-04-09T12:00:00Z');
    const f = features({ deadline: '2026-04-20T12:00:00Z' }); // 11 days
    expect(matchesFloor(f, { deadline_within_hours: 72 }, now)).toBe(false);
  });

  it('does not match deadline_within_hours when no deadline', () => {
    const f = features({ deadline: null });
    expect(matchesFloor(f, { deadline_within_hours: 72 })).toBe(false);
  });

  it('matches category', () => {
    const f = features({ category: 'work' });
    expect(matchesFloor(f, { category: 'work' })).toBe(true);
    expect(matchesFloor(f, { category: 'newsletter' })).toBe(false);
  });

  it('matches urgency', () => {
    const f = features({ urgency: 'high' });
    expect(matchesFloor(f, { urgency: 'high' })).toBe(true);
    expect(matchesFloor(f, { urgency: 'low' })).toBe(false);
  });
});

describe('extractFeatureVector', () => {
  it('extracts urgency as numeric score', () => {
    const v = extractFeatureVector(features({ urgency: 'high' }));
    expect(v.urgency).toBe(1.0);

    const v2 = extractFeatureVector(features({ urgency: 'medium' }));
    expect(v2.urgency).toBe(0.5);

    const v3 = extractFeatureVector(features({ urgency: 'low' }));
    expect(v3.urgency).toBe(0.0);
  });

  it('extracts deadline proximity', () => {
    const now = new Date('2026-04-09T12:00:00Z');
    // 12h away → high proximity
    const v = extractFeatureVector(features({ deadline: '2026-04-10T00:00:00Z' }), now);
    expect(v.deadline_proximity).toBeGreaterThan(0.5);

    // No deadline → 0
    const v2 = extractFeatureVector(features({ deadline: null }), now);
    expect(v2.deadline_proximity).toBe(0);
  });

  it('extracts has_amount', () => {
    const v = extractFeatureVector(features({ amount: '£50.00' }));
    expect(v.has_amount).toBe(1);

    const v2 = extractFeatureVector(features({ amount: null }));
    expect(v2.has_amount).toBe(0);
  });

  it('extracts waiting_on_user', () => {
    const v = extractFeatureVector(features({ waiting_on_user: true }));
    expect(v.waiting_on_user).toBe(1);

    const v2 = extractFeatureVector(features({ waiting_on_user: false }));
    expect(v2.waiting_on_user).toBe(0);
  });
});

describe('scoreCandidate', () => {
  it('produces higher scores for high-urgency with default weights', () => {
    const highScore = scoreCandidate(
      extractFeatureVector(features({ urgency: 'high' })),
      DEFAULT_WEIGHTS,
    );
    const lowScore = scoreCandidate(
      extractFeatureVector(features({ urgency: 'low' })),
      DEFAULT_WEIGHTS,
    );
    expect(highScore).toBeGreaterThan(lowScore);
  });

  it('returns per-feature breakdown that sums to total', () => {
    const fv = extractFeatureVector(features({ urgency: 'high', amount: '£100', waiting_on_user: true }));
    const total = scoreCandidate(fv, DEFAULT_WEIGHTS);
    // Sum of weight * feature for each dimension
    let sum = 0;
    for (const key of Object.keys(fv) as (keyof FeatureVector)[]) {
      sum += fv[key] * DEFAULT_WEIGHTS[key];
    }
    expect(total).toBeCloseTo(sum);
  });
});

describe('learnWeights', () => {
  it('returns default weights when journal is empty', () => {
    const weights = learnWeights([]);
    expect(weights).toEqual(DEFAULT_WEIGHTS);
  });

  it('increases weight for features present in approved decisions', () => {
    // Simulate: many approvals of high-urgency items
    const entries: JournalEntry[] = [];
    for (let i = 0; i < 10; i++) {
      entries.push({
        ts: new Date().toISOString(),
        kind: 'decision',
        decision: 'approve',
        goalId: `g-${i}`,
        messageId: `m-${i}`,
        features: { urgency: 'high', deadline: null, amount: null, waiting_on_user: false, category: 'work' },
      });
    }
    const weights = learnWeights(entries);
    expect(weights.urgency).toBeGreaterThan(DEFAULT_WEIGHTS.urgency);
  });

  it('decreases weight for features present in rejected decisions', () => {
    const entries: JournalEntry[] = [];
    for (let i = 0; i < 10; i++) {
      entries.push({
        ts: new Date().toISOString(),
        kind: 'decision',
        decision: 'reject',
        goalId: `g-${i}`,
        messageId: `m-${i}`,
        features: { urgency: 'high', deadline: null, amount: null, waiting_on_user: false, category: 'work' },
      });
    }
    const weights = learnWeights(entries);
    expect(weights.urgency).toBeLessThan(DEFAULT_WEIGHTS.urgency);
  });

  it('clamps weights within bounds', () => {
    // Many many approvals shouldn't push weight above max
    const entries: JournalEntry[] = [];
    for (let i = 0; i < 100; i++) {
      entries.push({
        ts: new Date().toISOString(),
        kind: 'decision',
        decision: 'approve',
        goalId: `g-${i}`,
        messageId: `m-${i}`,
        features: { urgency: 'high', deadline: '2026-04-10T00:00:00Z', amount: '£500', waiting_on_user: true, category: 'work' },
      });
    }
    const weights = learnWeights(entries);
    for (const key of Object.keys(weights) as (keyof FeatureWeights)[]) {
      expect(weights[key]).toBeGreaterThanOrEqual(WEIGHT_BOUNDS[key].min);
      expect(weights[key]).toBeLessThanOrEqual(WEIGHT_BOUNDS[key].max);
    }
  });

  it('ignores non-decision journal entries', () => {
    const entries: JournalEntry[] = [
      { ts: new Date().toISOString(), kind: 'action', goalId: 'g-1', messageId: 'm-1' },
      { ts: new Date().toISOString(), kind: 'verifier_anomaly', goalId: 'g-2', messageId: 'm-2' },
    ];
    const weights = learnWeights(entries);
    expect(weights).toEqual(DEFAULT_WEIGHTS);
  });
});

describe('rankCandidates', () => {
  it('caps output at targetDepth', () => {
    const candidates: RankInput[] = Array.from({ length: 10 }, (_, i) => ({
      messageId: `m${i}`,
      features: features(),
    }));
    const result = rankCandidates(candidates, [], 5);
    expect(result).toHaveLength(5);
  });

  it('sorts by urgency (high > medium > low)', () => {
    const candidates: RankInput[] = [
      { messageId: 'low1', features: features({ urgency: 'low' }) },
      { messageId: 'high1', features: features({ urgency: 'high' }) },
      { messageId: 'med1', features: features({ urgency: 'medium' }) },
    ];
    const result = rankCandidates(candidates, [], 5);
    expect(result.map((r) => r.messageId)).toEqual(['high1', 'med1', 'low1']);
  });

  it('honours floor reservations before tiebreaker', () => {
    const now = new Date('2026-04-09T12:00:00Z');
    const candidates: RankInput[] = [
      { messageId: 'high-no-deadline', features: features({ urgency: 'high' }) },
      { messageId: 'low-with-deadline', features: features({ urgency: 'low', deadline: '2026-04-10T00:00:00Z' }) },
      { messageId: 'med-no-deadline', features: features({ urgency: 'medium' }) },
    ];
    const floor: FloorReservation[] = [
      { match: { deadline_within_hours: 72 }, slots: 1 },
    ];
    const result = rankCandidates(candidates, floor, 3, now);
    // Floor candidate comes first even though it's low urgency
    expect(result[0].messageId).toBe('low-with-deadline');
    expect(result[0].floor).toBe(true);
    // Then high, then medium
    expect(result[1].messageId).toBe('high-no-deadline');
    expect(result[1].floor).toBe(false);
    expect(result[2].messageId).toBe('med-no-deadline');
  });

  it('floor slots cannot exceed targetDepth', () => {
    const now = new Date('2026-04-09T12:00:00Z');
    const candidates: RankInput[] = Array.from({ length: 5 }, (_, i) => ({
      messageId: `m${i}`,
      features: features({ deadline: '2026-04-10T00:00:00Z' }),
    }));
    const floor: FloorReservation[] = [
      { match: { deadline_within_hours: 72 }, slots: 10 },
    ];
    const result = rankCandidates(candidates, floor, 3, now);
    expect(result).toHaveLength(3);
  });

  it('returns empty when no candidates', () => {
    const result = rankCandidates([], [], 5);
    expect(result).toHaveLength(0);
  });

  it('multiple floor reservations fill independently', () => {
    const now = new Date('2026-04-09T12:00:00Z');
    const candidates: RankInput[] = [
      { messageId: 'work1', features: features({ category: 'work', urgency: 'low' }) },
      { messageId: 'deadline1', features: features({ deadline: '2026-04-10T00:00:00Z', urgency: 'low' }) },
      { messageId: 'high1', features: features({ urgency: 'high' }) },
    ];
    const floor: FloorReservation[] = [
      { match: { deadline_within_hours: 72 }, slots: 1 },
      { match: { category: 'work' }, slots: 1 },
    ];
    const result = rankCandidates(candidates, floor, 5, now);
    // Both floor candidates should be first
    expect(result[0].messageId).toBe('deadline1');
    expect(result[0].floor).toBe(true);
    expect(result[1].messageId).toBe('work1');
    expect(result[1].floor).toBe(true);
    // Then remaining by urgency
    expect(result[2].messageId).toBe('high1');
    expect(result[2].floor).toBe(false);
  });

  it('includes per-feature breakdown on each ranked candidate', () => {
    const candidates: RankInput[] = [
      { messageId: 'm1', features: features({ urgency: 'high', amount: '£50' }) },
    ];
    const result = rankCandidates(candidates, [], 5);
    expect(result[0].breakdown).toBeDefined();
    expect(result[0].breakdown!.urgency).toBeGreaterThan(0);
    expect(result[0].breakdown!.has_amount).toBeGreaterThan(0);
  });

  it('reserves exploration slots for candidates with high uncertainty', () => {
    // 10 candidates, all low urgency
    const candidates: RankInput[] = Array.from({ length: 10 }, (_, i) => ({
      messageId: `m${i}`,
      features: features({ urgency: 'low' }),
    }));
    // Request 5 slots with 1 exploration slot
    const result = rankCandidates(candidates, [], 5, undefined, {
      weights: DEFAULT_WEIGHTS,
      explorationSlots: 1,
      journalEntries: [],
    });
    expect(result).toHaveLength(5);
    // At least one candidate should be marked as exploration
    const explorationCards = result.filter((r) => r.exploration);
    expect(explorationCards).toHaveLength(1);
  });

  it('exploration slots pick candidates with least swipe history', () => {
    const candidates: RankInput[] = [
      { messageId: 'seen-a-lot', features: features({ urgency: 'high' }) },
      { messageId: 'never-seen', features: features({ urgency: 'low' }) },
    ];
    // Journal has many entries for 'seen-a-lot' sender but none for 'never-seen'
    const journalEntries: JournalEntry[] = Array.from({ length: 5 }, (_, i) => ({
      ts: new Date().toISOString(),
      kind: 'decision',
      decision: 'approve',
      goalId: `g-${i}`,
      messageId: `seen-a-lot`,
    }));
    const result = rankCandidates(candidates, [], 5, undefined, {
      weights: DEFAULT_WEIGHTS,
      explorationSlots: 1,
      journalEntries,
    });
    // The exploration slot should go to the less-seen candidate
    const explorationCards = result.filter((r) => r.exploration);
    expect(explorationCards).toHaveLength(1);
    expect(explorationCards[0].messageId).toBe('never-seen');
  });

  it('uses learned weights from journal to affect ranking', () => {
    // Train weights: many approvals of items with amounts
    const journalEntries: JournalEntry[] = [];
    for (let i = 0; i < 20; i++) {
      journalEntries.push({
        ts: new Date().toISOString(),
        kind: 'decision',
        decision: 'approve',
        goalId: `g-${i}`,
        messageId: `m-${i}`,
        features: { urgency: 'low', deadline: null, amount: '£100', waiting_on_user: false, category: 'transaction' },
      });
    }
    const weights = learnWeights(journalEntries);

    // Now rank: one with amount (low urgency) vs one without (medium urgency)
    const candidates: RankInput[] = [
      { messageId: 'no-amount', features: features({ urgency: 'medium' }) },
      { messageId: 'with-amount', features: features({ urgency: 'low', amount: '£50' }) },
    ];
    const result = rankCandidates(candidates, [], 5, undefined, {
      weights,
      explorationSlots: 0,
      journalEntries: [],
    });
    // With learned weights boosting has_amount, the amount candidate should rank higher
    expect(result[0].messageId).toBe('with-amount');
  });
});
