#!/usr/bin/env python3
"""Inject the shared Claude CRM memory index into Codex SessionStart context."""

from __future__ import annotations

import json
import os
from pathlib import Path

MEMORY_INDEX = Path(
    os.environ.get(
        "CRM_CODEX_MEMORY_INDEX",
        "/home/gharib/.claude/projects/-home-gharib-wip-projects-crm/memory/MEMORY.md",
    )
)


def main() -> int:
    try:
        memory = MEMORY_INDEX.read_text(encoding="utf-8")
    except OSError as exc:
        memory = f"CRM project memory index was not loaded: failed to read {MEMORY_INDEX}: {exc}"

    context = (
        "<CRM_PROJECT_MEMORY_INDEX>\n"
        f"Source: {MEMORY_INDEX}\n\n"
        "This is the shared Claude/Codex CRM memory index. Treat it as an index "
        "of durable project memories, not as a complete substitute for reading "
        "the referenced memory files. Before substantial CRM work, check this "
        "index for relevant entries and read the referenced memory file(s) before "
        "acting.\n\n"
        f"{memory}\n"
        "</CRM_PROJECT_MEMORY_INDEX>"
    )

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": context,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
