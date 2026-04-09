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
}

export interface ReversibilityDecl {
  action: string;
  reversible: boolean;
}

export interface Rules {
  blacklist: BlacklistEntry[];
  redaction: RedactionRule[];
  queue: QueueConfig;
  urgent_senders: string[];
  floor: FloorReservation[];
  reversibility: ReversibilityDecl[];
}

const DEFAULT_QUEUE: QueueConfig = { target_depth: 5, low_water_mark: 2, batch_threshold: 3 };

const EMPTY_RULES: Rules = {
  blacklist: [],
  redaction: [],
  queue: { ...DEFAULT_QUEUE },
  urgent_senders: [],
  floor: [],
  reversibility: [],
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

  return { blacklist, redaction, queue, urgent_senders, floor, reversibility };
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
