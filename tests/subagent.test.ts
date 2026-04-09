import { describe, it, expect, beforeEach } from 'vitest';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { FakeGmail } from '../src/gmail/fake.js';
import {
  createGmailSubAgent,
  type SubAgentInstruction,
  type SubAgentOutcome,
} from '../src/gmail/subagent.js';

describe('Gmail sub-agent', () => {
  let dir: string;
  let gmail: FakeGmail;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), 'steward-subagent-'));
    gmail = new FakeGmail(join(dir, 'fake_inbox.json'));
    gmail.save([
      { id: 'm1', from: 'alice@example.com', subject: 'hello', body: 'body', unread: true, archived: false },
      { id: 'm2', from: 'bob@example.com', subject: 'meeting', body: 'details', unread: true, archived: false },
    ]);
  });

  it('archives a message and returns a structured outcome', async () => {
    const agent = createGmailSubAgent(gmail);
    const instruction: SubAgentInstruction = {
      capability: 'archive',
      messageId: 'm1',
      instruction: 'Archive this newsletter from alice@example.com',
    };

    const outcome = await agent.dispatch(instruction);

    expect(outcome.success).toBe(true);
    expect(outcome.action_taken).toBe('archive');
    expect(outcome.messageId).toBe('m1');

    // Verify the message is actually archived in FakeGmail
    const msg = gmail.getById('m1');
    expect(msg).not.toBeNull();
    expect(msg!.archived).toBe(true);
  });

  it('verification confirms archive state', async () => {
    const agent = createGmailSubAgent(gmail);

    // Archive first
    await agent.dispatch({
      capability: 'archive',
      messageId: 'm1',
      instruction: 'Archive this message',
    });

    // Verify
    const verification = await agent.verify('m1', 'archive');
    expect(verification.verified).toBe(true);
    expect(verification.actual_state).toBe('archived');
  });

  it('verification detects non-archived state', async () => {
    const agent = createGmailSubAgent(gmail);

    // Don't archive — just verify
    const verification = await agent.verify('m1', 'archive');
    expect(verification.verified).toBe(false);
    expect(verification.actual_state).toBe('not_archived');
  });

  it('returns failure for unknown message', async () => {
    const agent = createGmailSubAgent(gmail);

    const outcome = await agent.dispatch({
      capability: 'archive',
      messageId: 'nonexistent',
      instruction: 'Archive this message',
    });

    expect(outcome.success).toBe(false);
    expect(outcome.error).toContain('not found');
  });

  it('returns failure for unknown capability', async () => {
    const agent = createGmailSubAgent(gmail);

    const outcome = await agent.dispatch({
      capability: 'delete',
      messageId: 'm1',
      instruction: 'Delete this message',
    });

    expect(outcome.success).toBe(false);
    expect(outcome.error).toContain('unknown capability');
  });

  it('archived messages no longer appear in search', async () => {
    const agent = createGmailSubAgent(gmail);
    await agent.dispatch({
      capability: 'archive',
      messageId: 'm1',
      instruction: 'Archive this',
    });

    const results = gmail.search('is:unread');
    expect(results).toHaveLength(1);
    expect(results[0].id).toBe('m2');
  });

  it('creates a draft reply and returns the draft ID', async () => {
    const agent = createGmailSubAgent(gmail);
    const outcome = await agent.dispatch({
      capability: 'draft_reply',
      messageId: 'm1',
      instruction: 'Reply to alice',
      draftBody: 'Thanks for your message!',
    });

    expect(outcome.success).toBe(true);
    expect(outcome.action_taken).toBe('draft_reply');
    expect(outcome.draftId).toBeDefined();

    // Verify the draft exists in FakeGmail
    const draft = gmail.getDraft(outcome.draftId!);
    expect(draft).not.toBeNull();
    expect(draft!.to).toBe('alice@example.com');
    expect(draft!.subject).toBe('Re: hello');
    expect(draft!.body).toBe('Thanks for your message!');
  });

  it('draft_reply verification confirms the draft exists', async () => {
    const agent = createGmailSubAgent(gmail);
    const outcome = await agent.dispatch({
      capability: 'draft_reply',
      messageId: 'm1',
      instruction: 'Reply',
      draftBody: 'Thanks!',
    });

    const verification = await agent.verify('m1', 'draft_reply', { draftId: outcome.draftId });
    expect(verification.verified).toBe(true);
    expect(verification.actual_state).toBe('draft_exists');
  });

  it('draft_reply fails for nonexistent message', async () => {
    const agent = createGmailSubAgent(gmail);
    const outcome = await agent.dispatch({
      capability: 'draft_reply',
      messageId: 'nonexistent',
      instruction: 'Reply',
      draftBody: 'Hello',
    });

    expect(outcome.success).toBe(false);
    expect(outcome.error).toContain('not found');
  });

  it('draft_reply verification fails without draft ID', async () => {
    const agent = createGmailSubAgent(gmail);
    const verification = await agent.verify('m1', 'draft_reply');
    expect(verification.verified).toBe(false);
    expect(verification.actual_state).toBe('no_draft_id');
  });
});
