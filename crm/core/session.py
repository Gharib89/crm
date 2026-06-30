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
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from crm.utils.d365_backend import ConnectionProfile


DEFAULT_HOME = Path.home() / ".crm"


def _state_root() -> Path:
    root = Path(os.environ.get("CRM_HOME", str(DEFAULT_HOME))).expanduser()
    (root / "profiles").mkdir(parents=True, exist_ok=True)
    (root / "sessions").mkdir(parents=True, exist_ok=True)
    return root


# ── Profile persistence ─────────────────────────────────────────────────


def profile_path(name: str) -> Path:
    from crm.utils.d365_backend import validate_profile_name
    validate_profile_name(name)
    return _state_root() / "profiles" / f"{name}.json"


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
    _atomic_write_json(p, payload)
    # _atomic_write_json writes a fresh tmp file (default 0644) and renames over
    # the original, dropping any prior 0600. Re-enforce it when a plaintext
    # secret is present so an unrelated re-save can't widen its permissions.
    if existing_secret is not None and os.name == "posix":
        os.chmod(p, 0o600)
    return p


def load_profile(name: str) -> ConnectionProfile:
    from crm.utils.d365_backend import ConnectionProfile
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
    _atomic_write_json(p, data)
    if os.name == "posix":
        os.chmod(p, 0o600)
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
    return _state_root() / "sessions" / f"{name}.json"


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


# ── Locked atomic write ─────────────────────────────────────────────────


def _atomic_write_json(path: Path, payload: Any) -> None:
    """Write JSON atomically via tmp + rename. Uses an exclusive lock during write.

    See guides/session-locking.md for the wider pattern.
    """
    try:
        import fcntl
    except ImportError:
        fcntl = None  # Windows: no flock, rely on atomic rename only

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        if fcntl is not None:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
            except (OSError, AttributeError):
                pass
        json.dump(payload, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


# ── Profile rename ──────────────────────────────────────────────────────


def rename_profile(
    old_name: str,
    new_name: str,
    session_name: str = "default",
) -> bool:
    """Rename a profile on disk and repoint the active-session pointer.

    Moves ``profiles/OLD.json`` → ``profiles/NEW.json``, rewrites the internal
    ``name`` field, and carries the inline ``_secret`` along unchanged.  Updates
    the active-profile pointer in *session_name* if it currently equals *old_name*.

    Returns True iff the active-session pointer was updated.

    Raises:
        FileNotFoundError — OLD does not exist.
        FileExistsError   — NEW already exists (refuse to clobber).
        D365Error         — NEW fails ``validate_profile_name``.
    """
    from crm.utils.d365_backend import validate_profile_name

    validate_profile_name(new_name)  # raises D365Error on bad name

    old_path = profile_path(old_name)
    new_path = profile_path(new_name)

    if not old_path.is_file():
        raise FileNotFoundError(f"Profile {old_name!r} not found (looked at {old_path})")
    if new_path.exists():
        raise FileExistsError(f"Profile {new_name!r} already exists; refusing to clobber")

    # Read the raw payload (preserves _secret and any future keys).
    with old_path.open("r", encoding="utf-8") as fh:
        payload: dict[str, Any] = json.load(fh)

    payload["name"] = new_name

    _atomic_write_json(new_path, payload)
    # Mirror save_profile: re-enforce 0600 when a plaintext secret rides along.
    if payload.get("_secret") is not None and os.name == "posix":
        os.chmod(new_path, 0o600)
    old_path.unlink()

    # Repoint the active-profile pointer when it currently names OLD.
    state = load_session(session_name)
    pointer_updated = state.get("active_profile") == old_name
    if pointer_updated:
        state["active_profile"] = new_name
        save_session(state, session_name)

    return pointer_updated


# ── History file (REPL line history) ────────────────────────────────────


def history_file_path() -> str:
    return str(_state_root() / "history")
