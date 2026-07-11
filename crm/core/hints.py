"""Next-step hints — show-once success-path guidance for humans (issue #657).

Distinct from failure-enrichment `hint` (a fix-it string on error envelopes, see
CONTEXT.md): a next-step hint is *success*-path teaching ("what to try next"),
rendered only in human/REPL mode and **never** in the JSON envelope. Each hint
shows at most once per `CRM_HOME` (seen-ids persisted here); `CRM_NO_HINTS`
disables the whole subsystem.

The human/JSON/TTY gate lives at the caller (`CLIContext.hint`); this module owns
only the curated table, the env kill-switch, and the seen-store. That split keeps
`take_hint` pure of any terminal state and lets the store logic be unit-tested
without a CLI context.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

from crm.core.session import DEFAULT_HOME

# Curated hint table: id → the one-line guidance shown after the triggering
# command's normal output. Quality over quantity — each points at a natural
# next step a new human user is unlikely to discover on their own.
HINTS: dict[str, str] = {
    "profile_add": "Next: crm connection whoami — confirm you're connected to the right org.",
    "profile_use": "Tip: crm connection status shows the active target.",
    "solution_export": "Tip: crm solution unpack extracts the zip into source-control-friendly files.",
    "query_odata": "Tip: crm repl gives an interactive session with tab-completion for queries.",
}


def hints_disabled() -> bool:
    """True when the user has opted out via `CRM_NO_HINTS`. Presence in the
    environment disables hints regardless of value — including an empty string
    (`CRM_NO_HINTS=`), which a shell can set — matching the documented "any
    value" contract.
    """
    return "CRM_NO_HINTS" in os.environ


def _seen_path() -> Path:
    root = Path(os.environ.get("CRM_HOME", str(DEFAULT_HOME))).expanduser()
    return root / "hints_seen.json"


def load_seen() -> set[str]:
    """Return the set of already-shown hint ids. A missing or corrupt store is
    treated as empty and never raises — a broken file must not break commands.
    """
    p = _seen_path()
    try:
        with p.open("r", encoding="utf-8") as f:
            raw: Any = json.load(f)
    except (OSError, ValueError):
        return set()
    if not isinstance(raw, dict):
        return set()
    seen = cast("dict[str, Any]", raw).get("seen")
    if not isinstance(seen, list):
        return set()
    return {s for s in cast("list[Any]", seen) if isinstance(s, str)}


def mark_seen(hint_id: str) -> None:
    """Persist `hint_id` as shown, preserving any already-recorded ids.

    Atomic tmp+rename (mirrors ``session._atomic_write_json``, replicated to keep
    this leaf module free of a cross-module private import). No advisory lock: a
    lost race merely re-shows a hint once — harmless, unlike session state. Any
    write failure (unwritable ``CRM_HOME``, fsync/rename error) is swallowed: a
    hint is optional UX and must never turn a successful command into a crash —
    at worst the hint re-shows next time because it wasn't recorded.
    """
    seen = load_seen()
    seen.add(hint_id)
    path = _seen_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump({"seen": sorted(seen)}, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    except OSError:
        pass


def take_hint(hint_id: str) -> str | None:
    """Return the hint text the first time `hint_id` is due, else None.

    Returns None (touching no store) when hints are disabled or the id is unknown;
    otherwise, on the first call for an unseen id, records it as seen and returns
    its text. Subsequent calls return None. The caller is responsible for the
    human/JSON/TTY gate — this must only be reached on a human-rendering path.
    """
    if hints_disabled() or hint_id not in HINTS:
        return None
    if hint_id in load_seen():
        return None
    mark_seen(hint_id)
    return HINTS[hint_id]
