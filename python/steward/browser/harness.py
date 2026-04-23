"""Real browser sub-agent backed by browser-harness CLI.

Invokes `browser-harness` as a subprocess with a generated Python script on
stdin. The script uses browser-harness's pre-imported helpers (`new_tab`,
`wait_for_load`, `goto`, `js`, `page_info`) to drive the user's running Chrome
via CDP.

Credential handling — the load-bearing part:
- Resolved credential values are passed to the subprocess via environment
  variables (STEWARD_CRED_USERNAME, STEWARD_CRED_PASSWORD). They are never
  baked into the script text, never written to disk, never logged.
- The env vars exist only for the lifetime of the subprocess.
- The generated script reads them via os.environ at runtime.
- Form fill is done via JS with `document.querySelector(selector).value = ...`
  and explicit `input`/`change` event dispatch, so framework forms (React,
  Vue) pick up the value. Coordinate clicks are avoided for field fills —
  they're brittle for hidden/offscreen/shadow-DOM fields.
- The sub-agent's stdout is captured and parsed for a line beginning with
  `STEWARD_RESULT:` followed by JSON. Everything else on stdout is ignored
  (browser-harness emits its own diagnostic output).

The caller (executor) is responsible for running the result through
`redact_browser_outcome` before journaling or returning over HTTP. This
module does NOT redact — keeping that at the executor boundary means the
same redactor hook applies to the fake and the real sub-agent.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any

from steward.browser.subagent import AUTHENTICATED_READ_CAPABILITY, READ_CAPABILITY

HARNESS_CMD = "browser-harness"
DEFAULT_TIMEOUT = 120  # seconds
RESULT_SENTINEL = "STEWARD_RESULT:"
CRED_USER_ENV = "STEWARD_CRED_USERNAME"
CRED_PASS_ENV = "STEWARD_CRED_PASSWORD"


def build_authenticated_script(
    *,
    login_url: str,
    target_url: str,
    username_selector: str,
    password_selector: str,
    submit_selector: str,
    extract_selector: str | None = None,
) -> str:
    """Generate the Python script that runs inside browser-harness.

    The script reads credentials from env vars at runtime. It does NOT contain
    the resolved values — the only literals baked in are URLs, selectors, and
    the extract-selector if provided.
    """
    # Every literal below is JSON-serialized so quoting and escaping are safe.
    return f"""
import json, os, sys

_user = os.environ.get({json.dumps(CRED_USER_ENV)}, "")
_pwd = os.environ.get({json.dumps(CRED_PASS_ENV)}, "")

_LOGIN_URL = {json.dumps(login_url)}
_TARGET_URL = {json.dumps(target_url)}
_USERNAME_SELECTOR = {json.dumps(username_selector)}
_PASSWORD_SELECTOR = {json.dumps(password_selector)}
_SUBMIT_SELECTOR = {json.dumps(submit_selector)}
_EXTRACT_SELECTOR = {json.dumps(extract_selector) if extract_selector else "None"}


def _fill(selector, value):
    expr = (
        "(()=>{{"
        "const el=document.querySelector(" + json.dumps(selector) + ");"
        "if(!el)return false;"
        "el.focus();"
        "el.value=" + json.dumps(value) + ";"
        "el.dispatchEvent(new Event('input',{{bubbles:true}}));"
        "el.dispatchEvent(new Event('change',{{bubbles:true}}));"
        "return true;"
        "}})()"
    )
    ok = js(expr)
    if not ok:
        raise RuntimeError("selector not found: " + selector)


def _click(selector):
    expr = (
        "(()=>{{"
        "const el=document.querySelector(" + json.dumps(selector) + ");"
        "if(!el)return false;"
        "el.click();"
        "return true;"
        "}})()"
    )
    ok = js(expr)
    if not ok:
        raise RuntimeError("submit selector not found: " + selector)


def _extract():
    info = page_info()
    if _EXTRACT_SELECTOR:
        expr = (
            "(()=>{{"
            "const el=document.querySelector(" + json.dumps(_EXTRACT_SELECTOR) + ");"
            "return el?el.innerText:null;"
            "}})()"
        )
        text = js(expr) or ""
    else:
        text = js("document.body.innerText") or ""
    return {{
        "url": info.get("url", ""),
        "title": info.get("title", ""),
        "text": text[:5000],
    }}


try:
    new_tab(_LOGIN_URL)
    wait_for_load()
    _fill(_USERNAME_SELECTOR, _user)
    _fill(_PASSWORD_SELECTOR, _pwd)
    _click(_SUBMIT_SELECTOR)
    wait_for_load()
    goto(_TARGET_URL)
    wait_for_load()
    result = _extract()
    result["success"] = True
except Exception as e:
    result = {{"success": False, "error": str(e)}}

print({json.dumps(RESULT_SENTINEL)} + json.dumps(result))
""".lstrip()


def parse_result(stdout: str) -> dict[str, Any]:
    """Find the STEWARD_RESULT: line in stdout and parse its JSON payload."""
    for line in stdout.splitlines():
        if line.startswith(RESULT_SENTINEL):
            payload = line[len(RESULT_SENTINEL):].strip()
            return json.loads(payload)
    raise RuntimeError(f"no {RESULT_SENTINEL} line found in browser-harness stdout")


@dataclass
class BrowserHarnessSubAgent:
    """Real browser sub-agent. Implements the BrowserSubAgent protocol."""

    cmd: str = HARNESS_CMD
    timeout: float = DEFAULT_TIMEOUT
    # Injectable for tests — defaults to real subprocess invocation.
    runner: Any = None  # type: ignore[assignment]

    async def dispatch(self, instruction: dict[str, Any]) -> dict[str, Any]:
        cap = instruction.get("capability")
        if cap == AUTHENTICATED_READ_CAPABILITY:
            return await self._dispatch_authenticated(instruction)
        if cap == READ_CAPABILITY:
            return await self._dispatch_read(instruction)
        return {
            "success": False,
            "action_taken": cap or "unknown",
            "url": instruction.get("url") or instruction.get("target_url", ""),
            "error": f"unknown capability: {cap}",
        }

    async def _dispatch_authenticated(self, instruction: dict[str, Any]) -> dict[str, Any]:
        target_url = instruction.get("target_url", "")
        resolved_creds = instruction.get("resolved_creds") or ["", ""]
        username, password = (resolved_creds + ["", ""])[:2]

        script = build_authenticated_script(
            login_url=instruction.get("login_url", ""),
            target_url=target_url,
            username_selector=instruction.get("username_selector", ""),
            password_selector=instruction.get("password_selector", ""),
            submit_selector=instruction.get("submit_selector", ""),
            extract_selector=instruction.get("selector"),
        )

        env = dict(os.environ)
        env[CRED_USER_ENV] = username
        env[CRED_PASS_ENV] = password

        try:
            stdout = await self._run(script, env)
        except Exception as e:
            return {
                "success": False,
                "action_taken": AUTHENTICATED_READ_CAPABILITY,
                "url": target_url,
                "error": f"browser-harness invocation failed: {e}",
            }

        try:
            result = parse_result(stdout)
        except Exception as e:
            return {
                "success": False,
                "action_taken": AUTHENTICATED_READ_CAPABILITY,
                "url": target_url,
                "error": f"could not parse harness output: {e}",
            }

        if not result.get("success"):
            return {
                "success": False,
                "action_taken": AUTHENTICATED_READ_CAPABILITY,
                "url": target_url,
                "error": result.get("error", "unknown harness error"),
            }
        return {
            "success": True,
            "action_taken": AUTHENTICATED_READ_CAPABILITY,
            "url": result.get("url") or target_url,
            "pageTitle": result.get("title", ""),
            "textContent": result.get("text", ""),
        }

    async def _dispatch_read(self, instruction: dict[str, Any]) -> dict[str, Any]:
        url = instruction.get("url", "")
        script = f"""
import json
try:
    new_tab({json.dumps(url)})
    wait_for_load()
    info = page_info()
    text = js("document.body.innerText") or ""
    result = {{"success": True, "url": info.get("url",""), "title": info.get("title",""), "text": text[:5000]}}
except Exception as e:
    result = {{"success": False, "error": str(e)}}
print({json.dumps(RESULT_SENTINEL)} + json.dumps(result))
""".lstrip()
        try:
            stdout = await self._run(script, dict(os.environ))
        except Exception as e:
            return {"success": False, "action_taken": READ_CAPABILITY, "url": url,
                    "error": f"browser-harness invocation failed: {e}"}
        try:
            result = parse_result(stdout)
        except Exception as e:
            return {"success": False, "action_taken": READ_CAPABILITY, "url": url,
                    "error": f"could not parse harness output: {e}"}
        if not result.get("success"):
            return {"success": False, "action_taken": READ_CAPABILITY, "url": url,
                    "error": result.get("error", "unknown harness error")}
        return {
            "success": True,
            "action_taken": READ_CAPABILITY,
            "url": result.get("url") or url,
            "pageTitle": result.get("title", ""),
            "textContent": result.get("text", ""),
        }

    async def verify(self, url: str) -> dict[str, Any]:
        """Re-navigate to url and grab current url + title."""
        script = f"""
import json
try:
    goto({json.dumps(url)})
    wait_for_load()
    info = page_info()
    result = {{"success": True, "url": info.get("url",""), "title": info.get("title","")}}
except Exception as e:
    result = {{"success": False, "error": str(e)}}
print({json.dumps(RESULT_SENTINEL)} + json.dumps(result))
""".lstrip()
        try:
            stdout = await self._run(script, dict(os.environ))
            result = parse_result(stdout)
        except Exception:
            return {"verified": False, "actual_url": "", "actual_title": ""}
        if not result.get("success"):
            return {"verified": False, "actual_url": "", "actual_title": ""}
        actual_url = result.get("url", "")
        return {
            "verified": actual_url == url or actual_url.startswith(url),
            "actual_url": actual_url,
            "actual_title": result.get("title", ""),
        }

    async def _run(self, script: str, env: dict[str, str]) -> str:
        """Run browser-harness with script on stdin. Returns captured stdout.

        Injectable via self.runner for tests. The runner must accept
        (script, env, timeout) and return stdout.
        """
        if self.runner is not None:
            return await self.runner(script, env, self.timeout)

        proc = await asyncio.create_subprocess_exec(
            self.cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(script.encode("utf-8")),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError as e:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"browser-harness timed out after {self.timeout}s") from e
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"browser-harness exited {proc.returncode}: {err}")
        return stdout.decode("utf-8", errors="replace")


def create_browser_harness_sub_agent(**kwargs: Any) -> BrowserHarnessSubAgent:
    return BrowserHarnessSubAgent(**kwargs)
