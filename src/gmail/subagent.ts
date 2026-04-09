import type { FakeGmail } from './fake.js';

/**
 * Free-form instruction sent to the Gmail sub-agent.
 * The executor builds this from the approved goal.
 */
export interface SubAgentInstruction {
  capability: string;
  messageId: string;
  /** Free-form natural-language instruction describing what to do. */
  instruction: string;
  /** For draft_reply: the body of the draft to create. */
  draftBody?: string;
}

/**
 * Structured outcome returned by the sub-agent after executing.
 */
export interface SubAgentOutcome {
  success: boolean;
  action_taken: string;
  messageId: string;
  error?: string;
  /** For draft_reply: the ID of the created draft. */
  draftId?: string;
}

/**
 * Result of a post-action verification step.
 */
export interface VerificationResult {
  verified: boolean;
  actual_state: string;
  messageId: string;
}

export interface GmailSubAgent {
  dispatch(instruction: SubAgentInstruction): Promise<SubAgentOutcome>;
  verify(messageId: string, expectedAction: string, meta?: { draftId?: string }): Promise<VerificationResult>;
}

/**
 * Create a Gmail sub-agent backed by the given Gmail provider.
 * The sub-agent accepts free-form instructions and returns structured outcomes.
 */
export function createGmailSubAgent(gmail: FakeGmail): GmailSubAgent {
  return {
    async dispatch(instruction: SubAgentInstruction): Promise<SubAgentOutcome> {
      if (instruction.capability === 'archive') {
        const found = gmail.archive(instruction.messageId);
        if (!found) {
          return {
            success: false,
            action_taken: 'archive',
            messageId: instruction.messageId,
            error: `message not found: ${instruction.messageId}`,
          };
        }
        return {
          success: true,
          action_taken: 'archive',
          messageId: instruction.messageId,
        };
      }

      if (instruction.capability === 'draft_reply') {
        const body = instruction.draftBody ?? instruction.instruction;
        const draft = gmail.createDraft(instruction.messageId, body);
        if (!draft) {
          return {
            success: false,
            action_taken: 'draft_reply',
            messageId: instruction.messageId,
            error: `message not found: ${instruction.messageId}`,
          };
        }
        return {
          success: true,
          action_taken: 'draft_reply',
          messageId: instruction.messageId,
          draftId: draft.id,
        };
      }

      return {
        success: false,
        action_taken: instruction.capability,
        messageId: instruction.messageId,
        error: `unknown capability: ${instruction.capability}`,
      };
    },

    async verify(messageId: string, expectedAction: string, meta?: { draftId?: string }): Promise<VerificationResult> {
      if (expectedAction === 'archive') {
        const msg = gmail.getById(messageId);
        if (!msg) {
          return { verified: false, actual_state: 'not_found', messageId };
        }
        return {
          verified: msg.archived === true,
          actual_state: msg.archived ? 'archived' : 'not_archived',
          messageId,
        };
      }

      if (expectedAction === 'draft_reply') {
        if (!meta?.draftId) {
          return { verified: false, actual_state: 'no_draft_id', messageId };
        }
        const draft = gmail.getDraft(meta.draftId);
        if (!draft) {
          return { verified: false, actual_state: 'draft_not_found', messageId };
        }
        return {
          verified: draft.inReplyTo === messageId,
          actual_state: 'draft_exists',
          messageId,
        };
      }

      return { verified: false, actual_state: 'unknown', messageId };
    },
  };
}
