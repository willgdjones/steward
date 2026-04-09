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
}

/**
 * Structured outcome returned by the sub-agent after executing.
 */
export interface SubAgentOutcome {
  success: boolean;
  action_taken: string;
  messageId: string;
  error?: string;
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
  verify(messageId: string, expectedAction: string): Promise<VerificationResult>;
}

/**
 * Create a Gmail sub-agent backed by the given Gmail provider.
 * The sub-agent accepts free-form instructions and returns structured outcomes.
 */
export function createGmailSubAgent(gmail: FakeGmail): GmailSubAgent {
  return {
    async dispatch(instruction: SubAgentInstruction): Promise<SubAgentOutcome> {
      if (instruction.capability !== 'archive') {
        return {
          success: false,
          action_taken: instruction.capability,
          messageId: instruction.messageId,
          error: `unknown capability: ${instruction.capability}`,
        };
      }

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
    },

    async verify(messageId: string, expectedAction: string): Promise<VerificationResult> {
      if (expectedAction !== 'archive') {
        return { verified: false, actual_state: 'unknown', messageId };
      }

      const msg = gmail.getById(messageId);
      if (!msg) {
        return { verified: false, actual_state: 'not_found', messageId };
      }

      return {
        verified: msg.archived === true,
        actual_state: msg.archived ? 'archived' : 'not_archived',
        messageId,
      };
    },
  };
}
