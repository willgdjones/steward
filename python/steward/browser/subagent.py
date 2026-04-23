"""Browser sub-agent — read-only + authenticated-read. CDP layer deferred; fakes for tests."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

READ_CAPABILITY = "browser_read"
AUTHENTICATED_READ_CAPABILITY = "browser_authenticated_read"


class BrowserSubAgent(Protocol):
    async def dispatch(self, instruction: dict[str, Any]) -> dict[str, Any]: ...
    async def verify(self, url: str) -> dict[str, Any]: ...


@dataclass
class FakeBrowserSubAgent:
    """Fake browser sub-agent for testing — no real browser needed.

    Response entry schema:
        {
            "title": str,
            "text": str,
            "reflects_credentials": bool,   # optional — if True, append resolved_creds to text
            "reflects_in_title": bool,      # optional — if True, append resolved_creds to title
        }
    """

    responses: dict[str, dict[str, Any]]

    async def dispatch(self, instruction: dict[str, Any]) -> dict[str, Any]:
        cap = instruction.get("capability")
        if cap == READ_CAPABILITY:
            return self._dispatch_read(instruction)
        if cap == AUTHENTICATED_READ_CAPABILITY:
            return self._dispatch_authenticated_read(instruction)
        return {
            "success": False,
            "action_taken": cap or "unknown",
            "url": instruction.get("url") or instruction.get("target_url", ""),
            "error": f"unknown capability: {cap}",
        }

    def _dispatch_read(self, instruction: dict[str, Any]) -> dict[str, Any]:
        url = instruction.get("url", "")
        entry = self.responses.get(url)
        if not entry:
            return {
                "success": False,
                "action_taken": READ_CAPABILITY,
                "url": url,
                "error": f"no canned response for URL: {url}",
            }
        return {
            "success": True,
            "action_taken": READ_CAPABILITY,
            "url": url,
            "pageTitle": entry["title"],
            "textContent": entry["text"],
        }

    def _dispatch_authenticated_read(self, instruction: dict[str, Any]) -> dict[str, Any]:
        target_url = instruction.get("target_url", "")
        entry = self.responses.get(target_url)
        if not entry:
            return {
                "success": False,
                "action_taken": AUTHENTICATED_READ_CAPABILITY,
                "url": target_url,
                "error": f"no canned response for URL: {target_url}",
            }
        resolved_creds: list[str] = instruction.get("resolved_creds", [])
        text = entry["text"]
        title = entry["title"]
        if entry.get("reflects_credentials"):
            text = text + " [creds reflected: " + " ".join(resolved_creds) + "]"
        if entry.get("reflects_in_title"):
            title = title + " — " + " ".join(resolved_creds)
        return {
            "success": True,
            "action_taken": AUTHENTICATED_READ_CAPABILITY,
            "url": target_url,
            "pageTitle": title,
            "textContent": text,
        }

    async def verify(self, url: str) -> dict[str, Any]:
        entry = self.responses.get(url)
        return {
            "verified": entry is not None,
            "actual_url": url,
            "actual_title": entry["title"] if entry else "",
        }


def create_fake_browser_sub_agent(responses: dict[str, dict[str, Any]]) -> FakeBrowserSubAgent:
    return FakeBrowserSubAgent(responses=responses)
