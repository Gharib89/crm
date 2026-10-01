"""On-disk session + connection-profile persistence.

Layout under `~/.crm/`:

    profiles/<name>.json   — ConnectionProfile dicts (+ optional opt-in `_secret`)
    sessions/<name>.json   — last-used profile + context (current entity, last query)
    history                — prompt_toolkit REPL history file

Secrets are saved by default (see save_profile_secret_plaintext / the OS keyring);
the resolution order at use time is `--password` (per-run) > plaintext `_secret` >
OS keyring > TTY prompt. There is no env-var fallback.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from crm.core.state_home import atomic_write_json, state_home

if TYPE_CHECKING:
    from crm.utils.d365_backend import ConnectionProfile


def _atomic_write_json(path: Path, payload: Any, *, mode: int | None = None) -> None:
    # Locked: concurrent crm processes (agent fleets) share profiles and sessions.
    atomic_write_json(path, payload, mode=mode, lock=True)


# ── Profile persistence ─────────────────────────────────────────────────


def profile_path(name: str) -> Path:
    from crm.utils.d365_backend import validate_profile_name

    validate_profile_name(name)
    return state_home() / "profiles" / f"{name}.json"


def save_profile(profile: ConnectionProfile) -> Path:
    # Preserve any existing opt-in plaintext _secret across unrelated re-saves
    # (e.g. solution autowire).  The connect flow explicitly calls
    # clear_profile_secret() when the user opts out, so omitting _secret here
    # would silently wipe it on every unrelated profile mutation.
    payload = profile.to_dict()
    existing_secret = load_profile_secret(profile.name)
    if existing_secret is not None:
        payload["_secret"] = existing_secret
    p = profile_path(profile.name)
    # When a plaintext secret is present, create the file 0600 from the first byte
    # so an unrelated re-save can't widen its permissions — even momentarily.
    _atomic_write_json(p, payload, mode=0o600 if existing_secret is not None else None)
    return p


def load_profile(name: str) -> ConnectionProfile:
    from crm.utils.d365_backend import ConnectionProfile

    p = profile_path(name)
    if not p.is_file():
        raise FileNotFoundError(f"Profile not found: {name} (looked at {p})")
    with p.open("r", encoding="utf-8") as f:
        return ConnectionProfile.from_dict(json.load(f))


def list_profiles() -> list[str]:
    """Saved profile names, read-only — no directory is created as a side
    effect. On the shell-completion hot path (a fresh `crm` process per Tab
    keystroke), a read must never mutate the filesystem.
    """
    return sorted(p.stem for p in (state_home() / "profiles").glob("*.json"))


def delete_profile(name: str) -> bool:
    p = profile_path(name)
    if p.is_file():
        p.unlink()
        return True
    return False


def rename_profile(old: str, new: str) -> None:
    """Move profile *old* → *new*: rewrite the file under *new* with its internal
    ``name`` set to *new*, carrying any inline plaintext ``_secret``, then delete
    *old*. Callers validate name / existence / no-clobber before calling.

    ``profile_path(new)`` re-validates *new* as a safe path component (raising
    ``D365Error`` on a bad name). The keyring entry, active-session pointer, and
    cache dir are the caller's concern — this touches only the profile file.
    """
    data = _read_profile_raw(old)  # FileNotFoundError if old is missing
    data["name"] = new
    dest = profile_path(new)
    # Carry the 0600 mode onto the renamed file at creation when it holds a secret.
    _atomic_write_json(dest, data, mode=0o600 if "_secret" in data else None)
    delete_profile(old)


# ── Plaintext profile secret (issue #130, explicit opt-in only) ─────────
#
# Stored as a `_secret` key in the SAME profile JSON file, written/read here
# directly — never via ConnectionProfile.to_dict()/from_dict() — so the
# dataclass (and every status/list view built from it) stays secret-free.


def _read_profile_raw(name: str) -> dict[str, Any]:
    p = profile_path(name)
    if not p.is_file():
        raise FileNotFoundError(f"Profile not found: {name} (looked at {p})")
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_profile_secret_plaintext(name: str, secret: str) -> Path:
    """Merge a plaintext `_secret` into the profile JSON; 0600 on POSIX.

    Windows cannot enforce file-mode perms via chmod — the caller emits the
    warning that steers Windows users to --store-password (Credential Manager).
    """
    data = _read_profile_raw(name)
    data["_secret"] = secret
    p = profile_path(name)
    _atomic_write_json(p, data, mode=0o600)  # created 0600 — never a widen window
    return p


def load_profile_secret(name: str) -> str | None:
    """Return the plaintext `_secret` from the profile file, or None."""
    try:
        return _read_profile_raw(name).get("_secret")
    except FileNotFoundError:
        return None


def clear_profile_secret(name: str) -> bool:
    """Strip `_secret` from the profile file. True iff one was present."""
    try:
        data = _read_profile_raw(name)
    except FileNotFoundError:
        return False
    if "_secret" not in data:
        return False
    del data["_secret"]
    _atomic_write_json(profile_path(name), data)
    return True


# ── Session persistence ─────────────────────────────────────────────────


def session_path(name: str = "default") -> Path:
    from crm.utils.d365_backend import validate_profile_name

    validate_profile_name(name)
    return state_home() / "sessions" / f"{name}.json"


def load_session(name: str = "default") -> dict[str, Any]:
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


def save_session(state: dict[str, Any], name: str = "default") -> Path:
    state.setdefault("name", name)
    p = session_path(name)
    _atomic_write_json(p, state)
    return p


def append_history(state: dict[str, Any], command: str, max_len: int = 500) -> None:
    history = state.setdefault("history", [])
    history.append(command)
    if len(history) > max_len:
        del history[: len(history) - max_len]


# ── History file (REPL line history) ────────────────────────────────────


def history_file_path() -> str:
    # prompt_toolkit appends to this file without creating its directory, so
    # the REPL (the only caller, and a writer) needs the state home to exist.
    home = state_home()
    home.mkdir(parents=True, exist_ok=True)
    return str(home / "history")
