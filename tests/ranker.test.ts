import { describe, it, expect } from 'vitest';
import { rankCandidates, matchesFloor, type RankInput } from '../src/ranker.js';
import type { TriageFeatures } from '../src/triage.js';
import type { FloorReservation } from '../src/rules.js';

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
});
