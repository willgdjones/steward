import { createInterface } from 'node:readline';
import type { RedactedMessage } from '../redactor.js';

export interface Goal {
  id: string;
  title: string;
  reason: string;
  messageId: string;
}

/**
 * Trivial planner for the slice-002 tracer bullet: one redacted message
 * in, one goal out. Real triage + frontier planning lands in issue 004.
 *
 * Runs as a child process spawned by the executor. Communicates over
 * stdio with newline-delimited JSON so there is no shared filesystem
 * path between executor and planner — credentials live only on the
 * executor side (see plannerClient.spawnPlanner).
 */
export function planGoal(message: RedactedMessage): Goal {
  return {
    id: `g-${message.id}`,
    title: `Review message from ${message.fromDomain}`,
    reason: `Subject: ${message.subject}`,
    messageId: message.id,
  };
}

function main(): void {
  const rl = createInterface({ input: process.stdin });
  rl.on('line', (line) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    try {
      const message = JSON.parse(trimmed) as RedactedMessage;
      const goal = planGoal(message);
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
