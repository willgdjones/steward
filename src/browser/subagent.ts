import { CdpClient, listTargets } from './cdp.js';

/**
 * Instruction sent to the browser sub-agent.
 * Free-form natural-language instruction in, structured outcome out.
 */
export interface BrowserInstruction {
  capability: 'browser_read';
  /** The URL to navigate to and extract data from. */
  url: string;
  /** Free-form instruction describing what data to extract. */
  instruction: string;
  /** Optional CSS selector to scope extraction. */
  selector?: string;
}

/**
 * Structured outcome returned by the browser sub-agent.
 */
export interface BrowserOutcome {
  success: boolean;
  action_taken: string;
  url: string;
  /** Extracted data as structured key-value pairs. */
  extracted?: Record<string, unknown>;
  /** Raw text content extracted from the page or element. */
  textContent?: string;
  /** Page title at time of extraction. */
  pageTitle?: string;
  error?: string;
}

/**
 * Verification result: re-fetch to confirm extracted data.
 */
export interface BrowserVerification {
  verified: boolean;
  actual_url: string;
  actual_title: string;
}

export interface BrowserSubAgent {
  dispatch(instruction: BrowserInstruction): Promise<BrowserOutcome>;
  verify(url: string): Promise<BrowserVerification>;
}

/**
 * Create a browser sub-agent backed by a CDP client.
 * Read-only only: navigates and extracts, never submits forms or clicks buttons.
 *
 * @param cdpPort Chrome debugging port (default 9222)
 */
export function createBrowserSubAgent(cdpPort = 9222): BrowserSubAgent {
  return {
    async dispatch(instruction: BrowserInstruction): Promise<BrowserOutcome> {
      if (instruction.capability !== 'browser_read') {
        return {
          success: false,
          action_taken: instruction.capability,
          url: instruction.url,
          error: `unknown capability: ${instruction.capability}`,
        };
      }

      const cdp = new CdpClient();
      try {
        // Find an available page target or use the debugging endpoint directly
        const targets = await listTargets(cdpPort);
        const pageTarget = targets.find((t) => t.type === 'page');
        if (!pageTarget) {
          return {
            success: false,
            action_taken: 'browser_read',
            url: instruction.url,
            error: 'no browser page target available — is Chrome running with --remote-debugging-port?',
          };
        }

        const wsUrl = `ws://127.0.0.1:${cdpPort}/devtools/page/${pageTarget.id}`;
        await cdp.connect(wsUrl);
        await cdp.navigate(instruction.url);

        const pageTitle = await cdp.getTitle();
        let textContent: string;

        if (instruction.selector) {
          // Extract text from a specific element
          const text = await cdp.evaluate(
            `(() => { const el = document.querySelector(${JSON.stringify(instruction.selector)}); return el ? el.textContent : null; })()`,
          );
          textContent = (text as string) ?? '';
        } else {
          // Extract full page text
          textContent = (await cdp.evaluate('document.body.innerText')) as string ?? '';
        }

        return {
          success: true,
          action_taken: 'browser_read',
          url: instruction.url,
          pageTitle,
          textContent: textContent.slice(0, 5000), // Cap to avoid huge payloads
        };
      } catch (err) {
        return {
          success: false,
          action_taken: 'browser_read',
          url: instruction.url,
          error: (err as Error).message,
        };
      } finally {
        cdp.close();
      }
    },

    async verify(url: string): Promise<BrowserVerification> {
      const cdp = new CdpClient();
      try {
        const targets = await listTargets(cdpPort);
        const pageTarget = targets.find((t) => t.type === 'page');
        if (!pageTarget) {
          return { verified: false, actual_url: '', actual_title: '' };
        }

        const wsUrl = `ws://127.0.0.1:${cdpPort}/devtools/page/${pageTarget.id}`;
        await cdp.connect(wsUrl);
        await cdp.navigate(url);

        const actual_url = await cdp.getUrl();
        const actual_title = await cdp.getTitle();

        return {
          verified: actual_url === url || actual_url.startsWith(url),
          actual_url,
          actual_title,
        };
      } catch {
        return { verified: false, actual_url: '', actual_title: '' };
      } finally {
        cdp.close();
      }
    },
  };
}

/**
 * Fake browser sub-agent for testing — no real browser needed.
 * Accepts canned responses.
 */
export function createFakeBrowserSubAgent(
  responses: Map<string, { title: string; text: string }>,
): BrowserSubAgent {
  return {
    async dispatch(instruction: BrowserInstruction): Promise<BrowserOutcome> {
      if (instruction.capability !== 'browser_read') {
        return {
          success: false,
          action_taken: instruction.capability,
          url: instruction.url,
          error: `unknown capability: ${instruction.capability}`,
        };
      }
      const entry = responses.get(instruction.url);
      if (!entry) {
        return {
          success: false,
          action_taken: 'browser_read',
          url: instruction.url,
          error: `no canned response for URL: ${instruction.url}`,
        };
      }
      return {
        success: true,
        action_taken: 'browser_read',
        url: instruction.url,
        pageTitle: entry.title,
        textContent: entry.text,
      };
    },

    async verify(url: string): Promise<BrowserVerification> {
      const entry = responses.get(url);
      return {
        verified: !!entry,
        actual_url: url,
        actual_title: entry?.title ?? '',
      };
    },
  };
}
