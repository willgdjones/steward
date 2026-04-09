import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { FakeGmail, type GmailMessage } from '../gmail/fake.js';
import { redact, applyRedactionRules, type RedactedMessage } from '../redactor.js';
import type { Goal, PlannerInput } from '../planner/index.js';
import type { TriageFn, TriageResult } from '../triage.js';
import { defaultTriageResult } from '../triage.js';
import { appendJournal } from '../journal.js';
import { checkBlacklist } from '../principlesGate.js';
import type { Rules } from '../rules.js';
import { rankCandidates, learnWeights, type RankInput } from '../ranker.js';
import { createGmailSubAgent, type GmailSubAgent } from '../gmail/subagent.js';
import { clusterCandidates, type TriagedCandidate } from '../batcher.js';
import { detectAnomalies } from '../verifier.js';
import { readJournal } from '../journal.js';
import { detectPromotions } from '../promoter.js';

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
  /** Directory containing rules files (principles.md, gmail.md). Needed for rule promotion writes. */
  rulesDir?: string;
}

interface CardState {
  goal: Goal;
  message: GmailMessage;
  features: import('../triage.js').TriageFeatures;
  /** For batched cards, all messages in the batch. */
  batchMessages?: GmailMessage[];
  /** Per-feature score breakdown from the ranker. */
  breakdown?: import('../ranker.js').ScoreBreakdown;
  /** Whether this card fills an exploration slot. */
  exploration?: boolean;
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
  const gmailSubAgent = createGmailSubAgent(deps.gmail);
  /** Track goal IDs already surfaced as meta-cards to avoid duplicates. */
  const metaCardGoalIds = new Set<string>();
  let verifierTimer: ReturnType<typeof setInterval> | null = null;

  /** Run the post-hoc verifier: detect anomalies and insert meta-cards. */
  async function runVerifier(): Promise<void> {
    const anomalies = await detectAnomalies(deps.journalPath, deps.gmail);
    for (const a of anomalies) {
      const metaId = `meta-${a.goalId}-${a.messageId}`;
      if (metaCardGoalIds.has(metaId)) continue;
      // Check if this meta-card goal ID is already in the queue
      if (queue.some((c) => c.goal.id === metaId)) continue;
      const msg = deps.gmail.getById(a.messageId);
      if (!msg) continue;
      const metaGoal: Goal = {
        id: metaId,
        title: a.type === 'unarchive'
          ? `Review: "${msg.subject}" was unarchived`
          : `Review: reply after archiving "${msg.subject}"`,
        reason: a.description,
        messageId: a.messageId,
      };
      const metaCard: CardState = {
        goal: metaGoal,
        message: msg,
        features: { deadline: null, amount: null, waiting_on_user: false, category: 'meta', urgency: 'medium' as const },
      };
      queue.push(metaCard);
      metaCardGoalIds.add(metaId);
      // Journal the anomaly to prevent re-detection
      appendJournal(deps.journalPath, {
        kind: 'verifier_anomaly',
        goalId: a.goalId,
        messageId: a.messageId,
        anomalyType: a.type,
        metaCardId: metaId,
      });
    }
  }

  function startVerifierCron(): void {
    const intervalMs = deps.getRules().verifier.interval_minutes * 60 * 1000;
    if (verifierTimer) clearInterval(verifierTimer);
    verifierTimer = setInterval(() => { runVerifier().catch(() => {}); }, intervalMs);
  }

  function stopVerifierCron(): void {
    if (verifierTimer) { clearInterval(verifierTimer); verifierTimer = null; }
  }

  let promoterTimer: ReturnType<typeof setInterval> | null = null;

  /** Run the rule promoter: detect patterns and insert meta-cards. */
  async function runPromoter(): Promise<void> {
    const rules = deps.getRules();
    const promotions = detectPromotions(deps.journalPath, rules.promotion);
    for (const p of promotions) {
      const metaId = `meta-promote-${p.patternKey}`;
      if (metaCardGoalIds.has(metaId)) continue;
      if (queue.some((c) => c.goal.id === metaId)) continue;

      const metaGoal: Goal = {
        id: metaId,
        title: `Promote rule: auto-${p.action} *@${p.senderDomain}`,
        reason: `You have approved ${p.count} ${p.action} actions from ${p.senderDomain}. Promote to a standing rule?`,
        messageId: '',
      };
      // Attach promotion data for the approval handler
      (metaGoal as unknown as Record<string, unknown>).promotionData = p;
      const metaCard: CardState = {
        goal: metaGoal,
        message: { id: '', from: `*@${p.senderDomain}`, subject: `Rule promotion: ${p.action} ${p.senderDomain}`, body: '', unread: false },
        features: { deadline: null, amount: null, waiting_on_user: false, category: 'meta', urgency: 'medium' as const },
      };
      queue.push(metaCard);
      metaCardGoalIds.add(metaId);
    }
  }

  function startPromoterCron(): void {
    const intervalMs = deps.getRules().promotion.interval_minutes * 60 * 1000;
    if (promoterTimer) clearInterval(promoterTimer);
    promoterTimer = setInterval(() => { runPromoter().catch(() => {}); }, intervalMs);
  }

  function stopPromoterCron(): void {
    if (promoterTimer) { clearInterval(promoterTimer); promoterTimer = null; }
  }

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

  async function planAndEnqueue(entry: TriagedCandidate): Promise<CardState> {
    const base = redact(entry.message);
    const redacted = applyRedactionRules(base, deps.getRules().redaction);
    const goal = await deps.plan({
      message: redacted,
      features: entry.result.features,
      snippet: entry.result.snippet,
    });
    return { goal, message: entry.message, features: entry.result.features };
  }

  function makeBatchedCard(cluster: import('../batcher.js').Cluster): CardState {
    const msgs = cluster.candidates.map((c) => c.message);
    const messageIds = msgs.map((m) => m.id);
    const sampleDomains = [...new Set(msgs.map((m) => {
      const at = m.from.lastIndexOf('@');
      return at >= 0 ? m.from.slice(at + 1) : m.from;
    }))].slice(0, 3);
    const sampleText = sampleDomains.join(', ');
    const goal: Goal = {
      id: `batch-${cluster.domain}-${Date.now()}`,
      title: `Archive ${msgs.length} ${cluster.category} from ${cluster.domain}`,
      reason: `Batch: ${msgs.length} similar messages (${sampleText})`,
      messageId: msgs[0].id,
      messageIds,
      batchSize: msgs.length,
      transport: 'gmail',
      action: 'archive',
    };
    return {
      goal,
      message: msgs[0],
      features: cluster.candidates[0].result.features,
      batchMessages: msgs,
    };
  }

  async function refill(): Promise<void> {
    if (refilling) return;
    refilling = true;
    try {
      const rules = deps.getRules();
      const { target_depth, low_water_mark, batch_threshold } = rules.queue;

      // Only refill when below low-water mark
      if (queue.length >= low_water_mark) return;

      const candidates = deps.gmail.search(searchQuery);
      if (candidates.length === 0) return;

      // Filter out messages already in the queue
      const fresh = candidates.filter((m) => !queuedMessageIds.has(m.id));
      if (fresh.length === 0) return;

      // Triage all fresh candidates to get features for ranking
      const triaged: TriagedCandidate[] = [];
      for (const message of fresh) {
        const result: TriageResult = deps.triage
          ? await deps.triage(message)
          : defaultTriageResult();
        triaged.push({ message, result });
      }

      // Check for urgent senders — inject immediately at front of queue
      const urgentSenders = rules.urgent_senders;
      const urgentMessages: TriagedCandidate[] = [];
      const normalMessages: TriagedCandidate[] = [];

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

      // Cluster normal candidates for batched cards
      const { batches, remaining } = clusterCandidates(normalMessages, batch_threshold);

      // Insert batched cards
      for (const cluster of batches) {
        if (queue.length >= target_depth) break;
        const batchCard = makeBatchedCard(cluster);
        queue.push(batchCard);
        for (const c of cluster.candidates) {
          queuedMessageIds.add(c.message.id);
        }
      }

      // Learn weights from swipe history and rank remaining candidates
      const journalEntries = readJournal(deps.journalPath);
      const learnedWeights = learnWeights(journalEntries);
      const rankInputs: RankInput[] = remaining.map((t) => ({
        messageId: t.message.id,
        features: t.result.features,
      }));
      const slotsAvailable = target_depth - queue.length;
      const ranked = rankCandidates(rankInputs, rules.floor, slotsAvailable, undefined, {
        weights: learnedWeights,
        explorationSlots: rules.queue.exploration_slots,
        journalEntries,
      });

      // Build a lookup for triaged data
      const triagedMap = new Map(triaged.map((t) => [t.message.id, t]));

      // Process urgent messages first (bypass queue depth for insertion at front)
      for (const urgent of urgentMessages) {
        if (queuedMessageIds.has(urgent.message.id)) continue;
        const card = await planAndEnqueue(urgent);
        // Insert at front but still respect target_depth
        if (queue.length >= target_depth) {
          const removed = queue.pop();
          if (removed) {
            if (removed.batchMessages) {
              for (const m of removed.batchMessages) queuedMessageIds.delete(m.id);
            } else {
              queuedMessageIds.delete(removed.message.id);
            }
          }
        }
        queue.unshift(card);
        queuedMessageIds.add(urgent.message.id);
      }

      // Process ranked normal candidates
      for (const r of ranked) {
        if (queue.length >= target_depth) break;
        const entry = triagedMap.get(r.messageId);
        if (!entry) continue;
        if (queuedMessageIds.has(r.messageId)) continue;

        const card = await planAndEnqueue(entry);
        card.breakdown = r.breakdown;
        card.exploration = r.exploration;
        queue.push(card);
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
          cards: queue.map((c) => ({
            ...c.goal,
            breakdown: c.breakdown,
            exploration: c.exploration,
          })),
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
            if (card.batchMessages) {
              for (const m of card.batchMessages) queuedMessageIds.delete(m.id);
            } else {
              queuedMessageIds.delete(card.message.id);
            }
            send(res, 403, JSON.stringify({ error: 'blocked', reason: gate.reason }));
            return;
          }
        }
        // If approving an actionable goal, dispatch to the sub-agent
        if (decision === 'approve' && card.goal.action === 'archive' && card.goal.transport === 'gmail') {
          const isBatch = card.batchMessages && card.batchMessages.length > 1;
          const messageIds = isBatch ? card.batchMessages!.map((m) => m.id) : [card.message.id];

          // Dispatch archive for all messages in the batch (or single message)
          const outcomes = [];
          for (const msgId of messageIds) {
            const instruction = {
              capability: card.goal.action,
              messageId: msgId,
              instruction: `Archive message (batch: ${card.goal.title})`,
            };
            const outcome = await gmailSubAgent.dispatch(instruction);
            outcomes.push(outcome);
          }

          // Verify a sample: first, last, and one from the middle
          const sampleIds = isBatch
            ? [...new Set([messageIds[0], messageIds[Math.floor(messageIds.length / 2)], messageIds[messageIds.length - 1]])]
            : messageIds;
          const verifications = [];
          for (const msgId of sampleIds) {
            const v = await gmailSubAgent.verify(msgId, card.goal.action);
            verifications.push(v);
          }
          const allVerified = verifications.every((v) => v.verified);

          appendJournal(deps.journalPath, {
            kind: 'action',
            goalId: card.goal.id,
            messageId: card.message.id,
            messageIds,
            batchSize: messageIds.length,
            title: card.goal.title,
            outcomes,
            verification: { verified: allVerified, sample: verifications },
          });

          // Remove card and clean up all queued message IDs
          queue.splice(idx, 1);
          for (const msgId of messageIds) {
            queuedMessageIds.delete(msgId);
          }
          send(res, 200, JSON.stringify({
            ok: true,
            outcomes,
            verification: { verified: allVerified, sample: verifications },
            batchSize: messageIds.length,
          }));
          return;
        }

        // Handle promotion meta-card decisions
        const promotionData = (card.goal as unknown as Record<string, unknown>).promotionData as
          import('../promoter.js').Promotion | undefined;
        if (promotionData && card.goal.id.startsWith('meta-promote-')) {
          if (decision === 'approve' && deps.rulesDir) {
            // Write the rule to gmail.md
            const gmailMdPath = join(deps.rulesDir, 'gmail.md');
            const existing = existsSync(gmailMdPath) ? readFileSync(gmailMdPath, 'utf8') : '';
            const newContent = existing
              ? existing.trimEnd() + '\n' + promotionData.proposedRule + '\n'
              : promotionData.proposedRule + '\n';
            writeFileSync(gmailMdPath, newContent);
            appendJournal(deps.journalPath, {
              kind: 'rule_promoted',
              patternKey: promotionData.patternKey,
              goalId: card.goal.id,
              senderDomain: promotionData.senderDomain,
              action: promotionData.action,
              proposedRule: promotionData.proposedRule,
            });
          } else if (decision === 'reject') {
            appendJournal(deps.journalPath, {
              kind: 'promotion_rejected',
              patternKey: promotionData.patternKey,
              goalId: card.goal.id,
              senderDomain: promotionData.senderDomain,
              action: promotionData.action,
            });
          }
          queue.splice(idx, 1);
          send(res, 200, JSON.stringify({ ok: true }));
          return;
        }

        appendJournal(deps.journalPath, {
          kind: 'decision',
          decision,
          goalId: card.goal.id,
          messageId: card.message.id,
          title: card.goal.title,
          features: card.features,
        });
        queue.splice(idx, 1);
        if (card.batchMessages) {
          for (const m of card.batchMessages) queuedMessageIds.delete(m.id);
        } else {
          queuedMessageIds.delete(card.message.id);
        }
        send(res, 200, JSON.stringify({ ok: true }));
        return;
      }
      // GET /activity — list recent action journal entries
      if (req.method === 'GET' && url === '/activity') {
        const entries = readJournal(deps.journalPath);
        const actions = entries.filter((e) => e.kind === 'action' || e.kind === 'verifier_anomaly');
        // Most recent first, capped at 50
        const recent = actions.reverse().slice(0, 50);
        send(res, 200, JSON.stringify({ entries: recent }));
        return;
      }

      // POST /activity/:goalId/wrong — user marks an action as wrong, emits a meta-card
      const wrongMatch = url.match(/^\/activity\/([^/]+)\/wrong$/);
      if (req.method === 'POST' && wrongMatch) {
        const goalId = decodeURIComponent(wrongMatch[1]);
        const entries = readJournal(deps.journalPath);
        const action = entries.find((e) => e.kind === 'action' && e.goalId === goalId);
        if (!action) {
          send(res, 404, JSON.stringify({ error: 'action not found' }));
          return;
        }
        const msgId = action.messageId as string;
        const metaId = `meta-wrong-${goalId}`;
        if (metaCardGoalIds.has(metaId) || queue.some((c) => c.goal.id === metaId)) {
          send(res, 200, JSON.stringify({ ok: true, alreadyQueued: true }));
          return;
        }
        const msg = deps.gmail.getById(msgId);
        const metaGoal: Goal = {
          id: metaId,
          title: `Review: user flagged "${action.title}" as wrong`,
          reason: `The user indicated this action was incorrect. Original goal: ${action.title}`,
          messageId: msgId,
        };
        const metaCard: CardState = {
          goal: metaGoal,
          message: msg ?? { id: msgId, from: 'unknown', subject: String(action.title ?? ''), body: '', unread: false },
          features: { deadline: null, amount: null, waiting_on_user: false, category: 'meta', urgency: 'high' as const },
        };
        queue.push(metaCard);
        metaCardGoalIds.add(metaId);
        appendJournal(deps.journalPath, {
          kind: 'verifier_anomaly',
          goalId,
          messageId: msgId,
          anomalyType: 'user_flagged_wrong',
          metaCardId: metaId,
        });
        send(res, 200, JSON.stringify({ ok: true, metaCardId: metaId }));
        return;
      }

      // POST /verifier/run — manually trigger the verifier (for tests and debugging)
      if (req.method === 'POST' && url === '/verifier/run') {
        await runVerifier();
        send(res, 200, JSON.stringify({ ok: true, queueDepth: queue.length }));
        return;
      }

      // POST /promoter/run — manually trigger the rule promoter (for tests and debugging)
      if (req.method === 'POST' && url === '/promoter/run') {
        await runPromoter();
        send(res, 200, JSON.stringify({ ok: true, queueDepth: queue.length }));
        return;
      }

      send(res, 404, JSON.stringify({ error: 'not found' }));
    } catch (err) {
      send(res, 500, JSON.stringify({ error: (err as Error).message }));
    }
  });
}
