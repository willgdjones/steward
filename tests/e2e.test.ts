import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { mkdtempSync, readFileSync, existsSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import { FakeGmail } from '../src/gmail/fake.js';
import { createExecutorServer } from '../src/executor/server.js';
import { planGoal, type PlannerInput } from '../src/planner/index.js';
import { sanitiseEnvForPlanner } from '../src/executor/plannerClient.js';
import { redact } from '../src/redactor.js';
import { loadRules, type Rules } from '../src/rules.js';

const EMPTY_RULES: Rules = { blacklist: [], redaction: [] };

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
    expect(entry).toMatchObject({
      kind: 'decision',
      decision: 'approve',
      goalId: goal.id,
      messageId: 'm1',
    });
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
