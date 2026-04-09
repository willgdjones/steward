import { appendFileSync, existsSync, mkdirSync, readFileSync } from 'node:fs';
import { dirname } from 'node:path';

export interface JournalEntry {
  ts: string;
  kind: string;
  [key: string]: unknown;
}

export function appendJournal(path: string, entry: Omit<JournalEntry, 'ts'>): JournalEntry {
  const full: JournalEntry = { ts: new Date().toISOString(), ...entry } as JournalEntry;
  mkdirSync(dirname(path), { recursive: true });
  appendFileSync(path, JSON.stringify(full) + '\n');
  return full;
}

/** Read all journal entries from a JSONL file. Returns [] if the file doesn't exist. */
export function readJournal(path: string): JournalEntry[] {
  if (!existsSync(path)) return [];
  const content = readFileSync(path, 'utf8').trim();
  if (!content) return [];
  return content.split('\n').map((line) => JSON.parse(line) as JournalEntry);
}
