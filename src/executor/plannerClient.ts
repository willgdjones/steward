import { spawn } from 'node:child_process';
import type { RedactedMessage } from '../redactor.js';
import type { Goal, PlannerInput } from '../planner/index.js';

/**
 * Spawn the planner as a separate child process. The executor's
 * environment is filtered before being handed to the planner so that
 * credential-bearing variables (anything matching CRED / TOKEN / SECRET
 * / KEY / STEWARD_CREDENTIALS_DIR) never reach the LLM-side process.
 *
 * This is the slice-002 enforcement of decision §15 (structural
 * separation). Issue 012 will replace env-based creds with op:// refs.
 */
const FORBIDDEN_ENV = /^(.*(CRED|TOKEN|SECRET|KEY|PASSWORD).*|STEWARD_CREDENTIALS_DIR)$/i;

export function sanitiseEnvForPlanner(
  env: NodeJS.ProcessEnv = process.env,
): NodeJS.ProcessEnv {
  const out: NodeJS.ProcessEnv = {};
  for (const [k, v] of Object.entries(env)) {
    if (FORBIDDEN_ENV.test(k)) continue;
    out[k] = v;
  }
  return out;
}

export async function runPlanner(
  input: PlannerInput | RedactedMessage,
  plannerScript: string,
): Promise<Goal> {
  return new Promise((resolve, reject) => {
    const child = spawn('npx', ['tsx', plannerScript], {
      env: sanitiseEnvForPlanner(),
      stdio: ['pipe', 'pipe', 'inherit'],
    });
    let buf = '';
    child.stdout.on('data', (chunk: Buffer) => {
      buf += chunk.toString('utf8');
    });
    child.on('error', reject);
    child.on('close', () => {
      const line = buf.trim().split('\n').pop() ?? '';
      try {
        const parsed = JSON.parse(line) as Goal | { error: string };
        if ('error' in parsed) reject(new Error(parsed.error));
        else resolve(parsed);
      } catch (err) {
        reject(new Error(`planner output not JSON: ${line}`));
      }
    });
    child.stdin.write(JSON.stringify(input) + '\n');
    child.stdin.end();
  });
}
