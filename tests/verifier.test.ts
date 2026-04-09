import { describe, it, expect } from 'vitest';
import { detectAnomalies, type Anomaly } from '../src/verifier.js';
import { FakeGmail } from '../src/gmail/fake.js';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { appendJournal } from '../src/journal.js';

function tmpDir() {
  return mkdtempSync(join(tmpdir(), 'steward-verifier-'));
}

describe('verifier — detectAnomalies', () => {
  it('returns empty when journal has no action entries', async () => {
    const dir = tmpDir();
    const gmail = new FakeGmail(join(dir, 'inbox.json'));
    gmail.save([]);
    const journalPath = join(dir, 'journal.jsonl');
    // Write a non-action entry
    appendJournal(journalPath, { kind: 'decision', decision: 'reject', goalId: 'g1', messageId: 'm1' });

    const anomalies = await detectAnomalies(journalPath, gmail);
    expect(anomalies).toEqual([]);
  });

  it('detects user-unarchive: message was archived but is no longer', async () => {
    const dir = tmpDir();
    const gmail = new FakeGmail(join(dir, 'inbox.json'));
    // Message exists but archived flag is false (user unarchived it)
    gmail.save([{ id: 'm1', from: 'alice@example.com', subject: 'hello', body: 'body', unread: true, archived: false }]);
    const journalPath = join(dir, 'journal.jsonl');
    appendJournal(journalPath, {
      kind: 'action',
      goalId: 'g1',
      messageId: 'm1',
      title: 'Archive newsletter',
      outcomes: [{ success: true, action_taken: 'archive', messageId: 'm1' }],
      verification: { verified: true, sample: [] },
    });

    const anomalies = await detectAnomalies(journalPath, gmail);
    expect(anomalies).toHaveLength(1);
    expect(anomalies[0]).toMatchObject({
      type: 'unarchive',
      messageId: 'm1',
      goalId: 'g1',
    });
    expect(anomalies[0].description).toContain('unarchived');
  });

  it('does not flag messages that are still archived', async () => {
    const dir = tmpDir();
    const gmail = new FakeGmail(join(dir, 'inbox.json'));
    gmail.save([{ id: 'm1', from: 'alice@example.com', subject: 'hello', body: 'body', unread: false, archived: true }]);
    const journalPath = join(dir, 'journal.jsonl');
    appendJournal(journalPath, {
      kind: 'action',
      goalId: 'g1',
      messageId: 'm1',
      title: 'Archive newsletter',
      outcomes: [{ success: true, action_taken: 'archive', messageId: 'm1' }],
      verification: { verified: true, sample: [] },
    });

    const anomalies = await detectAnomalies(journalPath, gmail);
    expect(anomalies).toEqual([]);
  });

  it('detects reply-after-archive via newer unread message from same sender', async () => {
    const dir = tmpDir();
    const gmail = new FakeGmail(join(dir, 'inbox.json'));
    // Original message is archived, but a new reply appeared from same sender
    gmail.save([
      { id: 'm1', from: 'alice@example.com', subject: 'hello', body: 'body', unread: false, archived: true },
      { id: 'm2', from: 'alice@example.com', subject: 'Re: hello', body: 'follow-up', unread: true },
    ]);
    const journalPath = join(dir, 'journal.jsonl');
    appendJournal(journalPath, {
      kind: 'action',
      goalId: 'g1',
      messageId: 'm1',
      title: 'Archive newsletter from alice',
      outcomes: [{ success: true, action_taken: 'archive', messageId: 'm1' }],
      verification: { verified: true, sample: [] },
    });

    const anomalies = await detectAnomalies(journalPath, gmail);
    expect(anomalies).toHaveLength(1);
    expect(anomalies[0]).toMatchObject({
      type: 'reply_after_archive',
      messageId: 'm1',
      goalId: 'g1',
    });
    expect(anomalies[0].description).toContain('reply');
  });

  it('handles batch actions — checks all messageIds', async () => {
    const dir = tmpDir();
    const gmail = new FakeGmail(join(dir, 'inbox.json'));
    gmail.save([
      { id: 'm1', from: 'news@sub.com', subject: 'Issue 1', body: '', unread: false, archived: true },
      { id: 'm2', from: 'news@sub.com', subject: 'Issue 2', body: '', unread: true, archived: false }, // unarchived!
      { id: 'm3', from: 'news@sub.com', subject: 'Issue 3', body: '', unread: false, archived: true },
    ]);
    const journalPath = join(dir, 'journal.jsonl');
    appendJournal(journalPath, {
      kind: 'action',
      goalId: 'g-batch',
      messageId: 'm1',
      messageIds: ['m1', 'm2', 'm3'],
      batchSize: 3,
      title: 'Archive 3 newsletter from sub.com',
      outcomes: [
        { success: true, action_taken: 'archive', messageId: 'm1' },
        { success: true, action_taken: 'archive', messageId: 'm2' },
        { success: true, action_taken: 'archive', messageId: 'm3' },
      ],
      verification: { verified: true, sample: [] },
    });

    const anomalies = await detectAnomalies(journalPath, gmail);
    expect(anomalies).toHaveLength(1);
    expect(anomalies[0]).toMatchObject({
      type: 'unarchive',
      messageId: 'm2',
      goalId: 'g-batch',
    });
  });

  it('skips journal entries that have already been verified (dedup)', async () => {
    const dir = tmpDir();
    const gmail = new FakeGmail(join(dir, 'inbox.json'));
    gmail.save([{ id: 'm1', from: 'a@b.com', subject: 'x', body: '', unread: true, archived: false }]);
    const journalPath = join(dir, 'journal.jsonl');
    appendJournal(journalPath, {
      kind: 'action',
      goalId: 'g1',
      messageId: 'm1',
      title: 'Archive',
      outcomes: [{ success: true, action_taken: 'archive', messageId: 'm1' }],
      verification: { verified: true, sample: [] },
    });
    // A verifier_anomaly entry already exists for this goalId
    appendJournal(journalPath, {
      kind: 'verifier_anomaly',
      goalId: 'g1',
      messageId: 'm1',
      anomalyType: 'unarchive',
    });

    const anomalies = await detectAnomalies(journalPath, gmail);
    expect(anomalies).toEqual([]);
  });
});
