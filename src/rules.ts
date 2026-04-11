import { existsSync, readFileSync, watch, type FSWatcher } from 'node:fs';
import { join } from 'node:path';
import { parse as parseYaml } from 'yaml';

export interface BlacklistEntry {
  transport: string;
  action: string;
}

export interface RedactionRule {
  field: string;
  /** Regex pattern string. If omitted, the field is dropped entirely. */
  pattern?: string;
}

export interface FloorReservation {
  /** Match condition, e.g. { deadline_within_hours: 72 } */
  match: Record<string, unknown>;
  /** Number of queue slots to reserve for matching candidates. */
  slots: number;
}

export interface QueueConfig {
  target_depth: number;
  low_water_mark: number;
  /** Minimum cluster size to trigger a batched card. Defaults to 3. */
  batch_threshold: number;
  /** Number of queue positions reserved for exploration (high-uncertainty candidates). Defaults to 1. */
  exploration_slots: number;
}

export interface ReversibilityDecl {
  action: string;
  reversible: boolean;
}

export interface VerifierConfig {
  /** How often the post-hoc verifier runs, in minutes. Defaults to 60. */
  interval_minutes: number;
}

export interface PromotionConfig {
  /** Number of approved actions needed before proposing a rule. Defaults to 5. */
  threshold: number;
  /** Minutes to wait after a rejection before re-proposing. Defaults to 1440 (24h). */
  cooldown_minutes: number;
  /** How often the promoter runs, in minutes. Defaults to 120. */
  interval_minutes: number;
}

export interface CredentialScopeDecl {
  action: string;
  refs: string[];
}

export interface Rules {
  blacklist: BlacklistEntry[];
  redaction: RedactionRule[];
  queue: QueueConfig;
  urgent_senders: string[];
  floor: FloorReservation[];
  reversibility: ReversibilityDecl[];
  credential_scopes: CredentialScopeDecl[];
  verifier: VerifierConfig;
  promotion: PromotionConfig;
}

const DEFAULT_QUEUE: QueueConfig = { target_depth: 5, low_water_mark: 2, batch_threshold: 3, exploration_slots: 1 };

const DEFAULT_VERIFIER: VerifierConfig = { interval_minutes: 60 };

const DEFAULT_PROMOTION: PromotionConfig = { threshold: 5, cooldown_minutes: 1440, interval_minutes: 120 };

const EMPTY_RULES: Rules = {
  blacklist: [],
  redaction: [],
  queue: { ...DEFAULT_QUEUE },
  urgent_senders: [],
  floor: [],
  reversibility: [],
  credential_scopes: [],
  verifier: { ...DEFAULT_VERIFIER },
  promotion: { ...DEFAULT_PROMOTION },
};

function loadFile(path: string): Record<string, unknown> | null {
  if (!existsSync(path)) return null;
  const content = readFileSync(path, 'utf8').trim();
  if (!content) return null;
  return parseYaml(content) as Record<string, unknown>;
}

export function loadRules(dir: string): Rules {
  const principles = loadFile(join(dir, 'principles.md'));
  // gmail.md loaded for future use; not parsed into rules yet
  loadFile(join(dir, 'gmail.md'));

  if (!principles) return { ...EMPTY_RULES };

  const blacklist: BlacklistEntry[] = Array.isArray(principles.blacklist)
    ? principles.blacklist.map((e: Record<string, string>) => ({
        transport: e.transport,
        action: e.action,
      }))
    : [];

  const redaction: RedactionRule[] = Array.isArray(principles.redaction)
    ? principles.redaction.map((e: Record<string, string>) => {
        const rule: RedactionRule = { field: e.field };
        if (e.pattern) rule.pattern = e.pattern;
        return rule;
      })
    : [];

  const queueRaw = principles.queue as Record<string, unknown> | undefined;
  const queue: QueueConfig = {
    target_depth: typeof queueRaw?.target_depth === 'number' ? queueRaw.target_depth : DEFAULT_QUEUE.target_depth,
    low_water_mark: typeof queueRaw?.low_water_mark === 'number' ? queueRaw.low_water_mark : DEFAULT_QUEUE.low_water_mark,
    batch_threshold: typeof queueRaw?.batch_threshold === 'number' ? queueRaw.batch_threshold : DEFAULT_QUEUE.batch_threshold,
    exploration_slots: typeof queueRaw?.exploration_slots === 'number' ? queueRaw.exploration_slots : DEFAULT_QUEUE.exploration_slots,
  };

  const urgent_senders: string[] = Array.isArray(principles.urgent_senders)
    ? (principles.urgent_senders as string[]).map((s) => s.toLowerCase())
    : [];

  const floor: FloorReservation[] = Array.isArray(principles.floor)
    ? (principles.floor as Array<Record<string, unknown>>).map((e) => ({
        match: (e.match ?? {}) as Record<string, unknown>,
        slots: typeof e.slots === 'number' ? e.slots : 1,
      }))
    : [];

  const reversibility: ReversibilityDecl[] = Array.isArray(principles.reversibility)
    ? (principles.reversibility as Array<Record<string, unknown>>).map((e) => ({
        action: String(e.action),
        reversible: e.reversible === true,
      }))
    : [];

  const verifierRaw = principles.verifier as Record<string, unknown> | undefined;
  const verifier: VerifierConfig = {
    interval_minutes: typeof verifierRaw?.interval_minutes === 'number' ? verifierRaw.interval_minutes : DEFAULT_VERIFIER.interval_minutes,
  };

  const promotionRaw = principles.promotion as Record<string, unknown> | undefined;
  const promotion: PromotionConfig = {
    threshold: typeof promotionRaw?.threshold === 'number' ? promotionRaw.threshold : DEFAULT_PROMOTION.threshold,
    cooldown_minutes: typeof promotionRaw?.cooldown_minutes === 'number' ? promotionRaw.cooldown_minutes : DEFAULT_PROMOTION.cooldown_minutes,
    interval_minutes: typeof promotionRaw?.interval_minutes === 'number' ? promotionRaw.interval_minutes : DEFAULT_PROMOTION.interval_minutes,
  };

  const credential_scopes: CredentialScopeDecl[] = Array.isArray(principles.credential_scopes)
    ? (principles.credential_scopes as Array<Record<string, unknown>>).map((e) => ({
        action: String(e.action),
        refs: Array.isArray(e.refs) ? (e.refs as string[]).map(String) : [],
      }))
    : [];

  return { blacklist, redaction, queue, urgent_senders, floor, reversibility, credential_scopes, verifier, promotion };
}

export function watchRules(
  dir: string,
  onChange: (rules: Rules) => void,
): { stop: () => void } {
  const watchers: FSWatcher[] = [];

  const reload = () => {
    try {
      onChange(loadRules(dir));
    } catch {
      // Ignore parse errors during mid-write
    }
  };

  const WATCHED = new Set(['principles.md', 'gmail.md']);
  const w = watch(dir, { persistent: false }, (_event, filename) => {
    if (filename && WATCHED.has(filename)) reload();
  });
  watchers.push(w);

  return {
    stop: () => watchers.forEach((w) => w.close()),
  };
}
