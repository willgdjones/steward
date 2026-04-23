"""Spawn the planner as a subprocess. Sanitise credential-bearing env vars before exec."""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

from steward.planner import Goal

FORBIDDEN_ENV = re.compile(r"^(.*(CRED|TOKEN|SECRET|KEY|PASSWORD).*|STEWARD_CREDENTIALS_DIR)$", re.IGNORECASE)


def sanitise_env_for_planner(env: dict[str, str] | None = None) -> dict[str, str]:
    source = env if env is not None else dict(os.environ)
    return {k: v for k, v in source.items() if not FORBIDDEN_ENV.match(k)}


async def run_planner(input_data: dict[str, Any]) -> Goal:
    proc = await asyncio.create_subprocess_exec(
        "python",
        "-m",
        "steward.planner",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=sanitise_env_for_planner(),
    )
    stdout, stderr = await proc.communicate(
        (json.dumps(input_data) + "\n").encode("utf-8")
    )
    line = stdout.decode("utf-8").strip().split("\n")[-1] if stdout else ""
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"planner output not JSON: {line!r}") from e
    if "error" in parsed:
        raise RuntimeError(parsed["error"])
    return Goal(**parsed)
