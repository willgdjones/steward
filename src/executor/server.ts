import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import { FakeGmail, type GmailMessage } from '../gmail/fake.js';
import { redact, applyRedactionRules, type RedactedMessage } from '../redactor.js';
import type { Goal } from '../planner/index.js';
import { appendJournal } from '../journal.js';
import { checkBlacklist } from '../principlesGate.js';
import type { Rules } from '../rules.js';

export type Decision = 'approve' | 'reject' | 'defer';

export interface ServerDeps {
  gmail: FakeGmail;
  journalPath: string;
  /** Injected so tests can avoid spawning a subprocess. */
  plan: (msg: RedactedMessage) => Promise<Goal>;
  /** Returns current rules; may be updated by file watcher. */
  getRules: () => Rules;
}

interface CardState {
  goal: Goal;
  message: GmailMessage;
}

const WEB_CLIENT_HTML = `<!doctype html>
<html><head><meta charset="utf-8"><title>steward</title>
<style>
body{font-family:system-ui;max-width:480px;margin:2em auto;padding:1em}
.card{border:1px solid #ccc;border-radius:8px;padding:1em;margin-bottom:1em}
button{margin-right:.5em;padding:.5em 1em}
</style></head><body>
<h1>steward</h1>
<div id="card">loading…</div>
<script>
async function load(){
  const r = await fetch('/card');
  if(r.status===204){document.getElementById('card').textContent='no cards';return;}
  const g = await r.json();
  document.getElementById('card').innerHTML =
    '<div class="card"><h2>'+g.title+'</h2><p>'+g.reason+'</p>'+
    '<button onclick="decide(\\''+g.id+'\\',\\'approve\\')">approve</button>'+
    '<button onclick="decide(\\''+g.id+'\\',\\'reject\\')">reject</button>'+
    '<button onclick="decide(\\''+g.id+'\\',\\'defer\\')">defer</button></div>';
}
async function decide(id,d){
  await fetch('/card/'+id+'/decision',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({decision:d})});
  load();
}
load();
</script></body></html>`;

export function createExecutorServer(deps: ServerDeps): Server {
  let current: CardState | null = null;

  async function refill(): Promise<void> {
    if (current) return;
    const message = deps.gmail.readOneUnread();
    if (!message) return;
    const base = redact(message);
    const redacted = applyRedactionRules(base, deps.getRules().redaction);
    const goal = await deps.plan(redacted);
    current = { goal, message };
  }

  function readBody(req: IncomingMessage): Promise<string> {
    return new Promise((resolve, reject) => {
      let data = '';
      req.on('data', (c) => (data += c));
      req.on('end', () => resolve(data));
      req.on('error', reject);
    });
  }

  function send(res: ServerResponse, status: number, body: string, type = 'application/json'): void {
    res.writeHead(status, { 'content-type': type });
    res.end(body);
  }

  return createServer(async (req, res) => {
    try {
      const url = req.url ?? '/';
      if (req.method === 'GET' && (url === '/' || url === '/index.html')) {
        send(res, 200, WEB_CLIENT_HTML, 'text/html; charset=utf-8');
        return;
      }
      if (req.method === 'GET' && url === '/card') {
        await refill();
        if (!current) {
          res.writeHead(204);
          res.end();
          return;
        }
        send(res, 200, JSON.stringify(current.goal));
        return;
      }
      const decisionMatch = url.match(/^\/card\/([^/]+)\/decision$/);
      if (req.method === 'POST' && decisionMatch) {
        const id = decisionMatch[1];
        if (!current || current.goal.id !== id) {
          send(res, 404, JSON.stringify({ error: 'no such card' }));
          return;
        }
        const body = await readBody(req);
        const { decision } = JSON.parse(body || '{}') as { decision: Decision };
        if (decision !== 'approve' && decision !== 'reject' && decision !== 'defer') {
          send(res, 400, JSON.stringify({ error: 'bad decision' }));
          return;
        }
        // Enforce blacklist before dispatching an approved action
        if (decision === 'approve' && current.goal.transport && current.goal.action) {
          const gate = checkBlacklist(
            deps.getRules().blacklist,
            current.goal.transport,
            current.goal.action,
          );
          if (!gate.allowed) {
            appendJournal(deps.journalPath, {
              kind: 'blocked',
              goalId: current.goal.id,
              messageId: current.message.id,
              reason: gate.reason,
            });
            current = null;
            send(res, 403, JSON.stringify({ error: 'blocked', reason: gate.reason }));
            return;
          }
        }
        appendJournal(deps.journalPath, {
          kind: 'decision',
          decision,
          goalId: current.goal.id,
          messageId: current.message.id,
          title: current.goal.title,
        });
        current = null;
        send(res, 200, JSON.stringify({ ok: true }));
        return;
      }
      send(res, 404, JSON.stringify({ error: 'not found' }));
    } catch (err) {
      send(res, 500, JSON.stringify({ error: (err as Error).message }));
    }
  });
}
