"""Local HTTP + WebSocket server. The integration point for all sub-systems."""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiohttp import WSMsgType, web

from steward.batcher import Cluster, TriagedCandidate, cluster_candidates
from steward.browser.subagent import BrowserSubAgent
from steward.credentials import CredentialResolver, check_credential_scopes
from steward.gmail.fake import FakeGmail
from steward.gmail.subagent import GmailSubAgent, create_gmail_sub_agent
from steward.journal import append_journal, read_journal
from steward.planner import Goal, PlannerInput
from steward.principles_gate import check_blacklist
from steward.promoter import Promotion, detect_promotions
from steward.ranker import RankInput, RankOptions, learn_weights, rank_candidates
from steward.redactor import apply_redaction_rules, redact
from steward.rules import Rules
from steward.triage import TriageFn, TriageResult, default_triage_result
from steward.verifier import detect_anomalies

PlanFn = Callable[[PlannerInput], Awaitable[Goal]]


def _goal_to_dict(goal: Any) -> dict[str, Any]:
    if hasattr(goal, "to_dict"):
        return goal.to_dict()
    if isinstance(goal, dict):
        return dict(goal)
    raise TypeError(f"plan() returned unexpected type: {type(goal)}")

WEB_CLIENT_HTML = """<!doctype html>
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
</script></body></html>"""


@dataclass
class ServerDeps:
    gmail: FakeGmail
    journal_path: str
    plan: PlanFn
    get_rules: Callable[[], Rules]
    triage: TriageFn | None = None
    search_query: str = "is:unread"
    rules_dir: str | None = None
    browser_sub_agent: BrowserSubAgent | None = None
    credential_resolver: CredentialResolver | None = None


@dataclass
class CardState:
    goal: dict[str, Any]
    message: dict[str, Any]
    features: dict[str, Any]
    batch_messages: list[dict[str, Any]] | None = None
    breakdown: dict[str, Any] | None = None
    exploration: bool = False
    re_approval: bool = False
    original_goal: dict[str, Any] | None = None


class ExecutorServer:
    def __init__(self, deps: ServerDeps) -> None:
        self.deps = deps
        self.queue: list[CardState] = []
        self.queued_message_ids: set[str] = set()
        self.refilling = False
        self.gmail_sub_agent: GmailSubAgent = create_gmail_sub_agent(deps.gmail)
        self.meta_card_goal_ids: set[str] = set()
        self.ws_clients: set[web.WebSocketResponse] = set()
        self._verifier_task: asyncio.Task | None = None
        self._promoter_task: asyncio.Task | None = None

    # ---------- helpers

    def _goal_for_card(self, card: CardState) -> dict[str, Any]:
        out = dict(card.goal)
        if card.breakdown is not None:
            out["breakdown"] = card.breakdown
        out["exploration"] = card.exploration
        return out

    def _queue_state(self) -> dict[str, Any]:
        return {
            "type": "queue_update",
            "depth": len(self.queue),
            "cards": [self._goal_for_card(c) for c in self.queue],
        }

    async def _broadcast_queue_update(self) -> None:
        msg = json.dumps(self._queue_state())
        stale: list[web.WebSocketResponse] = []
        for ws in list(self.ws_clients):
            try:
                await ws.send_str(msg)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.ws_clients.discard(ws)

    # ---------- triage + plan

    async def _triage_and_plan(self, message: dict[str, Any]) -> CardState:
        if self.deps.triage:
            triage_result = await self.deps.triage(message)
        else:
            triage_result = default_triage_result()
        base = redact(message)
        redacted = apply_redaction_rules(base, self.deps.get_rules().redaction)
        goal = await self.deps.plan({
            "message": redacted,
            "features": triage_result.features,
            "snippet": triage_result.snippet,
        })
        return CardState(
            goal=_goal_to_dict(goal),
            message=message,
            features=dict(triage_result.features),
        )

    async def _plan_and_enqueue(self, entry: TriagedCandidate) -> CardState:
        base = redact(entry.message)
        redacted = apply_redaction_rules(base, self.deps.get_rules().redaction)
        goal = await self.deps.plan({
            "message": redacted,
            "features": entry.result.features,
            "snippet": entry.result.snippet,
        })
        return CardState(
            goal=_goal_to_dict(goal),
            message=entry.message,
            features=dict(entry.result.features),
        )

    def _make_batched_card(self, cluster: Cluster) -> CardState:
        msgs = [c.message for c in cluster.candidates]
        message_ids = [m["id"] for m in msgs]
        unique_domains: list[str] = []
        for m in msgs:
            at = m["from"].rfind("@")
            domain = m["from"][at + 1:] if at >= 0 else m["from"]
            if domain not in unique_domains:
                unique_domains.append(domain)
        sample_text = ", ".join(unique_domains[:3])
        now_ms = int(time.time() * 1000)
        goal: dict[str, Any] = {
            "id": f"batch-{cluster.domain}-{now_ms}",
            "title": f"Archive {len(msgs)} {cluster.category} from {cluster.domain}",
            "reason": f"Batch: {len(msgs)} similar messages ({sample_text})",
            "messageId": msgs[0]["id"],
            "messageIds": message_ids,
            "batchSize": len(msgs),
            "transport": "gmail",
            "action": "archive",
        }
        return CardState(
            goal=goal,
            message=msgs[0],
            features=dict(cluster.candidates[0].result.features),
            batch_messages=msgs,
        )

    # ---------- refill

    async def refill(self) -> None:
        if self.refilling:
            return
        self.refilling = True
        try:
            rules = self.deps.get_rules()
            qc = rules.queue
            if len(self.queue) >= qc.low_water_mark:
                return

            candidates = self.deps.gmail.search(self.deps.search_query)
            if not candidates:
                return
            fresh = [m for m in candidates if m["id"] not in self.queued_message_ids]
            if not fresh:
                return

            triaged: list[TriagedCandidate] = []
            for message in fresh:
                if self.deps.triage:
                    result = await self.deps.triage(message)
                else:
                    result = default_triage_result()
                triaged.append(TriagedCandidate(message=message, result=result))

            urgent_senders = rules.urgent_senders
            urgent: list[TriagedCandidate] = []
            normal: list[TriagedCandidate] = []
            for t in triaged:
                sender_email = t.message["from"].lower()
                at = sender_email.rfind("@")
                sender_domain = sender_email[at + 1:] if at >= 0 else sender_email
                if sender_email in urgent_senders or sender_domain in urgent_senders:
                    urgent.append(t)
                else:
                    normal.append(t)

            batches, remaining = cluster_candidates(normal, qc.batch_threshold)

            for cluster in batches:
                if len(self.queue) >= qc.target_depth:
                    break
                batch_card = self._make_batched_card(cluster)
                self.queue.append(batch_card)
                for c in cluster.candidates:
                    self.queued_message_ids.add(c.message["id"])

            journal_entries = read_journal(self.deps.journal_path)
            learned = learn_weights(journal_entries)
            rank_inputs = [
                RankInput(messageId=t.message["id"], features=t.result.features)
                for t in remaining
            ]
            slots_available = qc.target_depth - len(self.queue)
            ranked = rank_candidates(
                rank_inputs,
                rules.floor,
                slots_available,
                options=RankOptions(
                    weights=learned,
                    exploration_slots=qc.exploration_slots,
                    journal_entries=journal_entries,
                ),
            )

            triaged_map = {t.message["id"]: t for t in triaged}

            for t in urgent:
                if t.message["id"] in self.queued_message_ids:
                    continue
                card = await self._plan_and_enqueue(t)
                if len(self.queue) >= qc.target_depth:
                    removed = self.queue.pop()
                    if removed.batch_messages:
                        for m in removed.batch_messages:
                            self.queued_message_ids.discard(m["id"])
                    else:
                        self.queued_message_ids.discard(removed.message["id"])
                self.queue.insert(0, card)
                self.queued_message_ids.add(t.message["id"])

            for r in ranked:
                if len(self.queue) >= qc.target_depth:
                    break
                entry = triaged_map.get(r.messageId)
                if not entry:
                    continue
                if r.messageId in self.queued_message_ids:
                    continue
                card = await self._plan_and_enqueue(entry)
                card.breakdown = dict(r.breakdown) if r.breakdown else None
                card.exploration = bool(r.exploration)
                self.queue.append(card)
                self.queued_message_ids.add(entry.message["id"])
        finally:
            self.refilling = False
            await self._broadcast_queue_update()

    # ---------- verifier / promoter

    async def run_verifier(self) -> None:
        anomalies = await detect_anomalies(self.deps.journal_path, self.deps.gmail)
        for a in anomalies:
            meta_id = f"meta-{a.goalId}-{a.messageId}"
            if meta_id in self.meta_card_goal_ids:
                continue
            if any(c.goal.get("id") == meta_id for c in self.queue):
                continue
            msg = self.deps.gmail.get_by_id(a.messageId)
            if not msg:
                continue
            title = (
                f'Review: "{msg["subject"]}" was unarchived'
                if a.type == "unarchive"
                else f'Review: reply after archiving "{msg["subject"]}"'
            )
            meta_goal = {
                "id": meta_id,
                "title": title,
                "reason": a.description,
                "messageId": a.messageId,
            }
            self.queue.append(
                CardState(
                    goal=meta_goal,
                    message=msg,
                    features={
                        "deadline": None,
                        "amount": None,
                        "waiting_on_user": False,
                        "category": "meta",
                        "urgency": "medium",
                    },
                )
            )
            self.meta_card_goal_ids.add(meta_id)
            append_journal(self.deps.journal_path, {
                "kind": "verifier_anomaly",
                "goalId": a.goalId,
                "messageId": a.messageId,
                "anomalyType": a.type,
                "metaCardId": meta_id,
            })

    async def run_promoter(self) -> None:
        rules = self.deps.get_rules()
        promotions = detect_promotions(self.deps.journal_path, rules.promotion)
        for p in promotions:
            meta_id = f"meta-promote-{p.patternKey}"
            if meta_id in self.meta_card_goal_ids:
                continue
            if any(c.goal.get("id") == meta_id for c in self.queue):
                continue
            meta_goal = {
                "id": meta_id,
                "title": f"Promote rule: auto-{p.action} *@{p.senderDomain}",
                "reason": f"You have approved {p.count} {p.action} actions from {p.senderDomain}. Promote to a standing rule?",
                "messageId": "",
                "promotionData": {
                    "patternKey": p.patternKey,
                    "senderDomain": p.senderDomain,
                    "action": p.action,
                    "transport": p.transport,
                    "count": p.count,
                    "proposedRule": p.proposedRule,
                },
            }
            self.queue.append(
                CardState(
                    goal=meta_goal,
                    message={
                        "id": "",
                        "from": f"*@{p.senderDomain}",
                        "subject": f"Rule promotion: {p.action} {p.senderDomain}",
                        "body": "",
                        "unread": False,
                    },
                    features={
                        "deadline": None,
                        "amount": None,
                        "waiting_on_user": False,
                        "category": "meta",
                        "urgency": "medium",
                    },
                )
            )
            self.meta_card_goal_ids.add(meta_id)

    # ---------- aiohttp handlers

    async def _index(self, request: web.Request) -> web.Response:
        return web.Response(text=WEB_CLIENT_HTML, content_type="text/html")

    async def _get_card(self, request: web.Request) -> web.Response:
        await self.refill()
        if not self.queue:
            return web.Response(status=204)
        return web.json_response(self.queue[0].goal)

    async def _get_queue(self, request: web.Request) -> web.Response:
        return web.json_response({
            "depth": len(self.queue),
            "cards": [self._goal_for_card(c) for c in self.queue],
        })

    async def _post_refill(self, request: web.Request) -> web.Response:
        rules = self.deps.get_rules()
        orig_low = rules.queue.low_water_mark
        rules.queue.low_water_mark = rules.queue.target_depth
        try:
            await self.refill()
        finally:
            rules.queue.low_water_mark = orig_low
        return web.json_response({"ok": True, "depth": len(self.queue)})

    async def _post_verifier_run(self, request: web.Request) -> web.Response:
        await self.run_verifier()
        await self._broadcast_queue_update()
        return web.json_response({"ok": True, "queueDepth": len(self.queue)})

    async def _post_promoter_run(self, request: web.Request) -> web.Response:
        await self.run_promoter()
        await self._broadcast_queue_update()
        return web.json_response({"ok": True, "queueDepth": len(self.queue)})

    async def _get_activity(self, request: web.Request) -> web.Response:
        entries = read_journal(self.deps.journal_path)
        actions = [e for e in entries if e.get("kind") in ("action", "verifier_anomaly")]
        recent = list(reversed(actions))[:50]
        return web.json_response({"entries": recent})

    async def _post_activity_wrong(self, request: web.Request) -> web.Response:
        goal_id = request.match_info["goalId"]
        entries = read_journal(self.deps.journal_path)
        action = next((e for e in entries if e.get("kind") == "action" and e.get("goalId") == goal_id), None)
        if not action:
            return web.json_response({"error": "action not found"}, status=404)
        msg_id = action.get("messageId", "")
        meta_id = f"meta-wrong-{goal_id}"
        if meta_id in self.meta_card_goal_ids or any(c.goal.get("id") == meta_id for c in self.queue):
            return web.json_response({"ok": True, "alreadyQueued": True})
        msg = self.deps.gmail.get_by_id(msg_id) if msg_id else None
        meta_goal = {
            "id": meta_id,
            "title": f'Review: user flagged "{action.get("title", "")}" as wrong',
            "reason": f'The user indicated this action was incorrect. Original goal: {action.get("title", "")}',
            "messageId": msg_id,
        }
        if not msg:
            msg = {
                "id": msg_id,
                "from": "unknown",
                "subject": str(action.get("title", "")),
                "body": "",
                "unread": False,
            }
        self.queue.append(
            CardState(
                goal=meta_goal,
                message=msg,
                features={
                    "deadline": None,
                    "amount": None,
                    "waiting_on_user": False,
                    "category": "meta",
                    "urgency": "high",
                },
            )
        )
        self.meta_card_goal_ids.add(meta_id)
        append_journal(self.deps.journal_path, {
            "kind": "verifier_anomaly",
            "goalId": goal_id,
            "messageId": msg_id,
            "anomalyType": "user_flagged_wrong",
            "metaCardId": meta_id,
        })
        await self._broadcast_queue_update()
        return web.json_response({"ok": True, "metaCardId": meta_id})

    async def _post_decision(self, request: web.Request) -> web.Response:
        card_id = request.match_info["cardId"]
        idx = next((i for i, c in enumerate(self.queue) if c.goal.get("id") == card_id), -1)
        if idx == -1:
            return web.json_response({"error": "no such card"}, status=404)
        card = self.queue[idx]
        body = await request.json()
        decision = body.get("decision")
        if decision not in ("approve", "reject", "defer"):
            return web.json_response({"error": "bad decision"}, status=400)

        goal = card.goal
        transport = goal.get("transport")
        action = goal.get("action")

        # Blacklist gate
        if decision == "approve" and transport and action:
            gate = check_blacklist(self.deps.get_rules().blacklist, transport, action)
            if not gate.allowed:
                append_journal(self.deps.journal_path, {
                    "kind": "blocked",
                    "goalId": goal.get("id"),
                    "messageId": card.message.get("id"),
                    "reason": gate.reason,
                })
                self._remove_card_at(idx, card)
                await self._broadcast_queue_update()
                return web.json_response({"error": "blocked", "reason": gate.reason}, status=403)

        # Irreversibility halt
        if decision == "approve" and transport == "gmail" and action and not card.re_approval:
            rules = self.deps.get_rules()
            decl = next((r for r in rules.reversibility if r.action == action), None)
            if decl and not decl.reversible:
                re_goal = {
                    "id": f"reapproval-{goal.get('id')}",
                    "title": f"⚠ Confirm irreversible: {goal.get('title')}",
                    "reason": f"This action ({action}) is irreversible. Original goal: {goal.get('reason')}",
                    "messageId": goal.get("messageId"),
                    "transport": transport,
                    "action": action,
                }
                for key in ("draftId", "draftBody", "messageIds", "batchSize"):
                    if key in goal:
                        re_goal[key] = goal[key]
                re_card = CardState(
                    goal=re_goal,
                    message=card.message,
                    features=card.features,
                    re_approval=True,
                    original_goal=goal,
                )
                self.queue.pop(idx)
                self.queue.insert(0, re_card)
                append_journal(self.deps.journal_path, {
                    "kind": "halt",
                    "goalId": goal.get("id"),
                    "messageId": card.message.get("id"),
                    "action": action,
                    "reason": "irreversible action requires re-approval",
                })
                await self._broadcast_queue_update()
                return web.json_response({"ok": True, "halted": True, "reApprovalId": re_goal["id"]})

        # Credential scope check
        if decision == "approve" and transport == "gmail" and action and self.deps.credential_resolver:
            rules = self.deps.get_rules()
            cred = check_credential_scopes(action, rules.credential_scopes, self.deps.credential_resolver)
            if not cred.allowed:
                append_journal(self.deps.journal_path, {
                    "kind": "credential_refused",
                    "goalId": goal.get("id"),
                    "messageId": card.message.get("id"),
                    "action": action,
                    "reason": cred.reason,
                })
                self.queue.pop(idx)
                self.queued_message_ids.discard(card.message.get("id", ""))
                await self._broadcast_queue_update()
                return web.json_response(
                    {"error": "credential_refused", "reason": cred.reason},
                    status=403,
                )

        # Gmail action dispatch
        if decision == "approve" and transport == "gmail" and action:
            if action == "archive":
                return await self._dispatch_archive(idx, card)
            if action == "draft_reply":
                return await self._dispatch_draft_reply(idx, card)
            if action == "send_draft":
                return await self._dispatch_send_draft(idx, card)

        # Browser read dispatch
        if decision == "approve" and transport == "browser" and action == "browser_read" and self.deps.browser_sub_agent:
            return await self._dispatch_browser_read(idx, card)

        # Promotion meta-card
        promotion_data = goal.get("promotionData")
        if promotion_data and goal.get("id", "").startswith("meta-promote-"):
            if decision == "approve" and self.deps.rules_dir:
                gmail_md = Path(self.deps.rules_dir) / "gmail.md"
                existing = gmail_md.read_text(encoding="utf-8") if gmail_md.exists() else ""
                proposed = promotion_data["proposedRule"]
                new_content = (existing.rstrip() + "\n" + proposed + "\n") if existing else (proposed + "\n")
                gmail_md.write_text(new_content, encoding="utf-8")
                append_journal(self.deps.journal_path, {
                    "kind": "rule_promoted",
                    "patternKey": promotion_data["patternKey"],
                    "goalId": goal.get("id"),
                    "senderDomain": promotion_data["senderDomain"],
                    "action": promotion_data["action"],
                    "proposedRule": proposed,
                })
            elif decision == "reject":
                append_journal(self.deps.journal_path, {
                    "kind": "promotion_rejected",
                    "patternKey": promotion_data["patternKey"],
                    "goalId": goal.get("id"),
                    "senderDomain": promotion_data["senderDomain"],
                    "action": promotion_data["action"],
                })
            self.queue.pop(idx)
            await self._broadcast_queue_update()
            return web.json_response({"ok": True})

        # Plain decision
        append_journal(self.deps.journal_path, {
            "kind": "decision",
            "decision": decision,
            "goalId": goal.get("id"),
            "messageId": card.message.get("id"),
            "title": goal.get("title"),
            "features": card.features,
        })
        self._remove_card_at(idx, card)
        await self._broadcast_queue_update()
        return web.json_response({"ok": True})

    def _remove_card_at(self, idx: int, card: CardState) -> None:
        self.queue.pop(idx)
        if card.batch_messages:
            for m in card.batch_messages:
                self.queued_message_ids.discard(m["id"])
        else:
            self.queued_message_ids.discard(card.message.get("id", ""))

    async def _dispatch_archive(self, idx: int, card: CardState) -> web.Response:
        goal = card.goal
        is_batch = bool(card.batch_messages and len(card.batch_messages) > 1)
        message_ids = [m["id"] for m in card.batch_messages] if is_batch else [card.message["id"]]

        outcomes: list[dict[str, Any]] = []
        for msg_id in message_ids:
            outcome = await self.gmail_sub_agent.dispatch({
                "capability": "archive",
                "messageId": msg_id,
                "instruction": f"Archive message (batch: {goal.get('title', '')})",
            })
            outcomes.append(outcome)

        if is_batch:
            n = len(message_ids)
            sample_ids = list(dict.fromkeys([message_ids[0], message_ids[n // 2], message_ids[-1]]))
        else:
            sample_ids = list(message_ids)
        verifications = [await self.gmail_sub_agent.verify(mid, "archive") for mid in sample_ids]
        all_verified = all(v["verified"] for v in verifications)

        append_journal(self.deps.journal_path, {
            "kind": "action",
            "goalId": goal.get("id"),
            "messageId": card.message.get("id"),
            "messageIds": message_ids,
            "batchSize": len(message_ids),
            "title": goal.get("title"),
            "outcomes": outcomes,
            "verification": {"verified": all_verified, "sample": verifications},
        })
        self.queue.pop(idx)
        for mid in message_ids:
            self.queued_message_ids.discard(mid)
        await self._broadcast_queue_update()
        return web.json_response({
            "ok": True,
            "outcomes": outcomes,
            "verification": {"verified": all_verified, "sample": verifications},
            "batchSize": len(message_ids),
        })

    async def _dispatch_draft_reply(self, idx: int, card: CardState) -> web.Response:
        goal = card.goal
        outcome = await self.gmail_sub_agent.dispatch({
            "capability": "draft_reply",
            "messageId": card.message["id"],
            "instruction": goal.get("title", ""),
            "draftBody": goal.get("draftBody"),
        })
        verification = await self.gmail_sub_agent.verify(
            card.message["id"],
            "draft_reply",
            {"draftId": outcome.get("draftId")},
        )
        append_journal(self.deps.journal_path, {
            "kind": "action",
            "goalId": goal.get("id"),
            "messageId": card.message.get("id"),
            "title": goal.get("title"),
            "outcomes": [outcome],
            "verification": {"verified": verification["verified"], "sample": [verification]},
        })
        self.queue.pop(idx)
        self.queued_message_ids.discard(card.message.get("id", ""))
        await self._broadcast_queue_update()
        return web.json_response({
            "ok": True,
            "outcomes": [outcome],
            "verification": {"verified": verification["verified"], "sample": [verification]},
        })

    async def _dispatch_send_draft(self, idx: int, card: CardState) -> web.Response:
        goal = card.goal
        draft_id = goal.get("draftId")
        outcome = await self.gmail_sub_agent.dispatch({
            "capability": "send_draft",
            "messageId": card.message["id"],
            "instruction": goal.get("title", ""),
            "draftId": draft_id,
        })
        verification = await self.gmail_sub_agent.verify(
            card.message["id"],
            "send_draft",
            {"draftId": outcome.get("draftId")},
        )
        append_journal(self.deps.journal_path, {
            "kind": "action",
            "goalId": goal.get("id"),
            "messageId": card.message.get("id"),
            "title": goal.get("title"),
            "outcomes": [outcome],
            "verification": {"verified": verification["verified"], "sample": [verification]},
        })
        self.queue.pop(idx)
        self.queued_message_ids.discard(card.message.get("id", ""))
        await self._broadcast_queue_update()
        return web.json_response({
            "ok": True,
            "outcomes": [outcome],
            "verification": {"verified": verification["verified"], "sample": [verification]},
        })

    async def _dispatch_browser_read(self, idx: int, card: CardState) -> web.Response:
        goal = card.goal
        browser_instruction = {
            "capability": "browser_read",
            "url": goal.get("targetUrl", ""),
            "instruction": goal.get("title", ""),
            "selector": goal.get("selector"),
        }
        outcome = await self.deps.browser_sub_agent.dispatch(browser_instruction)  # type: ignore[union-attr]
        verification = await self.deps.browser_sub_agent.verify(browser_instruction["url"])  # type: ignore[union-attr]
        append_journal(self.deps.journal_path, {
            "kind": "action",
            "goalId": goal.get("id"),
            "messageId": card.message.get("id"),
            "title": goal.get("title"),
            "transport": "browser",
            "action": "browser_read",
            "outcomes": [outcome],
            "verification": {"verified": verification["verified"], "actual_url": verification["actual_url"]},
        })
        self.queue.pop(idx)
        self.queued_message_ids.discard(card.message.get("id", ""))
        await self._broadcast_queue_update()
        return web.json_response({
            "ok": True,
            "outcomes": [outcome],
            "verification": {"verified": verification["verified"], "actual_url": verification["actual_url"]},
        })

    async def _websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.ws_clients.add(ws)
        # Send current state on connect
        await ws.send_str(json.dumps(self._queue_state()))
        try:
            async for msg in ws:
                if msg.type == WSMsgType.ERROR:
                    break
        finally:
            self.ws_clients.discard(ws)
        return ws

    def build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/", self._index)
        app.router.add_get("/index.html", self._index)
        app.router.add_get("/card", self._get_card)
        app.router.add_get("/queue", self._get_queue)
        app.router.add_post("/refill", self._post_refill)
        app.router.add_post("/card/{cardId}/decision", self._post_decision)
        app.router.add_post("/verifier/run", self._post_verifier_run)
        app.router.add_post("/promoter/run", self._post_promoter_run)
        app.router.add_get("/activity", self._get_activity)
        app.router.add_post("/activity/{goalId}/wrong", self._post_activity_wrong)
        app.router.add_get("/ws", self._websocket)
        # WebSocket at root too, since the TS ws lib attaches to the server itself.
        return app


def create_executor_server(deps: ServerDeps) -> ExecutorServer:
    return ExecutorServer(deps)
