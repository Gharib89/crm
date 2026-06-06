"""Append-only JSONL audit journal for agent-driven mutations.

Layout under ``<CRM_HOME>/audit/``::

    <session>.jsonl   — one JSON object per line, one line per mutation

Each line records metadata about a command that was executed: timestamp,
profile, command, target, solution, flags, and the derived ``result_id``.
The request payload is never persisted — only the fields above.

The journal is append-only: each :func:`record` call opens the file in ``"a"``
mode, writes one line, flushes, and fsyncs. Failures are swallowed so a broken
journal directory never disrupts the caller.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$"
)

# Anything outside this set is replaced with '_' so a user-controlled --session
# value (path separators, `..`) can never escape the audit directory.
_UNSAFE_SESSION_RE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_session(session: str) -> str:
    """Confine *session* to a single filename within the audit directory.

    Replaces path separators and any other unsafe character with ``_``, and
    maps an all-dots name (``.``/``..``) — which could otherwise traverse — to
    ``default``. Ordinary names (``default``, ``my-session``) pass through
    unchanged.
    """
    name = _UNSAFE_SESSION_RE.sub("_", session)
    if set(name) <= {"."}:
        name = "default"
    return name


def _audit_root() -> Path:
    root = Path(os.environ.get("CRM_HOME", str(Path.home() / ".crm"))).expanduser()
    audit = root / "audit"
    audit.mkdir(parents=True, exist_ok=True)
    return audit


def _journal_path(session: str) -> Path:
    return _audit_root() / f"{_safe_session(session)}.jsonl"


def _extract_result_id(result: Any) -> str | None:
    """Defensively derive a result ID from the API response.

    Returns a string when *result* is a dict containing an ``"id"`` key with a
    truthy value, or when it contains a key whose lowercased form equals
    ``"id"`` or ends with ``"id"`` and whose value looks GUID-ish.
    Returns ``None`` for everything else.
    """
    if not isinstance(result, dict):
        return None

    d: dict[str, Any] = cast("dict[str, Any]", result)

    # Fast path: explicit "id" key with a truthy value
    if "id" in d and d["id"]:
        return str(d["id"])

    # Fallback: scan for a key ending with "id" carrying a GUID-ish value
    for key, value in d.items():
        key_lower = str(key).lower()
        if key_lower == "id" or key_lower.endswith("id"):
            candidate = str(value)
            if _GUID_RE.match(candidate):
                return candidate

    return None


def record(
    *,
    session: str,
    profile: str | None,
    command: str,
    target: str | None,
    result: Any,
    solution: str | None = None,
    staged: bool = False,
    dry_run: bool = False,
    ok: bool = True,
    now: datetime | None = None,
) -> None:
    """Append one audit line to the session journal.

    The *result* argument is used only to derive ``result_id``; it is not
    stored. Failures (path resolution, directory creation, file write) are
    silently swallowed so the caller is never disrupted by a journal error.
    """
    ts = (now or datetime.now(timezone.utc)).isoformat()
    result_id = _extract_result_id(result)
    line: dict[str, Any] = {
        "ts": ts,
        "profile": profile,
        "command": command,
        "target": target,
        "solution": solution,
        "staged": staged,
        "dry_run": dry_run,
        "ok": ok,
        "result_id": result_id,
    }
    try:
        path = _journal_path(session)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass


def read(session: str, *, tail: int | None = None) -> list[dict[str, Any]]:
    """Return all journal rows for *session*, or the last *tail* rows.

    A missing journal file returns ``[]``. Lines that fail to parse are
    silently skipped.
    """
    try:
        path = _journal_path(session)
        if not path.is_file():
            return []
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    rows: list[dict[str, Any]] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        try:
            parsed: Any = json.loads(raw)
            if isinstance(parsed, dict):
                rows.append(cast("dict[str, Any]", parsed))
        except (json.JSONDecodeError, ValueError):
            pass

    if tail is not None:
        # `rows[-0:]` is `rows[:]` (all rows), so a non-positive tail must be
        # handled explicitly — "last N" of N<=0 is no rows.
        rows = rows[-tail:] if tail > 0 else []
    return rows
