import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { mkdtempSync, readFileSync, existsSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import { FakeGmail } from '../src/gmail/fake.js';
import { createExecutorServer, type ServerDeps } from '../src/executor/server.js';
import { planGoal, type PlannerInput } from '../src/planner/index.js';
import { sanitiseEnvForPlanner } from '../src/executor/plannerClient.js';
import { redact } from '../src/redactor.js';
import { loadRules, type Rules } from '../src/rules.js';
import type { JournalEntry } from '../src/journal.js';

const EMPTY_RULES: Rules = {
  blacklist: [],
  redaction: [],
  queue: { target_depth: 5, low_water_mark: 2, batch_threshold: 999, exploration_slots: 0 },
  urgent_senders: [],
  floor: [],
  reversibility: [],
  verifier: { interval_minutes: 60 },
  promotion: { threshold: 5, cooldown_minutes: 1440, interval_minutes: 120 },
};

/** Adapter: wraps the trivial planGoal for the new PlannerInput signature. */
const trivialPlan = async (input: PlannerInput) => planGoal(input.message);

describe('slice 002 end-to-end skeleton', () => {
  let dir: string;
  let url: string;
  let server: ReturnType<typeof createExecutorServer>;

  beforeEach(async () => {
    dir = mkdtempSync(join(tmpdir(), 'steward-e2e-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([
      {
        id: 'm1',
        from: 'alice@example.com',
        subject: 'hello',
        body: 'sensitive body content',
        unread: true,
      },
    ]);
    server = createExecutorServer({
      gmail,
      journalPath: join(dir, 'journal.jsonl'),
      plan: trivialPlan,
      getRules: () => EMPTY_RULES,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;
    url = `http://127.0.0.1:${port}`;
  });

  afterEach(async () => {
    await new Promise<void>((r) => server.close(() => r()));
  });

  it('reads gmail → produces a card → approve writes a journal entry', async () => {
    const cardRes = await fetch(`${url}/card`);
    expect(cardRes.status).toBe(200);
    const goal = (await cardRes.json()) as { id: string; title: string; reason: string };
    expect(goal.title).toContain('example.com');

    const decRes = await fetch(`${url}/card/${goal.id}/decision`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ decision: 'approve' }),
    });
    expect(decRes.status).toBe(200);

    const journalPath = join(dir, 'journal.jsonl');
    expect(existsSync(journalPath)).toBe(true);
    const lines = readFileSync(journalPath, 'utf8').trim().split('\n');
    expect(lines).toHaveLength(1);
    const entry = JSON.parse(lines[0]);
    // Since the trivial planner produces action=archive, the executor
    // dispatches to the sub-agent — journal kind is 'action', not 'decision'.
    expect(entry).toMatchObject({
      kind: 'action',
      goalId: goal.id,
      messageId: 'm1',
    });
    expect((entry.outcomes as unknown[])[0]).toMatchObject({ success: true, action_taken: 'archive' });
    expect(entry.verification).toMatchObject({ verified: true });
  });

  it('serves the web client at /', async () => {
    const res = await fetch(`${url}/`);
    expect(res.status).toBe(200);
    const html = await res.text();
    expect(html).toContain('steward');
    expect(html).toContain('approve');
  });

  it('returns 204 when there are no unread messages', async () => {
    const empty = mkdtempSync(join(tmpdir(), 'steward-empty-'));
    const gmail = new FakeGmail(join(empty, 'fake_inbox.json'));
    gmail.save([]);
    const s = createExecutorServer({
      gmail,
      journalPath: join(empty, 'journal.jsonl'),
      plan: trivialPlan,
      getRules: () => EMPTY_RULES,
    });
    await new Promise<void>((r) => s.listen(0, r));
    const { port } = s.address() as AddressInfo;
    const res = await fetch(`http://127.0.0.1:${port}/card`);
    expect(res.status).toBe(204);
    await new Promise<void>((r) => s.close(() => r()));
  });
});

describe('redactor', () => {
  it('drops the body and reduces from to a domain', () => {
    const r = redact({
      id: 'm1',
      from: 'alice@example.com',
      subject: 'hi',
      body: 'secret',
      unread: true,
    });
    expect(r).toEqual({ id: 'm1', fromDomain: 'example.com', subject: 'hi' });
    expect((r as unknown as Record<string, unknown>).body).toBeUndefined();
  });
});

describe('planner credential separation', () => {
  it('strips credential-bearing env vars before spawning the planner', () => {
    const env = sanitiseEnvForPlanner({
      PATH: '/usr/bin',
      STEWARD_CREDENTIALS_DIR: '/secret/creds',
      GMAIL_OAUTH_TOKEN: 'abc',
      MY_API_KEY: 'def',
      MY_SECRET: 'ghi',
      USER_PASSWORD: 'jkl',
      HARMLESS: 'ok',
    });
    expect(env.PATH).toBe('/usr/bin');
    expect(env.HARMLESS).toBe('ok');
    expect(env.STEWARD_CREDENTIALS_DIR).toBeUndefined();
    expect(env.GMAIL_OAUTH_TOKEN).toBeUndefined();
    expect(env.MY_API_KEY).toBeUndefined();
    expect(env.MY_SECRET).toBeUndefined();
    expect(env.USER_PASSWORD).toBeUndefined();
  });
});

describe('slice 003 principles gate + redactor rules', () => {
  it('blacklist blocks an approved action and returns 403', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-gate-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([
      { id: 'm1', from: 'alice@example.com', subject: 'hello', body: 'body', unread: true },
    ]);

    const rules: Rules = {
      blacklist: [{ transport: 'gmail', action: 'archive' }],
      redaction: [],
      queue: { target_depth: 5, low_water_mark: 2, batch_threshold: 3, exploration_slots: 0 },
      urgent_senders: [],
      floor: [],
      reversibility: [],
      verifier: { interval_minutes: 60 },
  promotion: { threshold: 5, cooldown_minutes: 1440, interval_minutes: 120 },
    };

    const server = createExecutorServer({
      gmail,
      journalPath: join(dir, 'journal.jsonl'),
      plan: trivialPlan,
      getRules: () => rules,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;
    const base = `http://127.0.0.1:${port}`;

    // Get the card
    const cardRes = await fetch(`${base}/card`);
    expect(cardRes.status).toBe(200);
    const goal = (await cardRes.json()) as { id: string };

    // Try to approve — should be blocked
    const decRes = await fetch(`${base}/card/${goal.id}/decision`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ decision: 'approve' }),
    });
    expect(decRes.status).toBe(403);
    const body = (await decRes.json()) as { error: string; reason: string };
    expect(body.error).toBe('blocked');
    expect(body.reason).toContain('gmail');

    // Journal records the block
    const lines = readFileSync(join(dir, 'journal.jsonl'), 'utf8').trim().split('\n');
    expect(JSON.parse(lines[0])).toMatchObject({ kind: 'blocked' });

    await new Promise<void>((r) => server.close(() => r()));
  });

  it('redaction rules strip fields before they reach the planner', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-redact-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([
      {
        id: 'm1',
        from: 'alice@bank.com',
        subject: 'Your card 1234-5678 statement',
        body: 'sensitive',
        unread: true,
      },
    ]);

    const rules: Rules = {
      blacklist: [],
      redaction: [{ field: 'subject', pattern: '\\d{4}-\\d{4}' }],
      queue: { target_depth: 5, low_water_mark: 2, batch_threshold: 3, exploration_slots: 0 },
      urgent_senders: [],
      floor: [],
      reversibility: [],
      verifier: { interval_minutes: 60 },
  promotion: { threshold: 5, cooldown_minutes: 1440, interval_minutes: 120 },
    };

    // Capture what the planner receives
    let plannerInput: PlannerInput | null = null;
    const server = createExecutorServer({
      gmail,
      journalPath: join(dir, 'journal.jsonl'),
      plan: async (input) => {
        plannerInput = input;
        return planGoal(input.message);
      },
      getRules: () => rules,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;

    await fetch(`http://127.0.0.1:${port}/card`);

    expect(plannerInput).not.toBeNull();
    expect(plannerInput!.message.subject).toBe('Your card [REDACTED] statement');
    expect(plannerInput!.message.subject).not.toContain('1234');

    await new Promise<void>((r) => server.close(() => r()));
  });

  it('loads rules from principles.md via loadRules', () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-load-'));
    writeFileSync(
      join(dir, 'principles.md'),
      `blacklist:\n  - transport: gmail\n    action: send\nredaction:\n  - field: subject\n`,
    );
    const rules = loadRules(dir);
    expect(rules.blacklist).toEqual([{ transport: 'gmail', action: 'send' }]);
    expect(rules.redaction).toEqual([{ field: 'subject' }]);
  });
});

describe('slice 004 two-stage pipeline', () => {
  it('triage runs before planner and both receive correct data', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-004-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([
      {
        id: 'm1',
        from: 'boss@work.com',
        subject: 'Q2 report due Friday',
        body: 'Please send the Q2 report by end of day Friday.',
        unread: true,
      },
    ]);

    let triageCalledWith: { from: string; subject: string } | null = null;
    let plannerCalledWith: PlannerInput | null = null;

    const server = createExecutorServer({
      gmail,
      journalPath: join(dir, 'journal.jsonl'),
      triage: async (msg) => {
        triageCalledWith = { from: msg.from, subject: msg.subject };
        return {
          features: {
            deadline: '2026-04-11',
            amount: null,
            waiting_on_user: true,
            category: 'work',
            urgency: 'high',
          },
          snippet: 'Boss requesting Q2 report by Friday.',
        };
      },
      plan: async (input) => {
        plannerCalledWith = input;
        return {
          id: `g-${input.message.id}`,
          title: 'Send Q2 report to boss',
          reason: 'Q2 report due Friday, boss is waiting for it.',
          messageId: input.message.id,
          transport: 'gmail',
          action: 'reply',
        };
      },
      getRules: () => EMPTY_RULES,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;

    const cardRes = await fetch(`http://127.0.0.1:${port}/card`);
    expect(cardRes.status).toBe(200);
    const goal = (await cardRes.json()) as { id: string; title: string; reason: string };

    // Triage saw the full message
    expect(triageCalledWith).not.toBeNull();
    expect(triageCalledWith!.from).toBe('boss@work.com');
    expect(triageCalledWith!.subject).toBe('Q2 report due Friday');

    // Planner saw redacted message + triage features
    expect(plannerCalledWith).not.toBeNull();
    expect(plannerCalledWith!.message.fromDomain).toBe('work.com');
    expect((plannerCalledWith!.message as unknown as Record<string, unknown>).body).toBeUndefined();
    expect(plannerCalledWith!.features.deadline).toBe('2026-04-11');
    expect(plannerCalledWith!.features.waiting_on_user).toBe(true);
    expect(plannerCalledWith!.features.urgency).toBe('high');
    expect(plannerCalledWith!.snippet).toBe('Boss requesting Q2 report by Friday.');

    // Goal reflects the planner's output
    expect(goal.title).toBe('Send Q2 report to boss');
    expect(goal.reason).toContain('Q2 report');

    await new Promise<void>((r) => server.close(() => r()));
  });

  it('redactor sits between triage and planner stages', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-004-redact-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([
      {
        id: 'm1',
        from: 'bank@hsbc.com',
        subject: 'Account 12345678 statement ready',
        body: 'Your statement for account 12345678 is ready.',
        unread: true,
      },
    ]);

    const rules: Rules = {
      blacklist: [],
      redaction: [{ field: 'subject', pattern: '\\d{8}' }],
      queue: { target_depth: 5, low_water_mark: 2, batch_threshold: 3, exploration_slots: 0 },
      urgent_senders: [],
      floor: [],
      reversibility: [],
      verifier: { interval_minutes: 60 },
  promotion: { threshold: 5, cooldown_minutes: 1440, interval_minutes: 120 },
    };

    let plannerInput: PlannerInput | null = null;
    const server = createExecutorServer({
      gmail,
      journalPath: join(dir, 'journal.jsonl'),
      triage: async () => ({
        features: {
          deadline: null,
          amount: null,
          waiting_on_user: false,
          category: 'transaction',
          urgency: 'low',
        },
        snippet: 'Bank statement available.',
      }),
      plan: async (input) => {
        plannerInput = input;
        return planGoal(input.message);
      },
      getRules: () => rules,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;

    await fetch(`http://127.0.0.1:${port}/card`);

    // Planner should see redacted subject
    expect(plannerInput).not.toBeNull();
    expect(plannerInput!.message.subject).toBe('Account [REDACTED] statement ready');
    expect(plannerInput!.message.subject).not.toContain('12345678');
    // But triage features pass through unredacted (they're structured, not raw content)
    expect(plannerInput!.features.category).toBe('transaction');

    await new Promise<void>((r) => server.close(() => r()));
  });

  it('uses search() instead of readOneUnread()', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-004-search-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([
      { id: 'm1', from: 'a@test.com', subject: 'msg1', body: 'b1', unread: true },
      { id: 'm2', from: 'b@test.com', subject: 'msg2', body: 'b2', unread: true },
      { id: 'm3', from: 'c@test.com', subject: 'msg3', body: 'b3', unread: false },
    ]);

    // search() should return unread messages
    const results = gmail.search('is:unread');
    expect(results).toHaveLength(2);
    expect(results.map((m) => m.id)).toEqual(['m1', 'm2']);
  });

  it('falls back to default triage when no triage function provided', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-004-fallback-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([
      { id: 'm1', from: 'a@test.com', subject: 'test', body: 'body', unread: true },
    ]);

    let plannerInput: PlannerInput | null = null;
    const server = createExecutorServer({
      gmail,
      journalPath: join(dir, 'journal.jsonl'),
      // No triage — should use defaultTriageResult
      plan: async (input) => {
        plannerInput = input;
        return planGoal(input.message);
      },
      getRules: () => EMPTY_RULES,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;

    await fetch(`http://127.0.0.1:${port}/card`);

    expect(plannerInput).not.toBeNull();
    expect(plannerInput!.features.category).toBe('other');
    expect(plannerInput!.features.urgency).toBe('low');
    expect(plannerInput!.snippet).toBe('No triage available.');

    await new Promise<void>((r) => server.close(() => r()));
  });
});

describe('slice 005 queue with deterministic floor', () => {
  function makeMessages(count: number) {
    return Array.from({ length: count }, (_, i) => ({
      id: `m${i}`,
      from: `user${i}@example.com`,
      subject: `msg ${i}`,
      body: `body ${i}`,
      unread: true,
    }));
  }

  function makeQueueRules(overrides: Omit<Partial<Rules>, 'queue'> & { queue?: Partial<import('../src/rules.js').QueueConfig> } = {}): Rules {
    const defaultQueue = { target_depth: 3, low_water_mark: 1, batch_threshold: 999, exploration_slots: 0 };
    const { queue: queueOverrides, ...rest } = overrides;
    return {
      blacklist: [],
      redaction: [],
      queue: { ...defaultQueue, ...queueOverrides },
      urgent_senders: [],
      floor: [],
      reversibility: [],
      verifier: { interval_minutes: 60 },
  promotion: { threshold: 5, cooldown_minutes: 1440, interval_minutes: 120 },
      ...rest,
    };
  }

  async function startServer(
    dir: string,
    messages: Array<{ id: string; from: string; subject: string; body: string; unread: boolean }>,
    rules: Rules,
    opts: { triage?: ServerDeps['triage'] } = {},
  ) {
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save(messages);
    const server = createExecutorServer({
      gmail,
      journalPath: join(dir, 'journal.jsonl'),
      plan: trivialPlan,
      getRules: () => rules,
      triage: opts.triage,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;
    return { server, url: `http://127.0.0.1:${port}`, gmail };
  }

  async function stopServer(server: ReturnType<typeof createExecutorServer>) {
    await new Promise<void>((r) => server.close(() => r()));
  }

  it('fills queue up to target_depth on first request', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-005-'));
    const rules = makeQueueRules({ queue: { target_depth: 3, low_water_mark: 1 } });
    const { server, url } = await startServer(dir, makeMessages(5), rules);

    const res = await fetch(`${url}/card`);
    expect(res.status).toBe(200);

    const queueRes = await fetch(`${url}/queue`);
    const q = (await queueRes.json()) as { depth: number; cards: unknown[] };
    expect(q.depth).toBe(3);
    expect(q.depth).toBeLessThanOrEqual(rules.queue.target_depth);

    await stopServer(server);
  });

  it('queue size never exceeds target_depth', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-005-cap-'));
    const rules = makeQueueRules({ queue: { target_depth: 2, low_water_mark: 1 } });
    const { server, url } = await startServer(dir, makeMessages(10), rules);

    await fetch(`${url}/card`);
    const queueRes = await fetch(`${url}/queue`);
    const q = (await queueRes.json()) as { depth: number };
    expect(q.depth).toBeLessThanOrEqual(2);

    await stopServer(server);
  });

  it('refills when queue drops below low_water_mark', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-005-refill-'));
    const rules = makeQueueRules({ queue: { target_depth: 3, low_water_mark: 2 } });
    const { server, url } = await startServer(dir, makeMessages(6), rules);

    // Initial fill
    const cardRes = await fetch(`${url}/card`);
    expect(cardRes.status).toBe(200);
    const goal = (await cardRes.json()) as { id: string };

    // Queue should be at target_depth (3)
    let q = (await (await fetch(`${url}/queue`)).json()) as { depth: number };
    expect(q.depth).toBe(3);

    // Approve two cards to drop below low_water (3 → 2 → 1)
    await fetch(`${url}/card/${goal.id}/decision`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ decision: 'approve' }),
    });

    // Get next card and approve it
    const card2Res = await fetch(`${url}/card`);
    const goal2 = (await card2Res.json()) as { id: string };
    await fetch(`${url}/card/${goal2.id}/decision`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ decision: 'approve' }),
    });

    // Now queue is at 1, below low_water_mark=2. Next GET /card should trigger refill.
    await fetch(`${url}/card`);
    q = (await (await fetch(`${url}/queue`)).json()) as { depth: number };
    expect(q.depth).toBeGreaterThanOrEqual(2);
    expect(q.depth).toBeLessThanOrEqual(3);

    await stopServer(server);
  });

  it('manual refill via POST /refill works', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-005-manual-'));
    // low_water_mark = target_depth so no auto-refill at 0
    const rules = makeQueueRules({ queue: { target_depth: 3, low_water_mark: 0 } });
    const { server, url } = await startServer(dir, makeMessages(5), rules);

    // No auto-refill because low_water_mark=0 means queue.length (0) >= 0
    // Actually low_water_mark=0 means it always refills. Let me use a different approach.
    // Start with an empty inbox, then add messages and force refill.
    await stopServer(server);

    const dir2 = mkdtempSync(join(tmpdir(), 'steward-005-manual2-'));
    const rules2 = makeQueueRules({ queue: { target_depth: 3, low_water_mark: 3 } });
    const { server: s2, url: u2, gmail } = await startServer(dir2, [], rules2);

    // Empty queue
    const emptyRes = await fetch(`${u2}/card`);
    expect(emptyRes.status).toBe(204);

    // Add messages and force refill
    gmail.save(makeMessages(5));
    const refillRes = await fetch(`${u2}/refill`, { method: 'POST' });
    expect(refillRes.status).toBe(200);
    const refillBody = (await refillRes.json()) as { ok: boolean; depth: number };
    expect(refillBody.ok).toBe(true);
    expect(refillBody.depth).toBe(3);

    await stopServer(s2);
  });

  it('urgent senders bypass queue and surface immediately', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-005-urgent-'));
    const rules = makeQueueRules({
      queue: { target_depth: 3, low_water_mark: 1, batch_threshold: 999, exploration_slots: 0 },
      urgent_senders: ['boss@important.com'],
    });
    const messages = [
      { id: 'm0', from: 'normal@example.com', subject: 'normal', body: 'b', unread: true },
      { id: 'm1', from: 'normal2@example.com', subject: 'normal2', body: 'b', unread: true },
      { id: 'urgent', from: 'boss@important.com', subject: 'urgent', body: 'b', unread: true },
      { id: 'm3', from: 'normal3@example.com', subject: 'normal3', body: 'b', unread: true },
    ];
    const { server, url } = await startServer(dir, messages, rules);

    const cardRes = await fetch(`${url}/card`);
    expect(cardRes.status).toBe(200);
    const goal = (await cardRes.json()) as { id: string; messageId: string };
    // Urgent sender should be the first card
    expect(goal.messageId).toBe('urgent');

    await stopServer(server);
  });

  it('floor reservations are honoured before tiebreaker', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-005-floor-'));
    const now = new Date('2026-04-09T12:00:00Z');
    const rules = makeQueueRules({
      queue: { target_depth: 3, low_water_mark: 1, batch_threshold: 999, exploration_slots: 0 },
      floor: [{ match: { deadline_within_hours: 72 }, slots: 1 }],
    });

    const messages = [
      { id: 'high', from: 'a@test.com', subject: 'high urgency', body: 'b', unread: true },
      { id: 'deadline', from: 'b@test.com', subject: 'has deadline', body: 'b', unread: true },
      { id: 'low', from: 'c@test.com', subject: 'low urgency', body: 'b', unread: true },
    ];

    // Triage returns: high urgency for first, low urgency with deadline for second, low for third
    const triageMap: Record<string, import('../src/triage.js').TriageResult> = {
      high: {
        features: { deadline: null, amount: null, waiting_on_user: false, category: 'work', urgency: 'high' },
        snippet: 'high urgency task',
      },
      deadline: {
        features: { deadline: '2026-04-10T12:00:00Z', amount: null, waiting_on_user: false, category: 'other', urgency: 'low' },
        snippet: 'task with deadline',
      },
      low: {
        features: { deadline: null, amount: null, waiting_on_user: false, category: 'other', urgency: 'low' },
        snippet: 'low urgency',
      },
    };

    const { server, url } = await startServer(dir, messages, rules, {
      triage: async (msg) => triageMap[msg.id],
    });

    const cardRes = await fetch(`${url}/card`);
    expect(cardRes.status).toBe(200);

    // Check queue order
    const queueRes = await fetch(`${url}/queue`);
    const q = (await queueRes.json()) as { depth: number; cards: Array<{ messageId: string }> };
    expect(q.depth).toBe(3);

    // First card should be the deadline one (floor reservation)
    expect(q.cards[0].messageId).toBe('deadline');
    // Then high urgency (tiebreaker)
    expect(q.cards[1].messageId).toBe('high');

    await stopServer(server);
  });

  it('does not duplicate messages in the queue', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-005-dedup-'));
    const rules = makeQueueRules({ queue: { target_depth: 5, low_water_mark: 3 } });
    const { server, url } = await startServer(dir, makeMessages(3), rules);

    // Fetch card twice — should not duplicate
    await fetch(`${url}/card`);
    await fetch(`${url}/card`);

    const queueRes = await fetch(`${url}/queue`);
    const q = (await queueRes.json()) as { depth: number; cards: Array<{ messageId: string }> };
    const ids = q.cards.map((c) => c.messageId);
    expect(new Set(ids).size).toBe(ids.length);

    await stopServer(server);
  });
});

describe('slice 006 archive via sub-agent', () => {
  it('approving an archive card dispatches to sub-agent, verifies, and journals the full flow', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-006-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([
      { id: 'm1', from: 'newsletter@substack.com', subject: 'Weekly digest', body: 'content', unread: true },
    ]);

    const rules: Rules = {
      blacklist: [],
      redaction: [],
      queue: { target_depth: 5, low_water_mark: 2, batch_threshold: 3, exploration_slots: 0 },
      urgent_senders: [],
      floor: [],
      reversibility: [{ action: 'archive', reversible: true }],
      verifier: { interval_minutes: 60 },
  promotion: { threshold: 5, cooldown_minutes: 1440, interval_minutes: 120 },
    };

    const server = createExecutorServer({
      gmail,
      journalPath: join(dir, 'journal.jsonl'),
      plan: trivialPlan,
      getRules: () => rules,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;
    const base = `http://127.0.0.1:${port}`;

    // Get card
    const cardRes = await fetch(`${base}/card`);
    expect(cardRes.status).toBe(200);
    const goal = (await cardRes.json()) as { id: string; action: string };
    expect(goal.action).toBe('archive');

    // Approve — should trigger sub-agent dispatch + verification
    const decRes = await fetch(`${base}/card/${goal.id}/decision`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ decision: 'approve' }),
    });
    expect(decRes.status).toBe(200);
    const decBody = (await decRes.json()) as { ok: boolean; outcomes: unknown[]; verification: { verified: boolean; sample: unknown[] } };
    expect(decBody.ok).toBe(true);
    expect(decBody.outcomes[0]).toMatchObject({ success: true, action_taken: 'archive' });
    expect(decBody.verification.verified).toBe(true);

    // Verify the message is archived in FakeGmail
    const msg = gmail.getById('m1');
    expect(msg).not.toBeNull();
    expect(msg!.archived).toBe(true);

    // Journal should have goal, outcomes, and verification
    const lines = readFileSync(join(dir, 'journal.jsonl'), 'utf8').trim().split('\n');
    const entries = lines.map((l) => JSON.parse(l) as JournalEntry);
    const actionEntry = entries.find((e) => e.kind === 'action');
    expect(actionEntry).toBeDefined();
    expect(actionEntry!.goalId).toBe(goal.id);
    expect(actionEntry!.outcomes).toBeDefined();
    expect((actionEntry!.outcomes as unknown[])[0]).toMatchObject({ success: true });
    expect(actionEntry!.verification).toMatchObject({ verified: true });

    await new Promise<void>((r) => server.close(() => r()));
  });

  it('archive card no longer shows archived messages in the queue', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-006-noshow-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([
      { id: 'm1', from: 'a@test.com', subject: 'msg1', body: 'b', unread: true },
    ]);

    const rules: Rules = {
      blacklist: [],
      redaction: [],
      queue: { target_depth: 5, low_water_mark: 2, batch_threshold: 3, exploration_slots: 0 },
      urgent_senders: [],
      floor: [],
      reversibility: [{ action: 'archive', reversible: true }],
      verifier: { interval_minutes: 60 },
  promotion: { threshold: 5, cooldown_minutes: 1440, interval_minutes: 120 },
    };

    const server = createExecutorServer({
      gmail,
      journalPath: join(dir, 'journal.jsonl'),
      plan: trivialPlan,
      getRules: () => rules,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;
    const base = `http://127.0.0.1:${port}`;

    // Get and approve
    const cardRes = await fetch(`${base}/card`);
    const goal = (await cardRes.json()) as { id: string };
    await fetch(`${base}/card/${goal.id}/decision`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ decision: 'approve' }),
    });

    // Next card request should be empty (message is archived)
    const nextRes = await fetch(`${base}/card`);
    expect(nextRes.status).toBe(204);

    await new Promise<void>((r) => server.close(() => r()));
  });

  it('verification failure is journalled when archive does not stick', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-006-verfail-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([
      { id: 'm1', from: 'a@test.com', subject: 'msg1', body: 'b', unread: true },
    ]);

    // Override archive to silently fail (simulate Gmail API failure)
    const origArchive = gmail.archive.bind(gmail);
    gmail.archive = (_id: string) => {
      // Don't actually archive — simulates a failure
      return true; // Returns true (found) but doesn't persist
    };

    const rules: Rules = {
      blacklist: [],
      redaction: [],
      queue: { target_depth: 5, low_water_mark: 2, batch_threshold: 3, exploration_slots: 0 },
      urgent_senders: [],
      floor: [],
      reversibility: [{ action: 'archive', reversible: true }],
      verifier: { interval_minutes: 60 },
  promotion: { threshold: 5, cooldown_minutes: 1440, interval_minutes: 120 },
    };

    const server = createExecutorServer({
      gmail,
      journalPath: join(dir, 'journal.jsonl'),
      plan: trivialPlan,
      getRules: () => rules,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;
    const base = `http://127.0.0.1:${port}`;

    const cardRes = await fetch(`${base}/card`);
    const goal = (await cardRes.json()) as { id: string };
    const decRes = await fetch(`${base}/card/${goal.id}/decision`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ decision: 'approve' }),
    });
    expect(decRes.status).toBe(200);
    const body = (await decRes.json()) as { verification: { verified: boolean } };
    expect(body.verification.verified).toBe(false);

    // Journal should record the failed verification
    const lines = readFileSync(join(dir, 'journal.jsonl'), 'utf8').trim().split('\n');
    const entries = lines.map((l) => JSON.parse(l) as JournalEntry);
    const actionEntry = entries.find((e) => e.kind === 'action');
    expect(actionEntry).toBeDefined();
    expect(actionEntry!.verification).toMatchObject({ verified: false });

    await new Promise<void>((r) => server.close(() => r()));
  });

  it('reversibility is parsed from principles.md', () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-006-rev-'));
    writeFileSync(
      join(dir, 'principles.md'),
      `reversibility:\n  - action: archive\n    reversible: true\n  - action: send\n    reversible: false\n`,
    );
    const rules = loadRules(dir);
    expect(rules.reversibility).toEqual([
      { action: 'archive', reversible: true },
      { action: 'send', reversible: false },
    ]);
  });
});

describe('slice 007 batched action card', () => {
  function makeBatchRules(overrides: Partial<{ batch_threshold: number; target_depth: number }> = {}): Rules {
    return {
      blacklist: [],
      redaction: [],
      queue: {
        target_depth: overrides.target_depth ?? 10,
        low_water_mark: 1,
        batch_threshold: overrides.batch_threshold ?? 3,
        exploration_slots: 0,
      },
      urgent_senders: [],
      floor: [],
      reversibility: [{ action: 'archive', reversible: true }],
      verifier: { interval_minutes: 60 },
  promotion: { threshold: 5, cooldown_minutes: 1440, interval_minutes: 120 },
    };
  }

  it('clusters similar messages into a batched card', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-007-cluster-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([
      { id: 'm1', from: 'a@newsletters.com', subject: 'Newsletter 1', body: 'b', unread: true },
      { id: 'm2', from: 'b@newsletters.com', subject: 'Newsletter 2', body: 'b', unread: true },
      { id: 'm3', from: 'c@newsletters.com', subject: 'Newsletter 3', body: 'b', unread: true },
      { id: 'm4', from: 'user@other.com', subject: 'Personal msg', body: 'b', unread: true },
    ]);

    const rules = makeBatchRules({ batch_threshold: 3 });
    const server = createExecutorServer({
      gmail,
      journalPath: join(dir, 'journal.jsonl'),
      plan: trivialPlan,
      getRules: () => rules,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;
    const base = `http://127.0.0.1:${port}`;

    await fetch(`${base}/card`);

    const queueRes = await fetch(`${base}/queue`);
    const q = (await queueRes.json()) as { depth: number; cards: Array<{ messageIds?: string[]; batchSize?: number; title: string }> };

    // Should have 2 cards: 1 batched (3 newsletters) + 1 individual
    expect(q.depth).toBe(2);

    const batchCard = q.cards.find((c) => c.batchSize);
    expect(batchCard).toBeDefined();
    expect(batchCard!.batchSize).toBe(3);
    expect(batchCard!.messageIds).toHaveLength(3);
    expect(batchCard!.title).toContain('newsletters.com');
    expect(batchCard!.title).toContain('3');

    const singleCard = q.cards.find((c) => !c.batchSize);
    expect(singleCard).toBeDefined();

    await new Promise<void>((r) => server.close(() => r()));
  });

  it('one swipe archives all messages in a batch', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-007-swipe-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([
      { id: 'm1', from: 'a@promo.com', subject: 'Promo 1', body: 'b', unread: true },
      { id: 'm2', from: 'b@promo.com', subject: 'Promo 2', body: 'b', unread: true },
      { id: 'm3', from: 'c@promo.com', subject: 'Promo 3', body: 'b', unread: true },
      { id: 'm4', from: 'd@promo.com', subject: 'Promo 4', body: 'b', unread: true },
    ]);

    const rules = makeBatchRules({ batch_threshold: 3 });
    const server = createExecutorServer({
      gmail,
      journalPath: join(dir, 'journal.jsonl'),
      plan: trivialPlan,
      getRules: () => rules,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;
    const base = `http://127.0.0.1:${port}`;

    // Get the batched card
    const cardRes = await fetch(`${base}/card`);
    expect(cardRes.status).toBe(200);
    const goal = (await cardRes.json()) as { id: string; batchSize: number; messageIds: string[] };
    expect(goal.batchSize).toBe(4);

    // Approve the batch — one swipe
    const decRes = await fetch(`${base}/card/${goal.id}/decision`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ decision: 'approve' }),
    });
    expect(decRes.status).toBe(200);
    const body = (await decRes.json()) as {
      ok: boolean;
      outcomes: Array<{ success: boolean }>;
      verification: { verified: boolean; sample: unknown[] };
      batchSize: number;
    };
    expect(body.ok).toBe(true);
    expect(body.batchSize).toBe(4);
    expect(body.outcomes).toHaveLength(4);
    expect(body.outcomes.every((o) => o.success)).toBe(true);
    expect(body.verification.verified).toBe(true);

    // All messages should be archived in FakeGmail
    for (const id of ['m1', 'm2', 'm3', 'm4']) {
      const msg = gmail.getById(id);
      expect(msg).not.toBeNull();
      expect(msg!.archived).toBe(true);
    }

    // Queue should be empty (no more unread, non-archived messages)
    const nextCard = await fetch(`${base}/card`);
    expect(nextCard.status).toBe(204);

    await new Promise<void>((r) => server.close(() => r()));
  });

  it('journal records the full message-ID list for a batch', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-007-journal-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([
      { id: 'm1', from: 'a@bulk.com', subject: 'Bulk 1', body: 'b', unread: true },
      { id: 'm2', from: 'b@bulk.com', subject: 'Bulk 2', body: 'b', unread: true },
      { id: 'm3', from: 'c@bulk.com', subject: 'Bulk 3', body: 'b', unread: true },
    ]);

    const rules = makeBatchRules({ batch_threshold: 3 });
    const server = createExecutorServer({
      gmail,
      journalPath: join(dir, 'journal.jsonl'),
      plan: trivialPlan,
      getRules: () => rules,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;
    const base = `http://127.0.0.1:${port}`;

    const cardRes = await fetch(`${base}/card`);
    const goal = (await cardRes.json()) as { id: string };

    await fetch(`${base}/card/${goal.id}/decision`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ decision: 'approve' }),
    });

    const lines = readFileSync(join(dir, 'journal.jsonl'), 'utf8').trim().split('\n');
    const entry = JSON.parse(lines[0]) as JournalEntry;
    expect(entry.kind).toBe('action');
    expect(entry.messageIds).toEqual(['m1', 'm2', 'm3']);
    expect(entry.batchSize).toBe(3);
    expect((entry.outcomes as unknown[]).length).toBe(3);

    await new Promise<void>((r) => server.close(() => r()));
  });

  it('verification samples first, middle, and last in a large batch', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-007-verify-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    const messages = Array.from({ length: 7 }, (_, i) => ({
      id: `m${i}`,
      from: `sender${i}@biglist.com`,
      subject: `Item ${i}`,
      body: 'b',
      unread: true,
    }));
    gmail.save(messages);

    const rules = makeBatchRules({ batch_threshold: 3 });
    const server = createExecutorServer({
      gmail,
      journalPath: join(dir, 'journal.jsonl'),
      plan: trivialPlan,
      getRules: () => rules,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;
    const base = `http://127.0.0.1:${port}`;

    const cardRes = await fetch(`${base}/card`);
    const goal = (await cardRes.json()) as { id: string; batchSize: number };
    expect(goal.batchSize).toBe(7);

    const decRes = await fetch(`${base}/card/${goal.id}/decision`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ decision: 'approve' }),
    });
    const body = (await decRes.json()) as {
      verification: { verified: boolean; sample: Array<{ messageId: string }> };
    };

    // Should verify 3 samples: first (m0), middle (m3), last (m6)
    expect(body.verification.sample.length).toBe(3);
    const sampledIds = body.verification.sample.map((v) => v.messageId);
    expect(sampledIds).toContain('m0');
    expect(sampledIds).toContain('m3');
    expect(sampledIds).toContain('m6');

    await new Promise<void>((r) => server.close(() => r()));
  });

  it('batch_threshold is configurable in principles.md', () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-007-config-'));
    writeFileSync(
      join(dir, 'principles.md'),
      `queue:\n  target_depth: 10\n  low_water_mark: 3\n  batch_threshold: 5\n`,
    );
    const rules = loadRules(dir);
    expect(rules.queue.batch_threshold).toBe(5);
  });

  it('below-threshold clusters produce individual cards, not batched', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-007-below-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([
      { id: 'm1', from: 'a@news.com', subject: 'News 1', body: 'b', unread: true },
      { id: 'm2', from: 'b@news.com', subject: 'News 2', body: 'b', unread: true },
    ]);

    const rules = makeBatchRules({ batch_threshold: 3 });
    const server = createExecutorServer({
      gmail,
      journalPath: join(dir, 'journal.jsonl'),
      plan: trivialPlan,
      getRules: () => rules,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;
    const base = `http://127.0.0.1:${port}`;

    await fetch(`${base}/card`);

    const queueRes = await fetch(`${base}/queue`);
    const q = (await queueRes.json()) as { depth: number; cards: Array<{ batchSize?: number }> };
    expect(q.depth).toBe(2);
    // No batched cards — all individual
    expect(q.cards.every((c) => !c.batchSize)).toBe(true);

    await new Promise<void>((r) => server.close(() => r()));
  });
});

describe('slice 008 post-hoc verifier + meta-cards', () => {
  it('POST /verifier/run detects unarchived message and inserts meta-card', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-008-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    // Message exists but was unarchived by user
    gmail.save([
      { id: 'm1', from: 'news@sub.com', subject: 'Newsletter', body: '', unread: true, archived: false },
    ]);
    const journalPath = join(dir, 'journal.jsonl');
    const { appendJournal: aj } = await import('../src/journal.js');
    aj(journalPath, {
      kind: 'action',
      goalId: 'g1',
      messageId: 'm1',
      title: 'Archive newsletter',
      outcomes: [{ success: true, action_taken: 'archive', messageId: 'm1' }],
      verification: { verified: true, sample: [] },
    });

    const server = createExecutorServer({
      gmail,
      journalPath,
      plan: trivialPlan,
      getRules: () => EMPTY_RULES,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;
    const base = `http://127.0.0.1:${port}`;

    // Run verifier
    const verifyRes = await fetch(`${base}/verifier/run`, { method: 'POST' });
    expect(verifyRes.status).toBe(200);

    // Queue should now have a meta-card
    const queueRes = await fetch(`${base}/queue`);
    const q = (await queueRes.json()) as { depth: number; cards: Array<{ id: string; title: string; reason: string }> };
    expect(q.depth).toBe(1);
    expect(q.cards[0].title).toContain('unarchived');
    expect(q.cards[0].id).toContain('meta-');

    // Running verifier again should not duplicate
    await fetch(`${base}/verifier/run`, { method: 'POST' });
    const q2 = (await (await fetch(`${base}/queue`)).json()) as { depth: number };
    expect(q2.depth).toBe(1);

    await new Promise<void>((r) => server.close(() => r()));
  });

  it('POST /verifier/run detects reply-after-archive', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-008-reply-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([
      { id: 'm1', from: 'alice@example.com', subject: 'Project update', body: '', unread: false, archived: true },
      { id: 'm2', from: 'alice@example.com', subject: 'Re: Project update', body: 'follow-up', unread: true },
    ]);
    const journalPath = join(dir, 'journal.jsonl');
    const { appendJournal: aj } = await import('../src/journal.js');
    aj(journalPath, {
      kind: 'action',
      goalId: 'g1',
      messageId: 'm1',
      title: 'Archive project update',
      outcomes: [{ success: true, action_taken: 'archive', messageId: 'm1' }],
      verification: { verified: true, sample: [] },
    });

    const server = createExecutorServer({
      gmail,
      journalPath,
      plan: trivialPlan,
      getRules: () => EMPTY_RULES,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;
    const base = `http://127.0.0.1:${port}`;

    await fetch(`${base}/verifier/run`, { method: 'POST' });
    const q = (await (await fetch(`${base}/queue`)).json()) as { depth: number; cards: Array<{ id: string; title: string }> };
    expect(q.depth).toBe(1);
    expect(q.cards[0].title).toContain('reply');

    await new Promise<void>((r) => server.close(() => r()));
  });

  it('GET /activity returns recent action entries', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-008-activity-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([
      { id: 'm1', from: 'a@b.com', subject: 'test', body: '', unread: true },
    ]);
    const journalPath = join(dir, 'journal.jsonl');
    const { appendJournal: aj } = await import('../src/journal.js');
    aj(journalPath, {
      kind: 'action',
      goalId: 'g1',
      messageId: 'm1',
      title: 'Archive test',
      outcomes: [{ success: true, action_taken: 'archive', messageId: 'm1' }],
      verification: { verified: true, sample: [] },
    });
    aj(journalPath, { kind: 'decision', decision: 'reject', goalId: 'g2', messageId: 'm2' });

    const server = createExecutorServer({
      gmail,
      journalPath,
      plan: trivialPlan,
      getRules: () => EMPTY_RULES,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;

    const res = await fetch(`http://127.0.0.1:${port}/activity`);
    expect(res.status).toBe(200);
    const body = (await res.json()) as { entries: Array<{ kind: string; goalId: string }> };
    // Only action entries, not decisions
    expect(body.entries).toHaveLength(1);
    expect(body.entries[0]).toMatchObject({ kind: 'action', goalId: 'g1' });

    await new Promise<void>((r) => server.close(() => r()));
  });

  it('POST /activity/:goalId/wrong emits a meta-card', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-008-wrong-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([
      { id: 'm1', from: 'a@b.com', subject: 'newsletter', body: '', unread: false, archived: true },
    ]);
    const journalPath = join(dir, 'journal.jsonl');
    const { appendJournal: aj } = await import('../src/journal.js');
    aj(journalPath, {
      kind: 'action',
      goalId: 'g1',
      messageId: 'm1',
      title: 'Archive newsletter',
      outcomes: [{ success: true, action_taken: 'archive', messageId: 'm1' }],
      verification: { verified: true, sample: [] },
    });

    const server = createExecutorServer({
      gmail,
      journalPath,
      plan: trivialPlan,
      getRules: () => EMPTY_RULES,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;
    const base = `http://127.0.0.1:${port}`;

    const res = await fetch(`${base}/activity/g1/wrong`, { method: 'POST' });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { ok: boolean; metaCardId: string };
    expect(body.ok).toBe(true);
    expect(body.metaCardId).toContain('meta-wrong-');

    // Meta-card should be in the queue
    const q = (await (await fetch(`${base}/queue`)).json()) as { depth: number; cards: Array<{ id: string; title: string }> };
    expect(q.depth).toBe(1);
    expect(q.cards[0].title).toContain('wrong');

    // Clicking wrong again is idempotent
    const res2 = await fetch(`${base}/activity/g1/wrong`, { method: 'POST' });
    const body2 = (await res2.json()) as { ok: boolean; alreadyQueued?: boolean };
    expect(body2.alreadyQueued).toBe(true);
    const q2 = (await (await fetch(`${base}/queue`)).json()) as { depth: number };
    expect(q2.depth).toBe(1); // no duplicate

    await new Promise<void>((r) => server.close(() => r()));
  });

  it('verifier_interval parsed from principles.md', () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-008-rules-'));
    writeFileSync(join(dir, 'principles.md'), `verifier:\n  interval_minutes: 30\n`);
    const rules = loadRules(dir);
    expect(rules.verifier).toEqual({ interval_minutes: 30 });
  });

  it('verifier_interval defaults to 60 when not specified', () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-008-rules-default-'));
    writeFileSync(join(dir, 'principles.md'), `blacklist: []\n`);
    const rules = loadRules(dir);
    expect(rules.verifier).toEqual({ interval_minutes: 60 });
  });
});

describe('slice 009 rule promotion meta-cards', () => {
  function makePromotionRules(overrides: Partial<import('../src/rules.js').PromotionConfig> = {}): Rules {
    return {
      blacklist: [],
      redaction: [],
      queue: { target_depth: 10, low_water_mark: 1, batch_threshold: 999, exploration_slots: 0 },
      urgent_senders: [],
      floor: [],
      reversibility: [{ action: 'archive', reversible: true }],
      verifier: { interval_minutes: 60 },
      promotion: { threshold: 3, cooldown_minutes: 1440, interval_minutes: 120, ...overrides },
    };
  }

  it('promoter detects pattern and surfaces a meta-card', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-009-promote-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([]);
    const journalPath = join(dir, 'journal.jsonl');
    const rules = makePromotionRules({ threshold: 3 });

    // Seed the journal with 3 archive actions from substack.com
    const { appendJournal: aj } = await import('../src/journal.js');
    for (let i = 0; i < 3; i++) {
      aj(journalPath, {
        kind: 'action',
        goalId: `g${i}`,
        messageId: `m${i}`,
        senderDomain: 'substack.com',
        action: 'archive',
        transport: 'gmail',
        title: `Archive newsletter`,
      });
    }

    const server = createExecutorServer({
      gmail,
      journalPath,
      plan: trivialPlan,
      getRules: () => rules,
      rulesDir: dir,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;
    const base = `http://127.0.0.1:${port}`;

    // Trigger the promoter
    const promRes = await fetch(`${base}/promoter/run`, { method: 'POST' });
    expect(promRes.status).toBe(200);

    // Queue should have a promotion meta-card
    const queueRes = await fetch(`${base}/queue`);
    const q = (await queueRes.json()) as { depth: number; cards: Array<{ id: string; title: string; reason: string }> };
    expect(q.depth).toBe(1);
    expect(q.cards[0].id).toContain('meta-promote-');
    expect(q.cards[0].title).toContain('substack.com');
    expect(q.cards[0].reason).toContain('3');

    await new Promise<void>((r) => server.close(() => r()));
  });

  it('approving a promotion meta-card writes rule to gmail.md', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-009-approve-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([]);
    const journalPath = join(dir, 'journal.jsonl');
    const rules = makePromotionRules({ threshold: 3 });

    const { appendJournal: aj } = await import('../src/journal.js');
    for (let i = 0; i < 3; i++) {
      aj(journalPath, {
        kind: 'action',
        goalId: `g${i}`,
        messageId: `m${i}`,
        senderDomain: 'substack.com',
        action: 'archive',
        transport: 'gmail',
      });
    }

    const server = createExecutorServer({
      gmail,
      journalPath,
      plan: trivialPlan,
      getRules: () => rules,
      rulesDir: dir,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;
    const base = `http://127.0.0.1:${port}`;

    await fetch(`${base}/promoter/run`, { method: 'POST' });

    const cardRes = await fetch(`${base}/card`);
    const goal = (await cardRes.json()) as { id: string };

    // Approve the promotion
    const decRes = await fetch(`${base}/card/${goal.id}/decision`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ decision: 'approve' }),
    });
    expect(decRes.status).toBe(200);

    // gmail.md should contain the new rule
    const gmailMd = readFileSync(join(dir, 'gmail.md'), 'utf8');
    expect(gmailMd).toContain('substack.com');
    expect(gmailMd).toContain('archive');

    // Journal should have a rule_promoted entry
    const journal = readFileSync(journalPath, 'utf8').trim().split('\n');
    const promoted = journal.map((l) => JSON.parse(l)).find((e: JournalEntry) => e.kind === 'rule_promoted');
    expect(promoted).toBeDefined();
    expect(promoted.patternKey).toBe('gmail::archive::substack.com');

    await new Promise<void>((r) => server.close(() => r()));
  });

  it('rejecting a promotion journals a rejection and prevents re-proposal', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-009-reject-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([]);
    const journalPath = join(dir, 'journal.jsonl');
    const rules = makePromotionRules({ threshold: 3 });

    const { appendJournal: aj } = await import('../src/journal.js');
    for (let i = 0; i < 5; i++) {
      aj(journalPath, {
        kind: 'action',
        goalId: `g${i}`,
        messageId: `m${i}`,
        senderDomain: 'substack.com',
        action: 'archive',
        transport: 'gmail',
      });
    }

    const server = createExecutorServer({
      gmail,
      journalPath,
      plan: trivialPlan,
      getRules: () => rules,
      rulesDir: dir,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;
    const base = `http://127.0.0.1:${port}`;

    // Trigger and get the meta-card
    await fetch(`${base}/promoter/run`, { method: 'POST' });
    const cardRes = await fetch(`${base}/card`);
    const goal = (await cardRes.json()) as { id: string };

    // Reject it
    await fetch(`${base}/card/${goal.id}/decision`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ decision: 'reject' }),
    });

    // Journal should have a promotion_rejected entry
    const journal = readFileSync(journalPath, 'utf8').trim().split('\n');
    const rejected = journal.map((l) => JSON.parse(l)).find((e: JournalEntry) => e.kind === 'promotion_rejected');
    expect(rejected).toBeDefined();
    expect(rejected.patternKey).toBe('gmail::archive::substack.com');

    // Running promoter again should NOT create a new meta-card (cooldown)
    await fetch(`${base}/promoter/run`, { method: 'POST' });
    const q = (await (await fetch(`${base}/queue`)).json()) as { depth: number };
    expect(q.depth).toBe(0);

    await new Promise<void>((r) => server.close(() => r()));
  });

  it('promoter does not re-propose already promoted rules', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-009-dedup-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([]);
    const journalPath = join(dir, 'journal.jsonl');
    const rules = makePromotionRules({ threshold: 3 });

    const { appendJournal: aj } = await import('../src/journal.js');
    for (let i = 0; i < 5; i++) {
      aj(journalPath, {
        kind: 'action',
        goalId: `g${i}`,
        messageId: `m${i}`,
        senderDomain: 'substack.com',
        action: 'archive',
        transport: 'gmail',
      });
    }

    const server = createExecutorServer({
      gmail,
      journalPath,
      plan: trivialPlan,
      getRules: () => rules,
      rulesDir: dir,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;
    const base = `http://127.0.0.1:${port}`;

    // First run and approve
    await fetch(`${base}/promoter/run`, { method: 'POST' });
    const cardRes = await fetch(`${base}/card`);
    const goal = (await cardRes.json()) as { id: string };
    await fetch(`${base}/card/${goal.id}/decision`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ decision: 'approve' }),
    });

    // Second run should not create a new meta-card
    await fetch(`${base}/promoter/run`, { method: 'POST' });
    const q = (await (await fetch(`${base}/queue`)).json()) as { depth: number };
    expect(q.depth).toBe(0);

    await new Promise<void>((r) => server.close(() => r()));
  });

  it('promotion config parsed from principles.md', () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-009-config-'));
    writeFileSync(join(dir, 'principles.md'), `promotion:\n  threshold: 10\n  cooldown_minutes: 720\n  interval_minutes: 60\n`);
    const rules = loadRules(dir);
    expect(rules.promotion).toEqual({ threshold: 10, cooldown_minutes: 720, interval_minutes: 60 });
  });

  it('promotion config defaults when not specified', () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-009-config-default-'));
    writeFileSync(join(dir, 'principles.md'), `blacklist: []\n`);
    const rules = loadRules(dir);
    expect(rules.promotion).toEqual({ threshold: 5, cooldown_minutes: 1440, interval_minutes: 120 });
  });
});

describe('slice 010 learned ranker with exploration', () => {
  function makeRankerRules(overrides: Partial<{ exploration_slots: number; target_depth: number }> = {}): Rules {
    return {
      blacklist: [],
      redaction: [],
      queue: {
        target_depth: overrides.target_depth ?? 5,
        low_water_mark: 1,
        batch_threshold: 999,
        exploration_slots: overrides.exploration_slots ?? 1,
      },
      urgent_senders: [],
      floor: [],
      reversibility: [{ action: 'archive', reversible: true }],
      verifier: { interval_minutes: 60 },
      promotion: { threshold: 5, cooldown_minutes: 1440, interval_minutes: 120 },
    };
  }

  it('decision journal entries include features for weight learning', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-010-features-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([
      { id: 'm1', from: 'alice@example.com', subject: 'hello', body: 'body', unread: true },
    ]);
    const journalPath = join(dir, 'journal.jsonl');
    const rules = makeRankerRules({ exploration_slots: 0 });

    const server = createExecutorServer({
      gmail,
      journalPath,
      plan: trivialPlan,
      getRules: () => rules,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;
    const base = `http://127.0.0.1:${port}`;

    const cardRes = await fetch(`${base}/card`);
    const goal = (await cardRes.json()) as { id: string };

    // Defer instead of approve (so it hits the generic decision path)
    await fetch(`${base}/card/${goal.id}/decision`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ decision: 'defer' }),
    });

    const journal = readFileSync(journalPath, 'utf8').trim().split('\n');
    const entry = JSON.parse(journal[0]) as JournalEntry;
    expect(entry.kind).toBe('decision');
    expect(entry.features).toBeDefined();
    expect((entry.features as Record<string, unknown>).urgency).toBeDefined();

    await new Promise<void>((r) => server.close(() => r()));
  });

  it('/queue exposes per-card feature breakdown', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-010-breakdown-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([
      { id: 'm1', from: 'alice@example.com', subject: 'hello', body: 'body', unread: true },
    ]);
    const journalPath = join(dir, 'journal.jsonl');
    const rules = makeRankerRules({ exploration_slots: 0 });

    const server = createExecutorServer({
      gmail,
      journalPath,
      plan: trivialPlan,
      getRules: () => rules,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;
    const base = `http://127.0.0.1:${port}`;

    // Trigger refill
    await fetch(`${base}/refill`, { method: 'POST' });

    const queueRes = await fetch(`${base}/queue`);
    const q = (await queueRes.json()) as { depth: number; cards: Array<{ id: string; breakdown?: Record<string, number> }> };
    expect(q.depth).toBeGreaterThanOrEqual(1);
    const card = q.cards[0];
    expect(card.breakdown).toBeDefined();
    expect(card.breakdown!.total).toBeDefined();
    expect(card.breakdown!.urgency).toBeDefined();

    await new Promise<void>((r) => server.close(() => r()));
  });

  it('exploration slots reserve positions for high-uncertainty candidates', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-010-explore-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    // Create several messages
    gmail.save([
      { id: 'm1', from: 'known@example.com', subject: 'msg1', body: 'body', unread: true },
      { id: 'm2', from: 'known@example.com', subject: 'msg2', body: 'body', unread: true },
      { id: 'm3', from: 'unknown@newdomain.com', subject: 'msg3', body: 'body', unread: true },
    ]);
    const journalPath = join(dir, 'journal.jsonl');

    // Seed journal with many decisions on m1 to give it low uncertainty
    const { appendJournal: aj } = await import('../src/journal.js');
    for (let i = 0; i < 5; i++) {
      aj(journalPath, {
        ts: new Date().toISOString(),
        kind: 'decision',
        decision: 'approve',
        goalId: `g-${i}`,
        messageId: 'm1',
        features: { urgency: 'low', deadline: null, amount: null, waiting_on_user: false, category: 'other' },
      });
    }

    const rules = makeRankerRules({ exploration_slots: 1, target_depth: 5 });

    const server = createExecutorServer({
      gmail,
      journalPath,
      plan: trivialPlan,
      getRules: () => rules,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;
    const base = `http://127.0.0.1:${port}`;

    await fetch(`${base}/refill`, { method: 'POST' });

    const queueRes = await fetch(`${base}/queue`);
    const q = (await queueRes.json()) as { depth: number; cards: Array<{ id: string; exploration?: boolean }> };
    // At least one card should be marked as exploration
    const explorationCards = q.cards.filter((c) => c.exploration === true);
    expect(explorationCards.length).toBeGreaterThanOrEqual(1);

    await new Promise<void>((r) => server.close(() => r()));
  });

  it('learned weights from swipe history affect ranking order', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-010-weights-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    const journalPath = join(dir, 'journal.jsonl');

    // Seed journal: many approvals for items with amounts
    const { appendJournal: aj } = await import('../src/journal.js');
    for (let i = 0; i < 20; i++) {
      aj(journalPath, {
        kind: 'decision',
        decision: 'approve',
        goalId: `g-${i}`,
        messageId: `m-hist-${i}`,
        features: { urgency: 'low', deadline: null, amount: '£100', waiting_on_user: false, category: 'transaction' },
      });
    }

    // Now put two fresh messages: one with amount (low urgency), one without (medium urgency)
    gmail.save([
      { id: 'no-amount', from: 'alice@example.com', subject: 'no amount', body: 'body', unread: true },
      { id: 'with-amount', from: 'bob@example.com', subject: 'has amount', body: 'body', unread: true },
    ]);

    // Custom triage: assign different features to each
    const triage = async (msg: import('../src/gmail/fake.js').GmailMessage) => {
      if (msg.id === 'with-amount') {
        return {
          features: { urgency: 'low' as const, deadline: null, amount: '£50', waiting_on_user: false, category: 'transaction' },
          snippet: 'has amount',
        };
      }
      return {
        features: { urgency: 'medium' as const, deadline: null, amount: null, waiting_on_user: false, category: 'other' },
        snippet: 'no amount',
      };
    };

    const rules = makeRankerRules({ exploration_slots: 0, target_depth: 5 });

    const server = createExecutorServer({
      gmail,
      journalPath,
      triage,
      plan: trivialPlan,
      getRules: () => rules,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;
    const base = `http://127.0.0.1:${port}`;

    await fetch(`${base}/refill`, { method: 'POST' });

    const queueRes = await fetch(`${base}/queue`);
    const q = (await queueRes.json()) as { depth: number; cards: Array<{ id: string; messageId: string }> };
    // With learned weights boosting has_amount, with-amount should come first
    expect(q.cards[0].id).toContain('with-amount');

    await new Promise<void>((r) => server.close(() => r()));
  });

  it('floor reservations from slice 005 are still honoured with learned ranker', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-010-floor-'));
    const gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([
      { id: 'm-high', from: 'alice@example.com', subject: 'urgent', body: 'body', unread: true },
      { id: 'm-deadline', from: 'bob@example.com', subject: 'deadline', body: 'body', unread: true },
    ]);
    const journalPath = join(dir, 'journal.jsonl');

    // Custom triage
    const triage = async (msg: import('../src/gmail/fake.js').GmailMessage) => {
      if (msg.id === 'm-deadline') {
        return {
          features: { urgency: 'low' as const, deadline: '2026-04-10T00:00:00Z', amount: null, waiting_on_user: false, category: 'work' },
          snippet: 'deadline item',
        };
      }
      return {
        features: { urgency: 'high' as const, deadline: null, amount: null, waiting_on_user: false, category: 'other' },
        snippet: 'high urgency',
      };
    };

    const rules: Rules = {
      ...makeRankerRules({ exploration_slots: 0, target_depth: 5 }),
      floor: [{ match: { deadline_within_hours: 72 }, slots: 1 }],
    };

    const server = createExecutorServer({
      gmail,
      journalPath,
      triage,
      plan: trivialPlan,
      getRules: () => rules,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;
    const base = `http://127.0.0.1:${port}`;

    await fetch(`${base}/refill`, { method: 'POST' });

    const queueRes = await fetch(`${base}/queue`);
    const q = (await queueRes.json()) as { depth: number; cards: Array<{ id: string }> };
    // Floor reservation should put deadline item first despite low urgency
    expect(q.cards[0].id).toContain('m-deadline');

    await new Promise<void>((r) => server.close(() => r()));
  });

  it('exploration_slots config parsed from principles.md', () => {
    const dir = mkdtempSync(join(tmpdir(), 'steward-010-config-'));
    writeFileSync(join(dir, 'principles.md'), `queue:\n  target_depth: 5\n  low_water_mark: 2\n  batch_threshold: 3\n  exploration_slots: 2\n`);
    const rules = loadRules(dir);
    expect(rules.queue.exploration_slots).toBe(2);
  });
});
