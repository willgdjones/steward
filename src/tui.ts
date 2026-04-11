import WebSocket from 'ws';

/**
 * Minimal terminal client for steward.
 * Connects to the local HTTP API + WebSocket for live updates.
 *
 * Keybindings:
 *   j/k  — navigate down/up
 *   y    — approve
 *   n    — reject
 *   d    — defer
 *   q    — quit
 */

interface CardData {
  id: string;
  title: string;
  reason: string;
  messageId: string;
  transport?: string;
  action?: string;
  breakdown?: Record<string, unknown>;
  exploration?: boolean;
}

interface QueueUpdate {
  type: 'queue_update';
  depth: number;
  cards: CardData[];
}

const BASE = process.env.STEWARD_URL ?? 'http://127.0.0.1:4040';
const WS_URL = BASE.replace(/^http/, 'ws') + '/';

let cards: CardData[] = [];
let selectedIndex = 0;
let connected = false;

function isIrreversible(card: CardData): boolean {
  return card.title.includes('irreversible') || card.id.startsWith('reapproval-');
}

function renderCard(card: CardData, selected: boolean): string {
  const sel = selected ? '▶ ' : '  ';
  const badge = isIrreversible(card) ? ' ⚠ IRREVERSIBLE' : '';
  const lines = [
    `${sel}┌─────────────────────────────────────────────────────┐`,
    `${sel}│ ${truncate(card.title, 50)}${badge}`,
    `${sel}│ ${truncate(card.reason, 50)}`,
    `${sel}│ ${card.transport ?? ''}/${card.action ?? ''} · ${card.messageId}`,
    `${sel}└─────────────────────────────────────────────────────┘`,
  ];
  return lines.join('\n');
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max - 1) + '…' : s.padEnd(max);
}

function render(): void {
  // Clear screen
  process.stdout.write('\x1b[2J\x1b[H');

  const status = connected ? '● connected' : '○ disconnected';
  process.stdout.write(`steward tui  ${status}  ${cards.length} card(s)\n\n`);

  if (cards.length === 0) {
    process.stdout.write('  No cards in queue.\n');
  } else {
    for (let i = 0; i < cards.length; i++) {
      process.stdout.write(renderCard(cards[i], i === selectedIndex) + '\n\n');
    }
  }

  process.stdout.write('\n  j/k navigate  y approve  n reject  d defer  q quit\n');
}

async function decide(decision: 'approve' | 'reject' | 'defer'): Promise<void> {
  if (cards.length === 0) return;
  const card = cards[selectedIndex];
  try {
    const res = await fetch(`${BASE}/card/${card.id}/decision`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ decision }),
    });
    if (!res.ok) {
      const body = await res.json() as Record<string, unknown>;
      process.stdout.write(`\n  Error: ${body.error ?? res.status}\n`);
    }
  } catch (err) {
    process.stdout.write(`\n  Error: ${(err as Error).message}\n`);
  }
  // WebSocket update will re-render; if not connected, poll
  if (!connected) await pollQueue();
}

async function pollQueue(): Promise<void> {
  try {
    const res = await fetch(`${BASE}/queue`);
    const data = (await res.json()) as QueueUpdate;
    cards = data.cards;
    if (selectedIndex >= cards.length) selectedIndex = Math.max(0, cards.length - 1);
    render();
  } catch {
    // Server unreachable
  }
}

function connectWebSocket(): void {
  const ws = new WebSocket(WS_URL);

  ws.on('open', () => {
    connected = true;
    render();
  });

  ws.on('message', (data) => {
    try {
      const msg = JSON.parse(data.toString()) as QueueUpdate;
      if (msg.type === 'queue_update') {
        cards = msg.cards;
        if (selectedIndex >= cards.length) selectedIndex = Math.max(0, cards.length - 1);
        render();
      }
    } catch { /* ignore malformed */ }
  });

  ws.on('close', () => {
    connected = false;
    render();
    // Reconnect after 2s
    setTimeout(connectWebSocket, 2000);
  });

  ws.on('error', () => {
    connected = false;
    // Will trigger close
  });
}

function main(): void {
  // Enable raw mode for keypress handling
  if (!process.stdin.isTTY) {
    console.error('steward tui requires an interactive terminal');
    process.exit(1);
  }
  process.stdin.setRawMode(true);
  process.stdin.resume();
  process.stdin.setEncoding('utf8');

  render();
  connectWebSocket();

  process.stdin.on('data', (key: string) => {
    switch (key) {
      case 'j':
        if (selectedIndex < cards.length - 1) {
          selectedIndex++;
          render();
        }
        break;
      case 'k':
        if (selectedIndex > 0) {
          selectedIndex--;
          render();
        }
        break;
      case 'y':
        decide('approve');
        break;
      case 'n':
        decide('reject');
        break;
      case 'd':
        decide('defer');
        break;
      case 'q':
      case '\u0003': // Ctrl+C
        process.stdout.write('\x1b[2J\x1b[H');
        process.exit(0);
        break;
    }
  });
}

main();
