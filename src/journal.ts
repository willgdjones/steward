import { appendFileSync, mkdirSync } from 'node:fs';
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
