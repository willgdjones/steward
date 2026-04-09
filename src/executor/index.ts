import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import Anthropic from '@anthropic-ai/sdk';
import { FakeGmail } from '../gmail/fake.js';
import { createExecutorServer } from './server.js';
import { runPlanner } from './plannerClient.js';
import { loadRules, watchRules, type Rules } from '../rules.js';
import { createTriage } from '../triage.js';
import { createPlanner, planGoal, type PlannerInput } from '../planner/index.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const stateDir = resolve(process.env.STEWARD_STATE_DIR ?? 'state');
const port = Number(process.env.STEWARD_PORT ?? 8731);
const rulesDir = resolve(process.env.STEWARD_RULES_DIR ?? stateDir);
const searchQuery = process.env.STEWARD_GMAIL_QUERY ?? 'is:unread';

const gmail = new FakeGmail(join(stateDir, 'fake_inbox.json'));
const journalPath = join(stateDir, 'journal.jsonl');
const plannerScript = resolve(__dirname, '..', 'planner', 'index.ts');

let rules: Rules = loadRules(rulesDir);
watchRules(rulesDir, (updated) => {
  rules = updated;
  // eslint-disable-next-line no-console
  console.log('rules reloaded');
});

// Use frontier models if ANTHROPIC_API_KEY is set; fall back to trivial planner
const useAI = !!process.env.ANTHROPIC_API_KEY;

const triage = useAI ? createTriage(new Anthropic()) : undefined;
const planFn = useAI
  ? createPlanner(new Anthropic())
  : undefined;

const server = createExecutorServer({
  gmail,
  journalPath,
  triage,
  plan: planFn
    ? (input) => planFn(input)
    : (input) => Promise.resolve(planGoal(input.message)),
  getRules: () => rules,
  searchQuery,
});

server.listen(port, () => {
  // eslint-disable-next-line no-console
  console.log(`steward executor listening on http://localhost:${port}`);
  if (useAI) {
    // eslint-disable-next-line no-console
    console.log('frontier triage + planner active (Haiku → Sonnet)');
  } else {
    // eslint-disable-next-line no-console
    console.log('trivial planner (no ANTHROPIC_API_KEY)');
  }
});
