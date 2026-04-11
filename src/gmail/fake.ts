import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

export interface GmailMessage {
  id: string;
  from: string;
  subject: string;
  body: string;
  unread: boolean;
  archived?: boolean;
}

export interface GmailDraft {
  id: string;
  /** The message this draft is a reply to. */
  inReplyTo: string;
  to: string;
  subject: string;
  body: string;
  /** Whether this draft has been sent. */
  sent?: boolean;
}

/**
 * Fake Gmail provider for the slice-002 tracer bullet. The real Gmail
 * provider (issue 004) will replace this; the surface stays the same.
 *
 * Backed by a single JSON file so tests can stage an inbox and the
 * executor can read from it without any network or OAuth.
 */
export class FakeGmail {
  private drafts: GmailDraft[] = [];

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

  /**
   * Search messages by query string. For FakeGmail this just returns
   * unread messages that aren't archived (the query is ignored).
   * Real Gmail (issue 004+) will use the Gmail API search.
   */
  search(_query: string): GmailMessage[] {
    return this.load().filter((m) => m.unread && !m.archived);
  }

  /** Look up a single message by ID. Returns null if not found. */
  getById(id: string): GmailMessage | null {
    return this.load().find((m) => m.id === id) ?? null;
  }

  /** Archive a message: sets archived=true. Returns true if the message was found. */
  archive(id: string): boolean {
    const messages = this.load();
    const msg = messages.find((m) => m.id === id);
    if (!msg) return false;
    msg.archived = true;
    this.save(messages);
    return true;
  }

  /** Create a draft reply to a message. Returns the draft, or null if the message doesn't exist. */
  createDraft(inReplyTo: string, body: string): GmailDraft | null {
    const msg = this.getById(inReplyTo);
    if (!msg) return null;
    const draft: GmailDraft = {
      id: `draft-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      inReplyTo,
      to: msg.from,
      subject: msg.subject.startsWith('Re: ') ? msg.subject : `Re: ${msg.subject}`,
      body,
    };
    this.drafts.push(draft);
    return draft;
  }

  /** Look up a draft by ID. */
  getDraft(id: string): GmailDraft | null {
    return this.drafts.find((d) => d.id === id) ?? null;
  }

  /** List all drafts. */
  listDrafts(): GmailDraft[] {
    return [...this.drafts];
  }

  /** Send an existing draft. Returns the draft if found, null otherwise. Marks it as sent. */
  sendDraft(draftId: string): GmailDraft | null {
    const draft = this.drafts.find((d) => d.id === draftId);
    if (!draft) return null;
    if (draft.sent) return null; // already sent
    draft.sent = true;
    return draft;
  }
}
