"""Planner subprocess entry point. Reads JSON on stdin, writes Goal JSON on stdout."""
from __future__ import annotations

import json
import sys

from steward.planner import plan_goal


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            if "message" in data:
                goal = plan_goal(data["message"])
            else:
                goal = plan_goal(data)
            sys.stdout.write(json.dumps(goal.to_dict()) + "\n")
            sys.stdout.flush()
        except Exception as e:
            sys.stdout.write(json.dumps({"error": str(e)}) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
