import { describe, it, expect } from 'vitest';
import {
  createFakeBrowserSubAgent,
  type BrowserInstruction,
} from '../src/browser/subagent.js';

describe('Browser sub-agent (fake)', () => {
  const responses = new Map([
    ['https://example.com/invoice', { title: 'Invoice #1234', text: 'Amount due: £250.00\nDue date: 2026-05-01' }],
    ['https://example.com/status', { title: 'Order Status', text: 'Your order is shipped' }],
  ]);

  const agent = createFakeBrowserSubAgent(responses);

  it('dispatches browser_read and returns extracted content', async () => {
    const instruction: BrowserInstruction = {
      capability: 'browser_read',
      url: 'https://example.com/invoice',
      instruction: 'Extract the amount due and due date',
    };

    const outcome = await agent.dispatch(instruction);
    expect(outcome.success).toBe(true);
    expect(outcome.action_taken).toBe('browser_read');
    expect(outcome.pageTitle).toBe('Invoice #1234');
    expect(outcome.textContent).toContain('£250.00');
    expect(outcome.textContent).toContain('2026-05-01');
  });

  it('returns failure for unknown URL', async () => {
    const instruction: BrowserInstruction = {
      capability: 'browser_read',
      url: 'https://unknown.com',
      instruction: 'Read this page',
    };

    const outcome = await agent.dispatch(instruction);
    expect(outcome.success).toBe(false);
    expect(outcome.error).toContain('no canned response');
  });

  it('returns failure for unknown capability', async () => {
    const outcome = await agent.dispatch({
      capability: 'browser_write' as 'browser_read',
      url: 'https://example.com/invoice',
      instruction: 'Submit the form',
    });

    expect(outcome.success).toBe(false);
    expect(outcome.error).toContain('unknown capability');
  });

  it('verification confirms URL was visited', async () => {
    const verification = await agent.verify('https://example.com/invoice');
    expect(verification.verified).toBe(true);
    expect(verification.actual_title).toBe('Invoice #1234');
  });

  it('verification fails for unknown URL', async () => {
    const verification = await agent.verify('https://unknown.com');
    expect(verification.verified).toBe(false);
  });
});
