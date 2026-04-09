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

export interface Rules {
  blacklist: BlacklistEntry[];
  redaction: RedactionRule[];
}

const EMPTY_RULES: Rules = { blacklist: [], redaction: [] };

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

  return { blacklist, redaction };
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
