import { describe, it, expect } from 'vitest';
import { clusterCandidates, type TriagedCandidate } from '../src/batcher.js';
import type { TriageResult } from '../src/triage.js';

function makeCandidate(id: string, from: string, category: string): TriagedCandidate {
  return {
    message: { id, from, subject: `subj-${id}`, body: 'body', unread: true },
    result: {
      features: {
        deadline: null,
        amount: null,
        waiting_on_user: false,
        category,
        urgency: 'low',
      },
      snippet: `snippet for ${id}`,
    },
  };
}

describe('clusterCandidates', () => {
  it('groups candidates by domain+category and returns batches meeting threshold', () => {
    const candidates = [
      makeCandidate('m1', 'a@substack.com', 'newsletter'),
      makeCandidate('m2', 'b@substack.com', 'newsletter'),
      makeCandidate('m3', 'c@substack.com', 'newsletter'),
      makeCandidate('m4', 'd@other.com', 'personal'),
    ];

    const { batches, remaining } = clusterCandidates(candidates, 3);

    expect(batches).toHaveLength(1);
    expect(batches[0].domain).toBe('substack.com');
    expect(batches[0].category).toBe('newsletter');
    expect(batches[0].candidates).toHaveLength(3);
    expect(remaining).toHaveLength(1);
    expect(remaining[0].message.id).toBe('m4');
  });

  it('does not batch when below threshold', () => {
    const candidates = [
      makeCandidate('m1', 'a@substack.com', 'newsletter'),
      makeCandidate('m2', 'b@substack.com', 'newsletter'),
    ];

    const { batches, remaining } = clusterCandidates(candidates, 3);

    expect(batches).toHaveLength(0);
    expect(remaining).toHaveLength(2);
  });

  it('separates different categories from the same domain', () => {
    const candidates = [
      makeCandidate('m1', 'a@example.com', 'newsletter'),
      makeCandidate('m2', 'b@example.com', 'newsletter'),
      makeCandidate('m3', 'c@example.com', 'newsletter'),
      makeCandidate('m4', 'd@example.com', 'marketing'),
      makeCandidate('m5', 'e@example.com', 'marketing'),
      makeCandidate('m6', 'f@example.com', 'marketing'),
    ];

    const { batches, remaining } = clusterCandidates(candidates, 3);

    expect(batches).toHaveLength(2);
    expect(batches.map((b) => b.category).sort()).toEqual(['marketing', 'newsletter']);
    expect(remaining).toHaveLength(0);
  });

  it('handles empty input', () => {
    const { batches, remaining } = clusterCandidates([], 3);
    expect(batches).toHaveLength(0);
    expect(remaining).toHaveLength(0);
  });

  it('is case-insensitive on domain', () => {
    const candidates = [
      makeCandidate('m1', 'a@SubStack.com', 'newsletter'),
      makeCandidate('m2', 'b@substack.COM', 'newsletter'),
      makeCandidate('m3', 'c@SUBSTACK.com', 'newsletter'),
    ];

    const { batches } = clusterCandidates(candidates, 3);

    expect(batches).toHaveLength(1);
    expect(batches[0].domain).toBe('substack.com');
  });
});
