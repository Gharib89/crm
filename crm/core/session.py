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
import re
import time
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
    effect (unlike `_state_root()`). On the shell-completion hot path (a fresh
    `crm` process per Tab keystroke), a read must never mutate the filesystem.
    """
    root = Path(os.environ.get("CRM_HOME", str(DEFAULT_HOME))).expanduser() / "profiles"
    return sorted(p.stem for p in root.glob("*.json"))


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


# A live writer holds its temp file for milliseconds; an hour is a wide margin
# past that, so anything older is an orphan from a crashed write, never a temp
# in flight.
_TEMP_REAP_AGE_SECONDS = 3600

# Matches exactly the temp names this module creates —
# `.{os.getpid()}.{os.urandom(6).hex()}.tmp`, i.e. digits then 12 hex chars — so
# the reap can never touch an unrelated dot-prefixed `.tmp` file that happens to
# sit in the same directory. Keep the 12 in lockstep with the urandom(6) above.
_TEMP_NAME_RE = re.compile(r"\.\d+\.[0-9a-f]{12}\.tmp")


def _reap_stale_temps(parent: Path) -> None:
    """Unlink orphaned atomic-write temp files in *parent* older than the reap
    threshold. A hard kill (SIGKILL, power loss) between ``os.open`` and
    ``os.replace`` leaves a unique ``.<pid>.<hex>.tmp`` that nothing else reaps;
    without this they accumulate unbounded on agent fleets.

    The age threshold is what makes this safe against a live writer: an in-flight
    temp is milliseconds old, far below the hour cutoff, so it is never a reap
    candidate. Callers *should* additionally run this under the parent-directory
    lock, but that lock is best-effort (absent on platforms without ``fcntl``,
    and its acquisition failures are swallowed), so the threshold — not the lock —
    is the guarantee. Best-effort throughout: any failure is swallowed, matching
    the module's stance.
    """
    cutoff = time.time() - _TEMP_REAP_AGE_SECONDS
    try:
        entries = list(parent.glob(".*.tmp"))
    except OSError:
        return
    for entry in entries:
        if not _TEMP_NAME_RE.fullmatch(entry.name):
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            pass


def _atomic_write_json(path: Path, payload: Any, *, mode: int | None = None) -> None:
    """Write JSON atomically: write a per-process-unique temp file, then rename it
    over *path*. The whole write-and-replace is serialized by an exclusive lock on
    the parent directory, so concurrent crm processes (agent fleets run many at
    once) can't interleave and corrupt shared state.

    *mode*, when given, is the permission the temp file is *created* with (via
    ``os.open`` ``O_CREAT|O_EXCL``) — pass ``0o600`` for secret-bearing files so the
    secret is never group/world-readable for any instant (no create-then-chmod
    widen window). When omitted, the temp file takes the umask default, matching
    prior behavior for non-secret session/profile writes.
    """
    try:
        import fcntl
    except ImportError:
        fcntl = None  # Windows: no flock, rely on the atomic rename alone

    path.parent.mkdir(parents=True, exist_ok=True)

    # Lock the parent directory (a stable target that always exists) for the whole
    # write-replace, instead of the old too-late lock on the temp fd — which, with
    # a shared temp name, was defeated by a concurrent open() truncation before the
    # lock was ever taken.
    lock_fd = os.open(path.parent, os.O_RDONLY) if fcntl is not None else None
    try:
        if lock_fd is not None and fcntl is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
            except (OSError, AttributeError):
                pass

        # Sweep temp files orphaned by crashed writes (#743). Runs under the
        # directory lock when it was acquired above (best-effort); the >1h age
        # threshold keeps it safe from live writers regardless.
        _reap_stale_temps(path.parent)

        # Unique temp name in the target dir: two concurrent writers get distinct
        # temp files instead of clobbering one shared "<name>.tmp". The name is a
        # short fixed shape independent of path.name, so a very long (but valid,
        # uncapped) profile name can't push the temp past NAME_MAX while the target
        # itself still fits. O_EXCL + retry guards the (astronomically rare) clash.
        create_mode = 0o666 if mode is None else mode
        while True:
            tmp = path.with_name(f".{os.getpid()}.{os.urandom(6).hex()}.tmp")
            try:
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, create_mode)
                break
            except FileExistsError:
                continue
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)  # don't leak a unique temp file on a failed write
            except OSError:
                pass
            raise
    finally:
        if lock_fd is not None:
            os.close(lock_fd)


# ── History file (REPL line history) ────────────────────────────────────


def history_file_path() -> str:
    return str(_state_root() / "history")
