"""Minimal terminal client. Connects to local HTTP + WebSocket for live updates.

Keybindings: j/k navigate, y approve, n reject, d defer, q quit.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import termios
import tty
from dataclasses import dataclass
from typing import Any

import aiohttp


BASE = os.environ.get("STEWARD_URL", "http://127.0.0.1:8731")
WS_URL = BASE.replace("http://", "ws://").replace("https://", "wss://") + "/ws"


@dataclass
class UIState:
    cards: list[dict[str, Any]]
    selected: int = 0
    connected: bool = False


def _truncate(s: str, n: int) -> str:
    return (s[: n - 1] + "…") if len(s) > n else s.ljust(n)


def _is_irreversible(card: dict[str, Any]) -> bool:
    title = card.get("title", "") or ""
    cid = card.get("id", "") or ""
    return "irreversible" in title or cid.startswith("reapproval-")


def _render(state: UIState) -> None:
    sys.stdout.write("\x1b[2J\x1b[H")
    status = "● connected" if state.connected else "○ disconnected"
    sys.stdout.write(f"steward tui  {status}  {len(state.cards)} card(s)\n\n")
    if not state.cards:
        sys.stdout.write("  No cards in queue.\n")
    else:
        for i, card in enumerate(state.cards):
            sel = "▶ " if i == state.selected else "  "
            badge = " ⚠ IRREVERSIBLE" if _is_irreversible(card) else ""
            sys.stdout.write(f"{sel}┌─────────────────────────────────────────────────────┐\n")
            sys.stdout.write(f"{sel}│ {_truncate(card.get('title', ''), 50)}{badge}\n")
            sys.stdout.write(f"{sel}│ {_truncate(card.get('reason', ''), 50)}\n")
            sys.stdout.write(
                f"{sel}│ {card.get('transport', '')}/{card.get('action', '')} · {card.get('messageId', '')}\n"
            )
            sys.stdout.write(f"{sel}└─────────────────────────────────────────────────────┘\n\n")
    sys.stdout.write("\n  j/k navigate  y approve  n reject  d defer  q quit\n")
    sys.stdout.flush()


async def _decide(session: aiohttp.ClientSession, card_id: str, decision: str) -> None:
    try:
        await session.post(f"{BASE}/card/{card_id}/decision", json={"decision": decision})
    except Exception as e:
        sys.stdout.write(f"\n  Error: {e}\n")


async def _ws_loop(state: UIState, session: aiohttp.ClientSession) -> None:
    while True:
        try:
            async with session.ws_connect(WS_URL) as ws:
                state.connected = True
                _render(state)
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            data = json.loads(msg.data)
                            if data.get("type") == "queue_update":
                                state.cards = data.get("cards", [])
                                if state.selected >= len(state.cards):
                                    state.selected = max(0, len(state.cards) - 1)
                                _render(state)
                        except Exception:
                            pass
        except Exception:
            state.connected = False
            _render(state)
            await asyncio.sleep(2)


async def _input_loop(state: UIState, session: aiohttp.ClientSession, stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        key = await loop.run_in_executor(None, sys.stdin.read, 1)
        if key == "j" and state.selected < len(state.cards) - 1:
            state.selected += 1
            _render(state)
        elif key == "k" and state.selected > 0:
            state.selected -= 1
            _render(state)
        elif key in ("y", "n", "d") and state.cards:
            decision = {"y": "approve", "n": "reject", "d": "defer"}[key]
            await _decide(session, state.cards[state.selected]["id"], decision)
        elif key in ("q", "\x03"):
            sys.stdout.write("\x1b[2J\x1b[H")
            stop.set()
            break


async def _run() -> None:
    state = UIState(cards=[], selected=0, connected=False)
    stop = asyncio.Event()
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    try:
        async with aiohttp.ClientSession() as session:
            _render(state)
            ws_task = asyncio.create_task(_ws_loop(state, session))
            input_task = asyncio.create_task(_input_loop(state, session, stop))
            await stop.wait()
            ws_task.cancel()
            input_task.cancel()
            await asyncio.gather(ws_task, input_task, return_exceptions=True)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def main() -> None:
    if not sys.stdin.isatty():
        print("steward tui requires an interactive terminal", file=sys.stderr)
        sys.exit(1)
    asyncio.run(_run())


if __name__ == "__main__":
    main()
