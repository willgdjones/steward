import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import { FakeGmail } from '../gmail/fake.js';
import { createExecutorServer } from './server.js';
import { runPlanner } from './plannerClient.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const stateDir = resolve(process.env.STEWARD_STATE_DIR ?? 'state');
const port = Number(process.env.STEWARD_PORT ?? 8731);

const gmail = new FakeGmail(join(stateDir, 'fake_inbox.json'));
const journalPath = join(stateDir, 'journal.jsonl');
const plannerScript = resolve(__dirname, '..', 'planner', 'index.ts');

const server = createExecutorServer({
  gmail,
  journalPath,
  plan: (msg) => runPlanner(msg, plannerScript),
});

server.listen(port, () => {
  // eslint-disable-next-line no-console
  console.log(`steward executor listening on http://localhost:${port}`);
});
