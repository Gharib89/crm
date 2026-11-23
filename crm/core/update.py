"""Self-update + passive update-notice logic for the crm CLI.

Frozen (PyInstaller) installs can learn the latest published version with one
cheap GET against the R2 release layout and swap the bundle in place. The
passive notice is cache-only at command exit; a guarded background thread does
the network refresh, so a command is never slowed and machine-readable output
is never polluted.

`requests` is imported inside the network functions, so importing this module
(as `crm --help` does to render the self-update help) never loads the transport
stack (#247). See ADR 0029.

A frozen bundle cannot replace itself on Windows. PyInstaller's C bootloader
opens `_internal/base_library.zip` before any Python runs and holds that
descriptor, without delete-sharing, for the process's whole life: renaming it is
refused (`WinError 32`), writing over it is refused, and renaming the directory
that contains it is refused (`WinError 5`). No retry helps, as the handle is
released only when the process exits (#937). So Windows stages the payload,
hands off to a detached copy of the new binary and exits; the finisher waits for
the parent to go, performs the swap, and records the outcome for the next `crm`
run to report. Posix can replace an open file outright, so it swaps in-process.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import threading
import time
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import IO, Any, NamedTuple, cast

from crm import __version__
from crm.core.state_home import atomic_write_json, read_json, state_home


class UpdateError(Exception):
    """A self-update could not be completed; the existing install is untouched.

    `install_intact` is False for the one exception to that promise: a Windows
    swap whose rollback also failed, leaving the install split across two trees.
    The failure notice keys its "your install is unchanged" reassurance on it.
    """

    install_intact: bool = True


_OPT_OUT_ENV = "CRM_NO_UPDATE_CHECK"

_DEFAULT_BASE_URL = "https://pub-bbeb86c46454443ca76521dd4d29818e.r2.dev"
"""The Cloudflare R2 public base.

`scripts/install.sh`, `scripts/install.ps1`, README.md and docs/getting-started/
carry the same URL, so moving the bucket changes every copy (`rg r2.dev` lists them).
"""

_NETWORK_TIMEOUT = 1.0
"""Hard ceiling for the passive check.

On-prem and government networks may have no outbound internet or a hostile
proxy, and the background check must never slow a command.
"""

_INTERACTIVE_TIMEOUT = 10.0
"""Timeout for an explicit `self-update`, so a slow but working proxy is not read as unreachable."""


def default_base_url() -> str:
    return os.environ.get("CRM_INSTALL_BASE_URL", _DEFAULT_BASE_URL)


def fetch_latest_version(base_url: str, timeout: float = _NETWORK_TIMEOUT) -> str | None:
    """GET ``<base_url>/latest/VERSION`` and return the trimmed `vX.Y.Z`.

    Returns None on any failure (timeout, connection error, non-2xx, empty body):
    the caller treats "unknown latest" as "no notice", never as an error. A 200
    whose body is not a version (a proxy login page, an HTML error) is None too,
    since caching it would make `compare_versions` raise downstream.
    """
    import requests

    try:
        resp = requests.get(f"{base_url}/latest/VERSION", timeout=timeout)
        resp.raise_for_status()
    except Exception:
        return None
    text = resp.text.strip()
    if not text or not _is_version(text):
        return None
    return text


_CHECK_INTERVAL = 86400.0
"""At most one remote check per this window; the result is cached in CRM_HOME."""


def _cache_path() -> Path:
    return state_home() / "update-check.json"


def read_cache() -> dict[str, Any] | None:
    """Last-known {checked_at, latest}, or None if absent/unreadable."""
    try:
        return read_json(_cache_path())
    except OSError:
        return None


def _write_cache_dict(data: dict[str, Any]) -> None:
    atomic_write_json(_cache_path(), data)


def write_cache(latest: str, now: float) -> None:
    """Atomically record the latest-version probe result (tmp + replace).

    Preserves an existing `notified_at` gate only when `latest` is unchanged —
    a newly discovered version drops the gate so the notice fires again.
    """
    data: dict[str, Any] = {"checked_at": now, "latest": latest}
    existing = read_cache() or {}
    if existing.get("latest") == latest and isinstance(existing.get("notified_at"), (int, float)):
        data["notified_at"] = existing["notified_at"]
    _write_cache_dict(data)


def mark_notified(now: float) -> None:
    """Record that the passive notice was shown, to gate it to once per interval."""
    data = read_cache() or {}
    data["notified_at"] = now
    _write_cache_dict(data)


def should_refresh(now: float) -> bool:
    """True when the cache is missing, unreadable, or older than the interval."""
    cache = read_cache()
    if cache is None:
        return True
    checked_at = cache.get("checked_at")
    if not isinstance(checked_at, (int, float)):
        return True
    return (now - checked_at) >= _CHECK_INTERVAL


_LINUX_ARCHIVE = "crm-linux-x86_64.tar.gz"
_WINDOWS_ARCHIVE = "crm-windows-x86_64.zip"


def platform_archive() -> str:
    """The release archive name for the current platform."""
    return _WINDOWS_ARCHIVE if sys.platform.startswith("win") else _LINUX_ARCHIVE


def parse_sha256sums(body: str) -> dict[str, str]:
    """Parse a ``<hash>  <filename>`` SHA256SUMS body (CRLF-tolerant)."""
    sums: dict[str, str] = {}
    for line in body.splitlines():
        parts = line.strip().split()
        if len(parts) == 2:
            sums[parts[1]] = parts[0]
    return sums


def verify_sha256(data: bytes, expected: str) -> bool:
    """True if `data`'s SHA-256 matches `expected` (case-insensitive hex)."""
    return hashlib.sha256(data).hexdigest() == expected.strip().lower()


def _parse(version: str) -> tuple[int, ...]:
    """`vX.Y.Z` / `X.Y.Z` -> int tuple. A leading `v` is tolerated on either side.

    Raises ValueError on a non-numeric component — callers that handle untrusted
    input (cache, server payload) must guard or pre-validate via `_is_version`.
    """
    core = version.strip().lstrip("vV").split("+", 1)[0].split("-", 1)[0]
    return tuple(int(part) for part in core.split("."))


def _is_version(text: str) -> bool:
    """True if `text` parses as a dotted numeric version (so compare won't raise)."""
    try:
        return bool(_parse(text))
    except ValueError:
        return False


def compare_versions(current: str, latest: str) -> int:
    """Return -1 / 0 / 1 if `current` is older / equal / newer than `latest`."""
    a, b = _parse(current), _parse(latest)
    return (a > b) - (a < b)


def is_frozen() -> bool:
    """True when running as a PyInstaller bundle (mirrors keyring_store.py)."""
    return bool(getattr(sys, "frozen", False))


def current_version() -> str:
    return __version__


def install_dir() -> Path:
    """The frozen bundle's install directory (the dir holding the `crm` launcher)."""
    return Path(sys.executable).resolve().parent


_GITHUB_REPO = "https://github.com/Gharib89/crm"
"""The git source of every non-frozen upgrade command, since crm is not on PyPI."""

INSTALL_METHODS = ("frozen", "uv-tool", "pipx", "editable", "pip-git", "unknown")
"""The install-method vocabulary, surfaced as `data.install_method` in `--json` (CLI contract)."""

_AUTO_RUN_METHODS = ("uv-tool", "pipx")
"""The methods `self-update` may auto-run: isolated, force-reinstallable envs only."""


def _path_contains(path: Path, *segments: str) -> bool:
    """True if `segments` appear as consecutive components anywhere in `path`
    (case-insensitive, so it works for both posix and Windows layouts).
    """
    parts = [p.lower() for p in path.parts]
    seg = [s.lower() for s in segments]
    return any(parts[i : i + len(seg)] == seg for i in range(len(parts) - len(seg) + 1))


def _read_direct_url() -> dict[str, Any] | None:
    """The `crm` dist's PEP 610 ``direct_url.json`` (editable/vcs marker), or None.

    Best-effort: any failure (package not found via importlib.metadata, missing
    file, malformed JSON) degrades to None so detection falls back to `unknown`.
    """
    try:
        from importlib import metadata

        text = metadata.distribution("crm").read_text("direct_url.json")
    except Exception:
        return None
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else None


def detect_install_method() -> str:
    """Best-effort detection of how `crm` was installed (one of INSTALL_METHODS).

    Path/marker sniffing only — no shelling out, no install-time stamp. An
    unknown or ambiguous signal degrades to ``"unknown"`` (printed guidance,
    never a wrong auto-action). `is_frozen()` stays the authoritative frozen
    signal; the rest keys off `sys.prefix` layout and PEP 610 metadata. Only a
    git VCS install is `pip-git`, since the constructed upgrade command is a
    `git+` URL; any other VCS is `unknown`.
    """
    if is_frozen():
        return "frozen"
    prefix = Path(sys.prefix)
    if (prefix / "uv-receipt.toml").exists() or _path_contains(prefix, "uv", "tools"):
        return "uv-tool"
    if (prefix / "pipx_metadata.json").exists() or _path_contains(prefix, "pipx", "venvs"):
        return "pipx"
    direct = _read_direct_url()
    if direct is not None:
        dir_info = direct.get("dir_info")
        if isinstance(dir_info, dict) and cast("dict[str, Any]", dir_info).get("editable"):
            return "editable"
        vcs_info = direct.get("vcs_info")
        if isinstance(vcs_info, dict) and cast("dict[str, Any]", vcs_info).get("vcs") == "git":
            return "pip-git"
    return "unknown"


def is_auto_run_method(method: str) -> bool:
    """True for methods `self-update` may auto-run (uv-tool / pipx)."""
    return method in _AUTO_RUN_METHODS


def upgrade_command(method: str, latest: str) -> str:
    """The exact upgrade command string shown to the user / emitted as ``data.command``.

    All non-frozen methods converge on the latest release tag ``@vX.Y.Z`` — the
    same value the frozen path reads from R2 ``latest/VERSION`` — so "latest"
    means one thing everywhere. `editable` gets checkout guidance; `pip-git` and
    `unknown` get the git-based pip upgrade (the most general correct command).

    uv gets `install --force`, not `uv tool upgrade`: uv pins the git commit SHA,
    so a plain upgrade can silently no-op. `--python` pins the running X.Y, since
    `--force` otherwise rebuilds the env on uv's default interpreter, which may
    lack prebuilt wheels for a dependency (#1038); it names the version, not
    `sys.executable`, because `--force` deletes the env that path lives in.
    """
    tag = f"v{latest.lstrip('vV')}"
    spec = f"git+{_GITHUB_REPO}@{tag}"
    if method == "uv-tool":
        python = f"{sys.version_info.major}.{sys.version_info.minor}"
        return f"uv tool install --force --python {python} {spec}"
    if method == "pipx":
        return f"pipx install --force {spec}"
    if method == "editable":
        return "git pull && pip install -e ."
    return f"pip install -U {spec}"


def upgrade_argv(method: str, latest: str) -> list[str] | None:
    """The argv for an auto-runnable upgrade (uv-tool / pipx), or None otherwise.

    Kept in lockstep with `upgrade_command` by splitting its output, so the argv
    handed to `run_upgrade` and the string shown to the user never drift.
    """
    if not is_auto_run_method(method):
        return None
    import shlex

    return shlex.split(upgrade_command(method, latest))


def run_upgrade(argv: list[str]) -> int:
    """Run the upgrade subprocess and return its exit status (injectable seam).

    Raises ``FileNotFoundError`` when the tool binary (uv/pipx) is not on PATH —
    the command layer catches it and falls back to printing the command.
    """
    import subprocess

    return subprocess.run(argv).returncode


def is_check_enabled(*, json_mode: bool, stderr_isatty: bool, env: Mapping[str, str]) -> bool:
    """All guards must pass: human TTY only, never under --json / CI / opt-out."""
    if json_mode or not stderr_isatty:
        return False
    if env.get(_OPT_OUT_ENV):
        return False
    if env.get("CI"):
        return False
    return True


def refresh_cache(now: float, base_url: str | None = None) -> None:
    """Background-thread body: probe latest and persist it; silent on failure.

    Runs on a daemon thread, so any error (including an OSError from a read-only
    CRM_HOME) must be swallowed — an unhandled traceback here would break the
    fail-silent / no-noise guarantee of the passive notice.
    """
    try:
        latest = fetch_latest_version(base_url or default_base_url())
        if latest:
            write_cache(latest, now)
    except Exception:
        pass


def pending_notice(current: str, *, now: float | None = None) -> str | None:
    """One-line notice from the cache (no network) if a newer version is known.

    Read-only and fail-silent: a malformed cached `latest` never raises, and the
    notice is gated to once per `_CHECK_INTERVAL` via the cached `notified_at`
    stamp so a new process per command does not reprint on every invocation.

    The upgrade instruction is unified across every install method — the notice
    always points at ``crm self-update``, which owns all method-specific logic
    (frozen swap, uv/pipx reinstall, printed guidance). The notice never restates
    a method-specific command, so it cannot drift from what the command does.
    """
    cache = read_cache()
    if cache is None:
        return None
    latest = cache.get("latest")
    if not isinstance(latest, str):
        return None
    try:
        if compare_versions(current, latest) >= 0:
            return None
    except ValueError:
        return None
    notified_at = cache.get("notified_at")
    if isinstance(notified_at, (int, float)):
        ref = time.time() if now is None else now
        if (ref - notified_at) < _CHECK_INTERVAL:
            return None
    return (
        f"A new crm release is available: {current} → {latest}. Run `crm self-update` to upgrade."
    )


_check_started = False
_notified = False


def run_background_check(
    *, json_mode: bool, stderr_isatty: bool, env: Mapping[str, str], now: float
) -> threading.Thread | None:
    """Spawn a daemon thread to refresh the version cache, if due and enabled.

    Returns the thread (started) or None when skipped. Never blocks the caller:
    the running command finishes regardless of whether the probe completes.
    """
    global _check_started
    if _check_started:
        return None
    if not is_check_enabled(json_mode=json_mode, stderr_isatty=stderr_isatty, env=env):
        return None
    if not should_refresh(now):
        return None
    _check_started = True
    thread = threading.Thread(target=refresh_cache, args=(now,), daemon=True)
    thread.start()
    return thread


def emit_pending_notice(
    *,
    json_mode: bool,
    stderr_isatty: bool,
    env: Mapping[str, str],
    stream: IO[str] | None = None,
    now: float | None = None,
) -> bool:
    """Print the cached update notice, if enabled and due. Returns printed?

    Gated both per-process (`_notified`, for the REPL) and per-interval (a
    persisted `notified_at`, so a fresh process per command shows it at most
    once a day rather than on every invocation). Stamping `notified_at` is
    best-effort and never breaks the command.
    """
    global _notified
    if _notified:
        return False
    if not is_check_enabled(json_mode=json_mode, stderr_isatty=stderr_isatty, env=env):
        return False
    ref = time.time() if now is None else now
    message = pending_notice(current_version(), now=ref)
    if message is None:
        return False
    _notified = True
    print(message, file=stream if stream is not None else sys.stderr)
    try:
        mark_notified(ref)
    except Exception:
        pass
    return True


def check_for_update(base_url: str | None = None) -> dict[str, Any]:
    """Compare the running version to the published latest. Network, no fs change."""
    current = current_version()
    latest = fetch_latest_version(base_url or default_base_url(), _INTERACTIVE_TIMEOUT)
    if latest is None:
        raise UpdateError("Could not determine the latest version (network unreachable).")
    try:
        available = compare_versions(current, latest) < 0
    except ValueError as exc:
        raise UpdateError(f"Unexpected version format from release server: {latest!r}") from exc
    return {
        "current": current,
        "latest": latest,
        "update_available": available,
    }


def _download_archive(base_url: str, version: str, archive: str) -> bytes:
    """Download one release archive, raising UpdateError on any network or HTTP error.

    The connect timeout is bounded so an unreachable network fails fast rather
    than appearing to hang.
    """
    import requests

    url = f"{base_url}/{version}/{archive}"
    try:
        resp = requests.get(url, timeout=(_INTERACTIVE_TIMEOUT, 30))
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise UpdateError(f"Failed to download {url}: {exc}") from exc
    return resp.content


def _fetch_sha256sums(base_url: str, version: str) -> dict[str, str]:
    import requests

    url = f"{base_url}/{version}/SHA256SUMS"
    try:
        resp = requests.get(url, timeout=(_INTERACTIVE_TIMEOUT, _INTERACTIVE_TIMEOUT))
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise UpdateError(f"Failed to fetch checksums from {url}: {exc}") from exc
    return parse_sha256sums(resp.text)


def _is_safe_member(name: str) -> bool:
    """Reject absolute paths and any `..` traversal (zip-slip / tar-slip)."""
    if name.startswith(("/", "\\")) or os.path.isabs(name):
        return False
    parts = name.replace("\\", "/").split("/")
    return ".." not in parts


def _extract(archive: str, data: bytes, dest: Path) -> None:
    """Extract a release archive (tar.gz on posix, zip on Windows) into `dest`.

    Members are validated against path traversal before extraction. The bundle is
    checksum-verified upstream, but this is defense-in-depth against a compromised
    distribution endpoint, layered on top of tarfile's `filter=` guard.

    Link and special members are rejected up front in both formats: a symlink
    such as `lib -> /etc` followed by a `lib/x` member escapes `dest` with no
    `..` at all. A zip marks a symlink with S_IFLNK in the high external-attr
    bits.
    """
    dest.mkdir(parents=True, exist_ok=True)
    if archive.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if not _is_safe_member(info.filename):
                    raise UpdateError(f"Unsafe path in archive (traversal): {info.filename!r}")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise UpdateError(f"Unsafe link member in archive: {info.filename!r}")
            zf.extractall(dest)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for member in tar.getmembers():
                if not _is_safe_member(member.name):
                    raise UpdateError(f"Unsafe path in archive (traversal): {member.name!r}")
                if not (member.isfile() or member.isdir()):
                    raise UpdateError(f"Unsafe non-regular member in archive: {member.name!r}")
            tar.extractall(dest, filter="data")


_LOCK_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.0)
"""The ladder (about 2.5s in all) each swap step waits a transient lock out across (#935).

Windows raises ERROR_SHARING_VIOLATION when another process holds a file open
without delete-sharing. A virus scanner or the search indexer is the usual
holder, and both react to writes inside a program directory, so the swap races
the very scan it provokes; that handle lives only as long as the scan.
"""

_TRANSIENT_LOCK_WINERRORS = (32, 33)
"""ERROR_SHARING_VIOLATION and ERROR_LOCK_VIOLATION.

Matched on `winerror`, not `errno`, because Windows maps ERROR_ACCESS_DENIED to
EACCES as well, and a genuine ACL denial must fail at once instead of stalling
through the whole ladder.
"""


class _LockPersisted(OSError):
    """A step hit a transient lock on every attempt of the retry ladder.

    Subclasses OSError so the swap's handler catches it alongside any other
    filesystem failure. The *type* is what records that the ladder ran out, because
    the error code cannot: steps that are not retried — the directory enumerations
    the swap walks, say — raise the very same sharing violation, and inferring
    exhaustion from the code alone would blame a persistent holder for what was
    only ever one un-retried attempt.
    """


def _is_transient_windows_lock(exc: OSError) -> bool:
    """True when `exc` is another process's momentary handle, so worth retrying."""
    return getattr(exc, "winerror", None) in _TRANSIENT_LOCK_WINERRORS


def _through_transient_lock(action: Callable[..., object], *args: Any, **kwargs: Any) -> None:
    """Run `action(*args, **kwargs)`, waiting out a transient lock on what it touches.

    `_LOCK_RETRY_DELAYS` is read at call time, so a test can shorten the ladder. A
    lock still held after the last attempt is re-raised as `_LockPersisted`, carrying
    the original message; anything else propagates untouched.
    """
    for delay in _LOCK_RETRY_DELAYS:
        try:
            action(*args, **kwargs)
            return
        except OSError as exc:
            if not _is_transient_windows_lock(exc):
                raise
        time.sleep(delay)
    try:
        action(*args, **kwargs)
    except OSError as exc:
        if _is_transient_windows_lock(exc):
            raise _LockPersisted(str(exc)) from exc
        raise


def _clear_contents(directory: Path) -> None:
    """Empty `directory` without removing it, waiting out a transient lock on each
    entry. Files and links go with `unlink`, real directories with `rmtree`, which
    refuses to follow a link.
    """
    for entry in directory.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            _through_transient_lock(shutil.rmtree, entry)
        else:
            _through_transient_lock(entry.unlink)


def _windows_swap(install_dir: Path, source: Path, old: Path) -> None:
    """Replace `install_dir`'s contents with `source`'s, one file at a time.

    Windows fails the rename of a directory that contains an open file with
    STATUS_ACCESS_DENIED (`WinError 5`) — see MS-FSA 2.1.5.15.12,
    `FileRenameInformation`. That check is directory-only: renaming an open *file*
    is permitted. So the old files leave one at a time and `install_dir` is never
    renamed — it keeps its identity (and its place on `PATH`) while its contents
    are replaced. Per-file also survives what a whole-directory rename would not:
    a second `crm` running from this install holds files open, and those files
    still move.

    `source` is **copied**, not moved. This runs in the detached finisher, which
    executes from `source` — PyInstaller's bootloader holds a descriptor on
    `source/_internal/base_library.zip` for the process's whole life, so moving
    that file (or its directory) is refused with the same sharing violation the
    detached design exists to escape (#937). Copying leaves `source` intact, which
    is also the better recovery position; `cleanup_stale_updates` reaps it later.

    Every evacuating rename is recorded so a failure at any point can be undone in
    reverse, leaving the existing install working. The evacuated tree is deleted
    once the payload is in: the process that had those images loaded is the one
    that exited to let this run.

    Every step that mutates the filesystem goes through `_through_transient_lock`,
    so reaching the handler below means a lock outlived the retry ladder — that is
    what lets the error name a persistent holder rather than a passing scan. The
    copy is the exception: `shutil.copytree` reports a per-file failure as
    `shutil.Error`, which carries no `winerror`, so there is nothing there for the
    ladder to recognise.

    Symlinks move as links and only real directories are skipped, so `rmtree`
    never meets a link. The emptied directory skeleton is then removed, since it
    has no open handles and the copy would otherwise merge the new payload into
    leftover old directories. On failure after the evacuation, whatever the copy
    wrote is cleared first, because Windows refuses a rename onto an existing
    name; before the evacuation completes, `install_dir` still holds files never
    moved, so clearing it then would destroy their only copy. That clear is
    best-effort: a leftover makes the restoring rename fail, which is the honest
    place to report it. The restoring steps recreate skeleton directories (which
    inherit their ACL from the install's parent, as the originals did) and are
    retried like the moves out, since a lost race there would leave the install
    split across two trees. An exhausted ladder is named in the error, as a lock
    held persistently is a different cause, with a different fix, from a passing
    scan. After success the evacuated tree's removal is best-effort too: a second
    `crm` holding one of its images keeps it undeletable, and
    `cleanup_stale_updates` reaps it on a later run.
    """
    done: list[tuple[Path, Path]] = []
    evacuated = False
    try:
        for src in sorted(p for p in install_dir.rglob("*") if p.is_symlink() or not p.is_dir()):
            dest = old / src.relative_to(install_dir)
            _through_transient_lock(dest.parent.mkdir, parents=True, exist_ok=True)
            _through_transient_lock(src.rename, dest)
            done.append((src, dest))
        evacuated = True
        _clear_contents(install_dir)
        shutil.copytree(source, install_dir, dirs_exist_ok=True)
    except OSError as exc:
        try:
            if evacuated:
                with contextlib.suppress(OSError):
                    _clear_contents(install_dir)
            for src, dest in reversed(done):
                _through_transient_lock(src.parent.mkdir, parents=True, exist_ok=True)
                _through_transient_lock(dest.rename, src)
        except OSError as undo_exc:
            split = UpdateError(
                f"Could not replace the installed files in {install_dir} ({exc}), "
                f"and restoring the previous install then failed ({undo_exc}). "
                f"Part of it is in {old}, which holds the only copy of those "
                "files — keep it (a later self-update reaps parked directories), "
                "then re-run the Windows installer (install.ps1) to get a working "
                "install back."
            )
            split.install_intact = False
            raise split from undo_exc
        shutil.rmtree(old, ignore_errors=True)
        stuck = (
            " That lock outlived the retry window, so a process is holding the file "
            "open persistently rather than for the moment a scan takes."
            if isinstance(exc, _LockPersisted)
            else ""
        )
        raise UpdateError(
            f"Could not replace the installed files in {install_dir}: {exc}.{stuck} "
            "The existing install is intact and still works — re-run the Windows "
            "installer (install.ps1) to update."
        ) from exc
    shutil.rmtree(old, ignore_errors=True)


def swap_bundle(install_dir: Path, staged: Path, *, windows: bool) -> None:
    """Replace `install_dir`'s contents with `staged`, in place.

    Posix: rename the old dir aside, promote the staged dir, delete the old.
    Windows: `install_dir` cannot be renamed while a `crm` runs from inside it, so
    its files are evacuated one by one and `staged` is copied in — see
    `_windows_swap`.
    """
    parent = install_dir.parent
    old = parent / f"{install_dir.name}.old-{os.getpid()}"
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)
    if windows:
        _windows_swap(install_dir, staged, old)
        return
    install_dir.rename(old)
    try:
        staged.rename(install_dir)
    except Exception:
        old.rename(install_dir)
        raise
    shutil.rmtree(old, ignore_errors=True)


def cleanup_stale_updates(install_dir: Path) -> None:
    """Remove the trees a prior Windows swap left beside the install.

    Two kinds: the evacuated `<name>.old-*`, and the `<name>.new-*` payload the
    detached finisher ran from and therefore could not delete itself (#937). A
    payload named by a live handoff is skipped — that handoff is deleted as the
    finisher's last act, so its presence is the signal that a swap may still be
    reading from that tree, and reaping it mid-copy would corrupt the very bundle
    being installed.

    The evacuated `<name>.old-*` trees are reaped only when no handoff is live:
    while a finisher is mid-swap, `<name>.old-<pid>` can hold the only copy of
    the evacuated install, which that swap's rollback needs.
    """
    handoffs = _handoffs()
    live = _payloads_in_use(handoffs.live)
    for handoff in handoffs.stale:
        handoff.unlink(missing_ok=True)
    if not handoffs.live:
        for leftover in install_dir.parent.glob(f"{install_dir.name}.old-*"):
            shutil.rmtree(leftover, ignore_errors=True)
    for leftover in install_dir.parent.glob(f"{install_dir.name}.new-*"):
        if str(leftover) not in live:
            shutil.rmtree(leftover, ignore_errors=True)


_HANDOFF_STEM = "update-handoff"
_RESULT_NAME = "update-result.json"
_LOG_NAME = "update.log"

_PARENT_EXIT_TIMEOUT = 60.0
"""How long the finisher waits for the process that spawned it.

That process exits within milliseconds of the spawn, so this only has to outlast
a slow interpreter teardown; giving up is safe, because nothing has been touched.
"""
_PARENT_POLL_INTERVAL = 0.05

_HANDOFF_STALE_AFTER = 300.0
"""Age after which a handoff no longer counts as a finisher that may still be working.

The whole job is a wait of milliseconds plus a bundle-sized copy, so anything
this old lost its process, and a crashed finisher must not block updates for good.
"""


def result_path() -> Path:
    """Where the finisher records the outcome for the next `crm` run to report."""
    return state_home() / _RESULT_NAME


def log_path() -> Path:
    """The finisher's append-only log — the record survives being reported.

    `result_path()` is consumed by the run that reports it, so it is gone the moment
    anyone reads the notice. This keeps one line per attempt, so a failure the user
    scrolled past (or that happened under `--json`, where the notice never prints)
    is still there to inspect. The failure notice names this file.
    """
    return state_home() / _LOG_NAME


class _Handoffs(NamedTuple):
    live: list[Path]
    stale: list[Path]


def _handoffs(now: float | None = None) -> _Handoffs:
    """The handoff files on disk, split by whether their finisher may still be at
    work — see `_HANDOFF_STALE_AFTER`.
    """
    ref = time.time() if now is None else now
    found = _Handoffs(live=[], stale=[])
    for handoff in sorted(state_home().glob(f"{_HANDOFF_STEM}-*.json")):
        try:
            age = ref - handoff.stat().st_mtime
        except OSError:
            continue
        (found.live if age < _HANDOFF_STALE_AFTER else found.stale).append(handoff)
    return found


def pending_handoff(now: float | None = None) -> Path | None:
    """A handoff whose finisher has not reported yet, if there is one.

    Two finishers running at once would evacuate the same install into two separate
    parked trees and copy over each other's work, so a second `self-update` must not
    stage while one is pending — and two `self-update` runs in quick succession is
    exactly what a user does when the first one appears to have done nothing.
    """
    live = _handoffs(now).live
    return live[0] if live else None


def _payloads_in_use(handoffs: list[Path]) -> set[str]:
    """The payload dirs those handoffs name: trees a finisher may still be reading.

    An unreadable handoff protects nothing, so its payload may be reaped.
    """
    live: set[str] = set()
    for handoff in handoffs:
        try:
            parsed = read_json(handoff)
        except OSError:
            continue
        if not isinstance(parsed, dict):
            continue
        payload = cast("dict[str, Any]", parsed).get("payload")
        if isinstance(payload, str):
            live.add(payload)
    return live


def write_handoff(*, install_dir: Path, payload: Path, from_version: str, to_version: str) -> Path:
    """Record what the finisher needs to know, and return the file's path."""
    path = state_home() / f"{_HANDOFF_STEM}-{os.getpid()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "parent_pid": os.getpid(),
                "install_dir": str(install_dir),
                "payload": str(payload),
                "from_version": from_version,
                "to_version": to_version,
            }
        ),
        encoding="utf-8",
    )
    return path


def _load_kernel32() -> Any:
    """`ctypes.windll.kernel32`, behind a typed boundary.

    `ctypes.windll` exists only on Windows builds of CPython, so it is absent from
    the type stubs and everything reached through it reads as unknown. Declaring
    `Any` here confines that to one line instead of leaking through every call.
    """
    import ctypes

    return ctypes.windll.kernel32  # pyright: ignore


def _win_process_alive(pid: int) -> bool:
    """Windows liveness, via a handle on the process.

    A process that cannot be opened is gone, or was never ours to open. A handle
    can outlive the process it names, so liveness is the wait result, not
    whether OpenProcess succeeded.
    """
    _SYNCHRONIZE = 0x00100000
    _WAIT_OBJECT_0 = 0x0
    kernel32 = _load_kernel32()
    handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        return bool(kernel32.WaitForSingleObject(handle, 0) != _WAIT_OBJECT_0)
    finally:
        kernel32.CloseHandle(handle)


def process_alive(pid: int) -> bool:
    """True while `pid` is still running.

    A PermissionError on posix means alive, only not ours to signal.
    """
    if sys.platform.startswith("win"):
        return _win_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_for_process_exit(pid: int, timeout: float = _PARENT_EXIT_TIMEOUT) -> bool:
    """Block until `pid` exits; False if it is still there when `timeout` runs out.

    Polled rather than blocking on a handle so both platforms share one shape. The
    interval is short because the wait is normally single-digit milliseconds.
    """
    deadline = time.monotonic() + timeout
    while process_alive(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(_PARENT_POLL_INTERVAL)
    return True


def spawn_finisher(payload: Path, handoff: Path) -> int:
    """Launch the staged binary detached, to finish the swap once we exit.

    Fully detached: no console, no inherited stdio, its own process group (a Ctrl-C
    in the terminal that started us must not kill a swap already under way), and a
    working directory outside both the install and the payload — a process's
    working directory is itself a handle that would keep that directory
    undeletable on Windows. The creation flags are DETACHED_PROCESS |
    CREATE_NEW_PROCESS_GROUP, spelled numerically because the `subprocess`
    constants exist only on Windows builds.
    """
    import subprocess
    import tempfile

    windows = sys.platform.startswith("win")
    exe = payload / ("crm.exe" if windows else "crm")
    proc = subprocess.Popen(
        [str(exe), "self-update", "--finish-update", str(handoff)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=tempfile.gettempdir(),
        close_fds=True,
        creationflags=(0x00000008 | 0x00000200) if windows else 0,
        start_new_session=not windows,
    )
    return proc.pid


def _write_result(record: dict[str, Any]) -> None:
    """Write the finisher's record, suppressing a write error.

    `finish_deferred_swap` promises never to raise and has nowhere to report a
    failed write; the log line, appended first, is the durable account then.
    """
    with contextlib.suppress(OSError):
        atomic_write_json(result_path(), record)


def _append_log(record: Mapping[str, Any]) -> None:
    """Append one line for this attempt. Suppressed: the report is what matters."""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(float(record["at"])))
    outcome = (
        f"ok {record.get('from_version')} -> {record.get('to_version')}"
        if record.get("ok")
        else f"FAILED {record.get('from_version')} -> {record.get('to_version')}: "
        f"{record.get('error')}"
    )
    warnings = cast("list[Any]", record.get("warnings") or [])
    entries = [f"{stamp} {outcome}"]
    entries.extend(f"{stamp} warning: {w}" for w in warnings)
    with contextlib.suppress(OSError):
        log_path().parent.mkdir(parents=True, exist_ok=True)
        with log_path().open("a", encoding="utf-8") as fh:
            fh.write("\n".join(entries) + "\n")


def finish_deferred_swap(
    handoff_file: Path,
    *,
    refresh: Callable[[str, Path], list[str]] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """The detached finisher's whole body: wait for the parent, swap, record.

    Never raises. This process has nowhere to print — no console, stdio on
    devnull — so every outcome, a refused wait and a failed swap included, is
    written to `result_path()` for the next `crm` run to report. `refresh` re-syncs
    recorded skills/completion once the new bundle is in place and returns the
    warnings that run should print; it lives in the command layer, which owns the
    registries. Returns the record it wrote.

    `refresh` runs only after a successful swap, since refreshing earlier would
    leave a new skill tree beside an old binary, and its errors are suppressed so
    the finisher never dies with the record unwritten. The handoff is deleted
    last, after the record is durable, because its absence is what marks the
    payload reapable.
    """
    record: dict[str, Any] = {
        "ok": False,
        "at": time.time() if now is None else now,
        "error": None,
        "warnings": [],
    }
    try:
        handoff = json.loads(handoff_file.read_text(encoding="utf-8"))
        install = Path(handoff["install_dir"])
        payload = Path(handoff["payload"])
        record["from_version"] = handoff.get("from_version")
        record["to_version"] = handoff.get("to_version")
        record["install_dir"] = str(install)
        if not wait_for_process_exit(int(handoff["parent_pid"])):
            raise UpdateError(
                "The crm process that started the update was still running after "
                f"{_PARENT_EXIT_TIMEOUT:.0f}s, so its files could not be replaced."
            )
        swap_bundle(install, payload, windows=True)
    except Exception as exc:
        record["error"] = str(exc)
        record["install_intact"] = bool(getattr(exc, "install_intact", True))
    else:
        record["ok"] = True
        if refresh is not None:
            with contextlib.suppress(Exception):
                record["warnings"] = refresh(str(record["to_version"] or ""), install)
    _append_log(record)
    _write_result(record)
    with contextlib.suppress(OSError):
        handoff_file.unlink()
    return record


def take_update_result() -> dict[str, Any] | None:
    """Read the finisher's record and delete it, so it is reported exactly once.

    The file is removed whatever it held, so junk cannot suppress the next real record.
    """
    path = result_path()
    try:
        record = read_json(path)
    except OSError:
        record = None
    with contextlib.suppress(OSError):
        path.unlink()
    return cast("dict[str, Any]", record) if isinstance(record, dict) else None


def update_result_lines(record: Mapping[str, Any]) -> list[str]:
    """What the next run prints about a deferred swap: the outcome, then warnings.

    "Your install is unchanged" prints only when the install is intact: a swap
    whose rollback also failed leaves the install split, and the error text,
    which explains the recovery, must not be contradicted by a reassurance.
    """
    to_version = record.get("to_version") or "?"
    if record.get("ok"):
        lines = [f"Finished updating crm to {to_version}."]
    else:
        unchanged = (
            f"Your install is unchanged (still {record.get('from_version') or '?'}). "
            if record.get("install_intact", True)
            else ""
        )
        lines = [
            f"The last crm update to {to_version} could not be applied: "
            f"{record.get('error') or 'unknown error'} "
            f"{unchanged}"
            f"Details: {log_path()}"
        ]
    warnings = record.get("warnings")
    if isinstance(warnings, list):
        lines.extend(str(w) for w in cast("list[Any]", warnings))
    return lines


def emit_update_result_notice(
    *, json_mode: bool, stderr_isatty: bool, stream: IO[str] | None = None
) -> bool:
    """Report a deferred swap's outcome once, after the next command. Printed?

    Human TTY only. Under `--json` the record is left for a later run rather than
    polluting the envelope; with no TTY there is no one reading, and consuming the
    record would throw the only report of a failed update away.
    """
    if json_mode or not stderr_isatty:
        return False
    record = take_update_result()
    if record is None:
        return False
    for line in update_result_lines(record):
        print(line, file=stream if stream is not None else sys.stderr)
    return True


def perform_update(
    *,
    install_dir: Path,
    base_url: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Download, checksum-verify, and swap the bundle. Mismatch leaves it intact.

    On Windows a pending handoff refuses the update before the download, since a
    second finisher would fight the first for the same install. An unexpected
    error from the posix swap (which restores the original before re-raising)
    becomes UpdateError for a clean command-layer envelope; the Windows swap
    reports its own outcome as UpdateError. A deferred payload is the finisher's
    own program, so it is kept for `cleanup_stale_updates`; when no finisher was
    launched, the handoff is removed too, or it would refuse the next update as
    already staged until it aged out. A deferred run returns `updated` false,
    since nothing is replaced yet and a scripter must not read a staged payload
    as a completed upgrade: the finisher's record reports the outcome.
    """
    base = base_url or default_base_url()
    current = current_version()
    latest = fetch_latest_version(base, _INTERACTIVE_TIMEOUT)
    if latest is None:
        raise UpdateError("Could not determine the latest version (network unreachable).")
    try:
        outdated = compare_versions(current, latest) >= 0
    except ValueError as exc:
        raise UpdateError(f"Unexpected version format from release server: {latest!r}") from exc
    if outdated:
        return {"updated": False, "current": current, "latest": latest, "reason": "up-to-date"}

    to_version = latest.lstrip("vV")
    windows = sys.platform.startswith("win")
    if windows and pending_handoff() is not None:
        return {
            "updated": False,
            "pending": True,
            "reason": "swap-already-staged",
            "from_version": current,
            "to_version": to_version,
        }

    archive = platform_archive()
    if progress:
        progress(f"Downloading crm {latest}...")
    data = _download_archive(base, latest, archive)
    if progress:
        progress("Verifying checksum...")
    sums = _fetch_sha256sums(base, latest)
    expected = sums.get(archive)
    if not expected or not verify_sha256(data, expected):
        raise UpdateError(f"Checksum mismatch for {archive}; install left untouched.")

    staged = install_dir.parent / f"{install_dir.name}.new-{os.getpid()}"
    if staged.exists():
        shutil.rmtree(staged, ignore_errors=True)
    if progress:
        progress("Installing...")
    deferred = False
    handoff: Path | None = None
    try:
        _extract(archive, data, staged)
        if windows:
            handoff = write_handoff(
                install_dir=install_dir,
                payload=staged,
                from_version=current,
                to_version=to_version,
            )
            spawn_finisher(staged, handoff)
            deferred = True
        else:
            swap_bundle(install_dir, staged, windows=False)
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError(f"Update failed during install: {exc}") from exc
    finally:
        if not deferred and staged.exists():
            shutil.rmtree(staged, ignore_errors=True)
        if not deferred and handoff is not None:
            with contextlib.suppress(OSError):
                handoff.unlink(missing_ok=True)
    if deferred:
        return {
            "updated": False,
            "pending": True,
            "reason": "swap-deferred",
            "from_version": current,
            "to_version": to_version,
        }
    return {"updated": True, "from_version": current, "to_version": to_version}
