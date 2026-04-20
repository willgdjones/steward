import WebSocket from 'ws';

/**
 * Minimal Chrome DevTools Protocol client.
 * Inspired by browser-harness-js: "the protocol is the API" —
 * one WebSocket to Chrome, nothing between.
 */

export interface CdpResponse {
  id: number;
  result?: Record<string, unknown>;
  error?: { code: number; message: string };
}

export interface CdpEvent {
  method: string;
  params: Record<string, unknown>;
}

interface Pending {
  resolve: (result: Record<string, unknown>) => void;
  reject: (error: Error) => void;
}

export class CdpClient {
  private ws: WebSocket | null = null;
  private nextId = 1;
  private pending = new Map<number, Pending>();
  private sessionId: string | null = null;

  /** Connect to a CDP WebSocket endpoint (e.g. ws://127.0.0.1:9222/devtools/page/...) */
  async connect(wsUrl: string): Promise<void> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(wsUrl);
      ws.on('open', () => {
        this.ws = ws;
        resolve();
      });
      ws.on('message', (data) => this.onMessage(data.toString()));
      ws.on('error', reject);
      ws.on('close', () => { this.ws = null; });
    });
  }

  /** Attach to a specific page target by ID. */
  async attachToTarget(targetId: string): Promise<string> {
    const result = await this.send('Target.attachToTarget', { targetId, flatten: true });
    this.sessionId = result.sessionId as string;
    return this.sessionId;
  }

  /** Send a CDP method call. */
  async send(method: string, params: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    if (!this.ws) throw new Error('not connected');
    const id = this.nextId++;
    const msg: Record<string, unknown> = { id, method, params };
    if (this.sessionId && !isBrowserLevel(method)) {
      msg.sessionId = this.sessionId;
    }
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws!.send(JSON.stringify(msg));
    });
  }

  /** Navigate to a URL and wait for load. */
  async navigate(url: string): Promise<void> {
    await this.send('Page.enable');
    await this.send('Page.navigate', { url });
    await this.waitForEvent('Page.loadEventFired', 15_000);
  }

  /** Get the page's DOM as HTML. */
  async getPageContent(): Promise<string> {
    const result = await this.send('Runtime.evaluate', {
      expression: 'document.documentElement.outerHTML',
      returnByValue: true,
    });
    const val = result.result as Record<string, unknown> | undefined;
    return (val?.value as string) ?? '';
  }

  /** Evaluate a JavaScript expression and return the result. */
  async evaluate(expression: string): Promise<unknown> {
    const result = await this.send('Runtime.evaluate', {
      expression,
      returnByValue: true,
    });
    const val = result.result as Record<string, unknown> | undefined;
    if (val?.type === 'undefined') return undefined;
    return val?.value;
  }

  /** Get the page title. */
  async getTitle(): Promise<string> {
    return (await this.evaluate('document.title')) as string ?? '';
  }

  /** Get the current URL. */
  async getUrl(): Promise<string> {
    return (await this.evaluate('window.location.href')) as string ?? '';
  }

  /** Wait for a specific CDP event. */
  private waitForEvent(method: string, timeoutMs: number): Promise<Record<string, unknown>> {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`timeout waiting for ${method}`)), timeoutMs);
      const check = (data: string) => {
        try {
          const msg = JSON.parse(data) as CdpEvent;
          if (msg.method === method) {
            clearTimeout(timer);
            this.ws?.removeListener('message', check);
            resolve(msg.params);
          }
        } catch { /* ignore */ }
      };
      this.ws?.on('message', check);
    });
  }

  private onMessage(data: string): void {
    const msg = JSON.parse(data) as CdpResponse & { method?: string };
    if (msg.id !== undefined) {
      const p = this.pending.get(msg.id);
      if (p) {
        this.pending.delete(msg.id);
        if (msg.error) {
          p.reject(new Error(`CDP error: ${msg.error.message}`));
        } else {
          p.resolve(msg.result ?? {});
        }
      }
    }
  }

  close(): void {
    this.ws?.close();
    this.ws = null;
  }

  get connected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }
}

/** Discover available page targets from the Chrome debugging endpoint. */
export async function listTargets(debuggingPort = 9222): Promise<Array<{ id: string; title: string; url: string; type: string }>> {
  const res = await fetch(`http://127.0.0.1:${debuggingPort}/json/list`);
  return (await res.json()) as Array<{ id: string; title: string; url: string; type: string }>;
}

function isBrowserLevel(method: string): boolean {
  return method.startsWith('Target.') || method.startsWith('Browser.');
}
