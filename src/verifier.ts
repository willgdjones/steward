import type { FakeGmail } from './gmail/fake.js';
import { readJournal, type JournalEntry } from './journal.js';

export interface Anomaly {
  type: 'unarchive' | 'reply_after_archive';
  messageId: string;
  goalId: string;
  title: string;
  description: string;
}

/**
 * Scan the journal for recent action entries and check Gmail for anomalies:
 * - unarchive: message was archived but is no longer archived
 * - reply_after_archive: a new unread message from the same sender appeared
 *
 * Already-reported anomalies (kind: 'verifier_anomaly' in journal) are skipped.
 */
export async function detectAnomalies(
  journalPath: string,
  gmail: FakeGmail,
): Promise<Anomaly[]> {
  const entries = readJournal(journalPath);

  // Collect goalIds that already have anomaly entries (dedup)
  const reportedGoals = new Set<string>();
  for (const e of entries) {
    if (e.kind === 'verifier_anomaly' && typeof e.goalId === 'string') {
      reportedGoals.add(e.goalId);
    }
  }

  // Filter to action entries only
  const actions = entries.filter(
    (e) => e.kind === 'action' && !reportedGoals.has(e.goalId as string),
  );

  const anomalies: Anomaly[] = [];

  for (const entry of actions) {
    const goalId = entry.goalId as string;
    const title = (entry.title as string) ?? '';
    // Collect all message IDs for this action (batched or single)
    const messageIds: string[] = Array.isArray(entry.messageIds)
      ? (entry.messageIds as string[])
      : [entry.messageId as string];

    for (const msgId of messageIds) {
      const msg = gmail.getById(msgId);
      if (!msg) continue;

      // Check for unarchive: message was archived by us but is no longer
      if (msg.archived === false || (msg.archived === undefined && msg.unread)) {
        anomalies.push({
          type: 'unarchive',
          messageId: msgId,
          goalId,
          title,
          description: `Message "${msg.subject}" was archived but has been unarchived by the user.`,
        });
        continue; // Don't also report reply for same message
      }

      // Check for reply-after-archive: new unread message from same sender
      if (msg.archived === true) {
        const allMessages = gmail.load();
        const senderDomain = msg.from.split('@')[1] ?? msg.from;
        const hasReply = allMessages.some(
          (m) =>
            m.id !== msgId &&
            m.unread &&
            !m.archived &&
            (m.from === msg.from || (m.from.split('@')[1] ?? m.from) === senderDomain) &&
            m.subject.toLowerCase().includes(msg.subject.toLowerCase().replace(/^re:\s*/i, '')),
        );
        if (hasReply) {
          anomalies.push({
            type: 'reply_after_archive',
            messageId: msgId,
            goalId,
            title,
            description: `A reply appeared in the thread after archiving "${msg.subject}" — should the archive rule be reconsidered?`,
          });
        }
      }
    }
  }

  return anomalies;
}
