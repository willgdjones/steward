import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { mkdtempSync, readFileSync, existsSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import { FakeGmail } from '../src/gmail/fake.js';
import { createExecutorServer } from '../src/executor/server.js';
import { planGoal } from '../src/planner/index.js';
import { sanitiseEnvForPlanner } from '../src/executor/plannerClient.js';
import { redact } from '../src/redactor.js';
import { loadRules, type Rules } from '../src/rules.js';

const EMPTY_RULES: Rules = { blacklist: [], redaction: [] };

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
      plan: async (msg) => planGoal(msg),
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
      plan: async (msg) => planGoal(msg),
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
      plan: async (msg) => planGoal(msg),
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
    let plannerInput: { subject: string } | null = null;
    const server = createExecutorServer({
      gmail,
      journalPath: join(dir, 'journal.jsonl'),
      plan: async (msg) => {
        plannerInput = msg;
        return planGoal(msg);
      },
      getRules: () => rules,
    });
    await new Promise<void>((r) => server.listen(0, r));
    const { port } = server.address() as AddressInfo;

    await fetch(`http://127.0.0.1:${port}/card`);

    expect(plannerInput).not.toBeNull();
    expect(plannerInput!.subject).toBe('Your card [REDACTED] statement');
    expect(plannerInput!.subject).not.toContain('1234');

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
