import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

export interface GmailMessage {
  id: string;
  from: string;
  subject: string;
  body: string;
  unread: boolean;
}

/**
 * Fake Gmail provider for the slice-002 tracer bullet. The real Gmail
 * provider (issue 004) will replace this; the surface stays the same.
 *
 * Backed by a single JSON file so tests can stage an inbox and the
 * executor can read from it without any network or OAuth.
 */
export class FakeGmail {
  constructor(private readonly path: string) {}

  load(): GmailMessage[] {
    if (!existsSync(this.path)) return [];
    return JSON.parse(readFileSync(this.path, 'utf8')) as GmailMessage[];
  }

  save(messages: GmailMessage[]): void {
    mkdirSync(dirname(this.path), { recursive: true });
    writeFileSync(this.path, JSON.stringify(messages, null, 2));
  }

  readOneUnread(): GmailMessage | null {
    const messages = this.load();
    return messages.find((m) => m.unread) ?? null;
  }
}
