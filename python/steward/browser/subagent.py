"""Browser sub-agent — read-only. CDP layer deferred; a fake is provided for tests."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class BrowserSubAgent(Protocol):
    async def dispatch(self, instruction: dict[str, Any]) -> dict[str, Any]: ...
    async def verify(self, url: str) -> dict[str, Any]: ...


@dataclass
class FakeBrowserSubAgent:
    """Fake browser sub-agent for testing — no real browser needed."""

    responses: dict[str, dict[str, str]]

    async def dispatch(self, instruction: dict[str, Any]) -> dict[str, Any]:
        if instruction.get("capability") != "browser_read":
            return {
                "success": False,
                "action_taken": instruction.get("capability") or "unknown",
                "url": instruction.get("url", ""),
                "error": f"unknown capability: {instruction.get('capability')}",
            }
        url = instruction.get("url", "")
        entry = self.responses.get(url)
        if not entry:
            return {
                "success": False,
                "action_taken": "browser_read",
                "url": url,
                "error": f"no canned response for URL: {url}",
            }
        return {
            "success": True,
            "action_taken": "browser_read",
            "url": url,
            "pageTitle": entry["title"],
            "textContent": entry["text"],
        }

    async def verify(self, url: str) -> dict[str, Any]:
        entry = self.responses.get(url)
        return {
            "verified": entry is not None,
            "actual_url": url,
            "actual_title": entry["title"] if entry else "",
        }


def create_fake_browser_sub_agent(responses: dict[str, dict[str, str]]) -> FakeBrowserSubAgent:
    return FakeBrowserSubAgent(responses=responses)
