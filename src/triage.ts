import Anthropic from '@anthropic-ai/sdk';
import type { GmailMessage } from './gmail/fake.js';

/**
 * Structured features extracted by the cheap triage model (Haiku).
 * These are non-sensitive structured signals used by the planner
 * to form goals. The triage model sees the full message; the planner
 * only sees the redacted message plus these features.
 */
export interface TriageFeatures {
  deadline: string | null;
  amount: string | null;
  waiting_on_user: boolean;
  category: string;
  urgency: 'high' | 'medium' | 'low';
}

export interface TriageResult {
  features: TriageFeatures;
  /** Short factual summary safe to pass through the redactor to the planner. */
  snippet: string;
}

const TRIAGE_SYSTEM = `You are an email triage assistant. Extract structured features from the email.
Respond with valid JSON only, no markdown, no explanation.

Schema:
{
  "features": {
    "deadline": "<ISO date string or null if no deadline>",
    "amount": "<monetary amount as string or null if none>",
    "waiting_on_user": <true if the sender is waiting for the user to respond>,
    "category": "<one of: newsletter, transaction, personal, work, notification, marketing, other>",
    "urgency": "<one of: high, medium, low>"
  },
  "snippet": "<one-sentence factual summary of what the email is about, no personal names or account numbers>"
}`;

export type TriageFn = (message: GmailMessage) => Promise<TriageResult>;

/**
 * Create a triage function backed by a cheap frontier model.
 * Injectable so tests can substitute a fake.
 */
export function createTriage(
  client: Anthropic,
  model = 'claude-haiku-4-5-20251001',
): TriageFn {
  return async (message: GmailMessage): Promise<TriageResult> => {
    const userContent = `From: ${message.from}\nSubject: ${message.subject}\n\n${message.body}`;

    const response = await client.messages.create({
      model,
      max_tokens: 512,
      system: TRIAGE_SYSTEM,
      messages: [{ role: 'user', content: userContent }],
    });

    const text = response.content
      .filter((b) => b.type === 'text')
      .map((b) => b.text)
      .join('');

    return JSON.parse(text) as TriageResult;
  };
}

/** Default triage features for testing / fallback. */
export function defaultTriageResult(): TriageResult {
  return {
    features: {
      deadline: null,
      amount: null,
      waiting_on_user: false,
      category: 'other',
      urgency: 'low',
    },
    snippet: 'No triage available.',
  };
}
