import { createInterface } from 'node:readline';
import Anthropic from '@anthropic-ai/sdk';
import type { RedactedMessage } from '../redactor.js';
import type { TriageFeatures } from '../triage.js';

export interface Goal {
  id: string;
  title: string;
  reason: string;
  messageId: string;
  /** Transport the action targets (e.g. 'gmail', 'calendar'). */
  transport?: string;
  /** Action class (e.g. 'archive', 'send', 'delete'). */
  action?: string;
}

/** Input to the planner: redacted message plus triage features. */
export interface PlannerInput {
  message: RedactedMessage;
  features: TriageFeatures;
  snippet: string;
}

const PLANNER_SYSTEM = `You are a personal email assistant. Given a redacted email summary and triage features, produce a single goal for the user.

Respond with valid JSON only, no markdown, no explanation.

Schema:
{
  "title": "<short action-oriented title, max 80 chars>",
  "reason": "<one-sentence explanation of why this goal matters>",
  "transport": "gmail",
  "action": "<one of: archive, reply, read, flag, other>"
}`;

/**
 * Trivial planner for the slice-002 tracer bullet: one redacted message
 * in, one goal out. Kept as a fallback and for tests that don't need LLM.
 */
export function planGoal(message: RedactedMessage): Goal {
  return {
    id: `g-${message.id}`,
    title: `Review message from ${message.fromDomain}`,
    reason: `Subject: ${message.subject}`,
    messageId: message.id,
    transport: 'gmail',
    action: 'archive',
  };
}

export type PlanFn = (input: PlannerInput) => Promise<Goal>;

/**
 * Create a planner function backed by an expensive frontier model.
 * Injectable so tests can substitute a fake.
 */
export function createPlanner(
  client: Anthropic,
  model = 'claude-sonnet-4-20250514',
): PlanFn {
  return async (input: PlannerInput): Promise<Goal> => {
    const userContent = [
      `Domain: ${input.message.fromDomain}`,
      `Subject: ${input.message.subject}`,
      `Snippet: ${input.snippet}`,
      `Category: ${input.features.category}`,
      `Urgency: ${input.features.urgency}`,
      `Deadline: ${input.features.deadline ?? 'none'}`,
      `Amount: ${input.features.amount ?? 'none'}`,
      `Waiting on user: ${input.features.waiting_on_user}`,
    ].join('\n');

    const response = await client.messages.create({
      model,
      max_tokens: 512,
      system: PLANNER_SYSTEM,
      messages: [{ role: 'user', content: userContent }],
    });

    const text = response.content
      .filter((b) => b.type === 'text')
      .map((b) => b.text)
      .join('');

    const parsed = JSON.parse(text) as {
      title: string;
      reason: string;
      transport?: string;
      action?: string;
    };

    return {
      id: `g-${input.message.id}`,
      title: parsed.title,
      reason: parsed.reason,
      messageId: input.message.id,
      transport: parsed.transport ?? 'gmail',
      action: parsed.action ?? 'archive',
    };
  };
}

function main(): void {
  const rl = createInterface({ input: process.stdin });
  rl.on('line', (line) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    try {
      const input = JSON.parse(trimmed) as PlannerInput | RedactedMessage;
      // Support both old-style (RedactedMessage) and new-style (PlannerInput)
      let goal: Goal;
      if ('message' in input) {
        // New-style: has triage features — but subprocess uses trivial planner
        // (real LLM call happens in-process, not via subprocess, in slice 004)
        goal = planGoal(input.message);
      } else {
        goal = planGoal(input);
      }
      process.stdout.write(JSON.stringify(goal) + '\n');
    } catch (err) {
      process.stdout.write(
        JSON.stringify({ error: (err as Error).message }) + '\n',
      );
    }
  });
}

// Only run when invoked as a script, not when imported by tests.
const invokedDirectly =
  process.argv[1] && process.argv[1].endsWith('planner/index.ts');
if (invokedDirectly) {
  main();
}
