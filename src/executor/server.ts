import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import { FakeGmail, type GmailMessage } from '../gmail/fake.js';
import { redact, applyRedactionRules, type RedactedMessage } from '../redactor.js';
import type { Goal, PlannerInput } from '../planner/index.js';
import type { TriageFn, TriageResult } from '../triage.js';
import { defaultTriageResult } from '../triage.js';
import { appendJournal } from '../journal.js';
import { checkBlacklist } from '../principlesGate.js';
import type { Rules } from '../rules.js';
import { rankCandidates, type RankInput } from '../ranker.js';

export type Decision = 'approve' | 'reject' | 'defer';

export interface ServerDeps {
  gmail: FakeGmail;
  journalPath: string;
  /** Triage function (cheap model). If omitted, uses default features. */
  triage?: TriageFn;
  /** Injected so tests can avoid spawning a subprocess. */
  plan: (input: PlannerInput) => Promise<Goal>;
  /** Returns current rules; may be updated by file watcher. */
  getRules: () => Rules;
  /** Gmail search query. Defaults to 'is:unread'. */
  searchQuery?: string;
}

interface CardState {
  goal: Goal;
  message: GmailMessage;
  features: import('../triage.js').TriageFeatures;
}

const WEB_CLIENT_HTML = `<!doctype html>
<html><head><meta charset="utf-8"><title>steward</title>
<style>
body{font-family:system-ui;max-width:480px;margin:2em auto;padding:1em}
.card{border:1px solid #ccc;border-radius:8px;padding:1em;margin-bottom:1em}
button{margin-right:.5em;padding:.5em 1em}
</style></head><body>
<h1>steward</h1>
<div id="card">loading\u2026</div>
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
  const queue: CardState[] = [];
  const searchQuery = deps.searchQuery ?? 'is:unread';
  /** Track message IDs already in the queue to avoid duplicates. */
  const queuedMessageIds = new Set<string>();
  let refilling = false;

  async function triageAndPlan(message: GmailMessage): Promise<CardState> {
    // Stage 1: Triage (cheap model) — sees full message
    const triageResult: TriageResult = deps.triage
      ? await deps.triage(message)
      : defaultTriageResult();

    // Stage 2: Redact — deterministic, between triage and planner
    const base = redact(message);
    const redacted = applyRedactionRules(base, deps.getRules().redaction);

    // Stage 3: Plan (expensive model) — sees only redacted message + features
    const goal = await deps.plan({
      message: redacted,
      features: triageResult.features,
      snippet: triageResult.snippet,
    });

    return { goal, message, features: triageResult.features };
  }

  async function refill(): Promise<void> {
    if (refilling) return;
    refilling = true;
    try {
      const rules = deps.getRules();
      const { target_depth, low_water_mark } = rules.queue;

      // Only refill when below low-water mark
      if (queue.length >= low_water_mark) return;

      const candidates = deps.gmail.search(searchQuery);
      if (candidates.length === 0) return;

      // Filter out messages already in the queue
      const fresh = candidates.filter((m) => !queuedMessageIds.has(m.id));
      if (fresh.length === 0) return;

      // Triage all fresh candidates to get features for ranking
      const triaged: Array<{ message: GmailMessage; result: TriageResult }> = [];
      for (const message of fresh) {
        const result: TriageResult = deps.triage
          ? await deps.triage(message)
          : defaultTriageResult();
        triaged.push({ message, result });
      }

      // Check for urgent senders — inject immediately at front of queue
      const urgentSenders = rules.urgent_senders;
      const urgentMessages: typeof triaged = [];
      const normalMessages: typeof triaged = [];

      for (const t of triaged) {
        const senderEmail = t.message.from.toLowerCase();
        const senderDomain = senderEmail.includes('@')
          ? senderEmail.split('@')[1]
          : senderEmail;
        if (
          urgentSenders.includes(senderEmail) ||
          urgentSenders.includes(senderDomain)
        ) {
          urgentMessages.push(t);
        } else {
          normalMessages.push(t);
        }
      }

      // Rank normal candidates
      const rankInputs: RankInput[] = normalMessages.map((t) => ({
        messageId: t.message.id,
        features: t.result.features,
      }));
      const slotsAvailable = target_depth - queue.length;
      const ranked = rankCandidates(rankInputs, rules.floor, slotsAvailable);

      // Build a lookup for triaged data
      const triagedMap = new Map(triaged.map((t) => [t.message.id, t]));

      // Process urgent messages first (bypass queue depth for insertion at front)
      for (const urgent of urgentMessages) {
        if (queuedMessageIds.has(urgent.message.id)) continue;
        const base = redact(urgent.message);
        const redacted = applyRedactionRules(base, deps.getRules().redaction);
        const goal = await deps.plan({
          message: redacted,
          features: urgent.result.features,
          snippet: urgent.result.snippet,
        });
        // Insert at front but still respect target_depth
        if (queue.length >= target_depth) {
          // Remove lowest-priority item from back to make room
          const removed = queue.pop();
          if (removed) queuedMessageIds.delete(removed.message.id);
        }
        queue.unshift({ goal, message: urgent.message, features: urgent.result.features });
        queuedMessageIds.add(urgent.message.id);
      }

      // Process ranked normal candidates
      for (const r of ranked) {
        if (queue.length >= target_depth) break;
        const entry = triagedMap.get(r.messageId);
        if (!entry) continue;
        if (queuedMessageIds.has(r.messageId)) continue;

        const base = redact(entry.message);
        const redacted = applyRedactionRules(base, deps.getRules().redaction);
        const goal = await deps.plan({
          message: redacted,
          features: entry.result.features,
          snippet: entry.result.snippet,
        });
        queue.push({ goal, message: entry.message, features: entry.result.features });
        queuedMessageIds.add(entry.message.id);
      }
    } finally {
      refilling = false;
    }
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
        if (queue.length === 0) {
          res.writeHead(204);
          res.end();
          return;
        }
        send(res, 200, JSON.stringify(queue[0].goal));
        return;
      }
      if (req.method === 'GET' && url === '/queue') {
        send(res, 200, JSON.stringify({
          depth: queue.length,
          cards: queue.map((c) => c.goal),
        }));
        return;
      }
      if (req.method === 'POST' && url === '/refill') {
        // Force immediate refill to target_depth regardless of low-water
        const rules = deps.getRules();
        // Temporarily set low_water to target so refill always runs
        const origLow = rules.queue.low_water_mark;
        rules.queue.low_water_mark = rules.queue.target_depth;
        try {
          await refill();
        } finally {
          rules.queue.low_water_mark = origLow;
        }
        send(res, 200, JSON.stringify({ ok: true, depth: queue.length }));
        return;
      }
      const decisionMatch = url.match(/^\/card\/([^/]+)\/decision$/);
      if (req.method === 'POST' && decisionMatch) {
        const id = decisionMatch[1];
        const idx = queue.findIndex((c) => c.goal.id === id);
        if (idx === -1) {
          send(res, 404, JSON.stringify({ error: 'no such card' }));
          return;
        }
        const card = queue[idx];
        const body = await readBody(req);
        const { decision } = JSON.parse(body || '{}') as { decision: Decision };
        if (decision !== 'approve' && decision !== 'reject' && decision !== 'defer') {
          send(res, 400, JSON.stringify({ error: 'bad decision' }));
          return;
        }
        // Enforce blacklist before dispatching an approved action
        if (decision === 'approve' && card.goal.transport && card.goal.action) {
          const gate = checkBlacklist(
            deps.getRules().blacklist,
            card.goal.transport,
            card.goal.action,
          );
          if (!gate.allowed) {
            appendJournal(deps.journalPath, {
              kind: 'blocked',
              goalId: card.goal.id,
              messageId: card.message.id,
              reason: gate.reason,
            });
            queue.splice(idx, 1);
            queuedMessageIds.delete(card.message.id);
            send(res, 403, JSON.stringify({ error: 'blocked', reason: gate.reason }));
            return;
          }
        }
        appendJournal(deps.journalPath, {
          kind: 'decision',
          decision,
          goalId: card.goal.id,
          messageId: card.message.id,
          title: card.goal.title,
        });
        queue.splice(idx, 1);
        queuedMessageIds.delete(card.message.id);
        send(res, 200, JSON.stringify({ ok: true }));
        return;
      }
      send(res, 404, JSON.stringify({ error: 'not found' }));
    } catch (err) {
      send(res, 500, JSON.stringify({ error: (err as Error).message }));
    }
  });
}
