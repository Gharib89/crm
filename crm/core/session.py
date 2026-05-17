"""On-disk session + connection-profile persistence.

Layout under `~/.crm/`:

    profiles/<name>.json   — ConnectionProfile dicts (no passwords)
    sessions/<name>.json   — last-used profile + context (current entity, last query)
    history                — prompt_toolkit REPL history file

Passwords are never persisted. They come from env (`D365_PASSWORD`) or `--password`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from crm.utils.d365_backend import ConnectionProfile


DEFAULT_HOME = Path.home() / ".crm"


def _state_root() -> Path:
    root = Path(os.environ.get("CLI_ANYTHING_D365_HOME", str(DEFAULT_HOME))).expanduser()
    (root / "profiles").mkdir(parents=True, exist_ok=True)
    (root / "sessions").mkdir(parents=True, exist_ok=True)
    return root


# ── Profile persistence ─────────────────────────────────────────────────


def profile_path(name: str) -> Path:
    return _state_root() / "profiles" / f"{name}.json"


def save_profile(profile: ConnectionProfile) -> Path:
    p = profile_path(profile.name)
    _atomic_write_json(p, profile.to_dict())
    return p


def load_profile(name: str) -> ConnectionProfile:
    p = profile_path(name)
    if not p.is_file():
        raise FileNotFoundError(f"Profile not found: {name} (looked at {p})")
    with p.open("r", encoding="utf-8") as f:
        return ConnectionProfile.from_dict(json.load(f))


def list_profiles() -> list[str]:
    root = _state_root() / "profiles"
    return sorted(p.stem for p in root.glob("*.json"))


def delete_profile(name: str) -> bool:
    p = profile_path(name)
    if p.is_file():
        p.unlink()
        return True
    return False


# ── Session persistence ─────────────────────────────────────────────────


def session_path(name: str = "default") -> Path:
    return _state_root() / "sessions" / f"{name}.json"


def load_session(name: str = "default") -> dict:
    p = session_path(name)
    if not p.is_file():
        return {
            "name": name,
            "active_profile": None,
            "current_entity_set": None,
            "last_query": None,
            "history": [],
        }
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_session(state: dict, name: str = "default") -> Path:
    state.setdefault("name", name)
    p = session_path(name)
    _atomic_write_json(p, state)
    return p


def append_history(state: dict, command: str, max_len: int = 500) -> None:
    history = state.setdefault("history", [])
    history.append(command)
    if len(history) > max_len:
        del history[: len(history) - max_len]


# ── Locked atomic write ─────────────────────────────────────────────────


def _atomic_write_json(path: Path, payload: Any) -> None:
    """Write JSON atomically via tmp + rename. Uses an exclusive lock during write.

    See guides/session-locking.md for the wider pattern.
    """
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except (OSError, AttributeError):
            pass
        json.dump(payload, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


# ── History file (REPL line history) ────────────────────────────────────


def history_file_path() -> str:
    return str(_state_root() / "history")
