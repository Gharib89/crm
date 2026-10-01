"""The state home: where crm keeps all local state, and how state files are written.

Every piece of crm state (profiles, sessions, the audit journal, caches, hints,
registries, the token cache, REPL history) resolves its location through
:func:`state_home`. ``CRM_HOME`` relocates it; unset or empty, it is ``~/.crm``.
Resolving creates nothing: each writer creates its own directory on write, so a
read (shell completion, ``profile list``) never mutates the filesystem.

Stdlib-only and import-free within crm, so any layer (the HTTP backend included)
can import it without a cycle.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any


def state_home() -> Path:
    """The state home directory. Unset or empty ``CRM_HOME`` means ``~/.crm``."""
    return Path(os.environ.get("CRM_HOME") or "~/.crm").expanduser()


# A live writer holds its temp file for milliseconds; an hour is a wide margin
# past that, so anything older is an orphan from a crashed write, never a temp
# in flight.
_TEMP_REAP_AGE_SECONDS = 3600

# Matches exactly the temp names this module creates —
# `.{os.getpid()}.{os.urandom(6).hex()}.tmp`, i.e. digits then 12 hex chars — so
# the reap can never touch an unrelated dot-prefixed `.tmp` file that happens to
# sit in the same directory. Keep the 12 in lockstep with the urandom(6) below.
_TEMP_NAME_RE = re.compile(r"\.\d+\.[0-9a-f]{12}\.tmp")


def _reap_stale_temps(parent: Path) -> None:
    """Unlink orphaned atomic-write temp files in *parent* older than the reap
    threshold. A hard kill (SIGKILL, power loss) between ``os.open`` and
    ``os.replace`` leaves a unique ``.<pid>.<hex>.tmp`` that nothing else reaps;
    without this they accumulate unbounded on agent fleets.

    The age threshold is what makes this safe against a live writer: an in-flight
    temp is milliseconds old, far below the hour cutoff, so it is never a reap
    candidate. The optional directory lock is best-effort (absent on platforms
    without ``fcntl``, and its acquisition failures are swallowed), so the
    threshold — not the lock — is the guarantee. Best-effort throughout: any
    failure is swallowed.
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


def atomic_write(path: Path, data: bytes, *, mode: int | None = None, lock: bool = False) -> None:
    """Write *data* to *path* atomically, creating the parent directory.

    Writes a per-process-unique temp file in the target directory, fsyncs it, then
    renames it over *path*, so a reader sees the old file or the new one, never a
    partial one; a failed write unlinks the temp and leaves the old file intact.

    *mode* is the permission the temp file is *created* with (``O_CREAT|O_EXCL``):
    pass ``0o600`` for secret-bearing files so they are never group/world-readable
    for any instant. Omitted, it is ``0o644`` (masked by the umask).

    *lock* serializes the whole write-replace under an exclusive lock on the parent
    directory, so concurrent crm processes can't interleave writes to shared state
    (POSIX only; elsewhere the atomic rename alone applies). Errors propagate:
    callers that are best-effort catch them.
    """
    try:
        import fcntl
    except ImportError:
        fcntl = None  # Windows: no flock, rely on the atomic rename alone

    path.parent.mkdir(parents=True, exist_ok=True)

    lock_fd = os.open(path.parent, os.O_RDONLY) if lock and fcntl is not None else None
    try:
        if lock_fd is not None and fcntl is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
            except (OSError, AttributeError):
                pass

        # Sweep temp files orphaned by crashed writes (#743); the >1h age threshold
        # keeps it safe from live writers with or without the lock.
        _reap_stale_temps(path.parent)

        # The temp name is a short fixed shape independent of path.name, so a very
        # long (but valid) target name can't push the temp past NAME_MAX. O_EXCL +
        # retry guards the (astronomically rare) clash.
        create_mode = 0o644 if mode is None else mode
        while True:
            tmp = path.with_name(f".{os.getpid()}.{os.urandom(6).hex()}.tmp")
            try:
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, create_mode)
                break
            except FileExistsError:
                continue
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
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


def atomic_write_json(
    path: Path, payload: Any, *, mode: int | None = None, lock: bool = False
) -> None:
    """:func:`atomic_write` of *payload* as indented, key-sorted JSON.

    Serializes before touching the disk, so an unserializable payload leaves no
    trace.
    """
    data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    atomic_write(path, data, mode=mode, lock=lock)


def read_json(path: Path) -> Any | None:
    """The parsed JSON at *path*, or ``None`` when it is missing or corrupt.

    Tolerant only of a missing file and of bad content (JSON or Unicode decode
    errors). ``PermissionError`` and other I/O faults propagate, so a fault never
    reads as "empty" and gets overwritten by the next write.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError:  # JSONDecodeError and UnicodeDecodeError both subclass it
        return None
