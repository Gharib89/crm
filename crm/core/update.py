"""Self-update + passive update-notice logic for the crm CLI.

Frozen (PyInstaller) installs can learn the latest published version with one
cheap GET against the R2 release layout and swap the bundle in place. The
passive notice is cache-only at command exit; a guarded background thread does
the network refresh, so a command is never slowed and machine-readable output
is never polluted.
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

# requests is imported lazily inside the network functions below so that merely
# importing this module (e.g. when `crm --help` imports the self-update command
# module to render help) never pulls in the transport stack (#247).
from crm import __version__


class UpdateError(Exception):
    """A self-update could not be completed; the existing install is untouched.

    `install_intact` is False for the one exception to that promise: a Windows
    swap whose rollback also failed, leaving the install split across two trees.
    The failure notice keys its "your install is unchanged" reassurance on it.
    """

    install_intact: bool = True


# Env var to opt out of the passive update notice entirely.
_OPT_OUT_ENV = "CRM_NO_UPDATE_CHECK"

# Cloudflare R2 public base, mirroring scripts/install.sh. CRM_INSTALL_BASE_URL
# overrides it (shared with the install scripts; honored for the download base).
_DEFAULT_BASE_URL = "https://pub-bbeb86c46454443ca76521dd4d29818e.r2.dev"

# Hard ceiling for the PASSIVE check. On-prem/government networks may have no
# outbound internet or a hostile proxy; the background check must never slow a
# command, so it gives up after ~1s.
_NETWORK_TIMEOUT = 1.0

# The user explicitly ran `self-update` — a slow-but-working proxy must not be
# mistaken for "unreachable", so the interactive paths wait longer.
_INTERACTIVE_TIMEOUT = 10.0


def default_base_url() -> str:
    return os.environ.get("CRM_INSTALL_BASE_URL", _DEFAULT_BASE_URL)


def fetch_latest_version(base_url: str, timeout: float = _NETWORK_TIMEOUT) -> str | None:
    """GET ``<base_url>/latest/VERSION`` and return the trimmed `vX.Y.Z`.

    Returns None on any failure (timeout, connection error, non-2xx, empty body):
    the caller treats "unknown latest" as "no notice", never as an error.
    """
    import requests  # deferred transport import (#247)

    try:
        resp = requests.get(f"{base_url}/latest/VERSION", timeout=timeout)
        resp.raise_for_status()
    except Exception:
        return None
    text = resp.text.strip()
    if not text or not _is_version(text):
        # A 200 with junk (proxy login page, HTML error, "latest") must never
        # be cached or compared — it would raise in compare_versions downstream.
        return None
    return text


# ── Update-check cache (throttle) ───────────────────────────────────────

# At most one remote check per this window; the result is cached in CRM_HOME.
_CHECK_INTERVAL = 86400.0  # 24h


def _state_dir() -> Path:
    # Resolve CRM_HOME directly (mirrors crm/core/audit.py) rather than importing
    # session's private root helper.
    root = Path(os.environ.get("CRM_HOME", str(Path.home() / ".crm"))).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cache_path() -> Path:
    return _state_dir() / "update-check.json"


def read_cache() -> dict[str, Any] | None:
    """Last-known {checked_at, latest}, or None if absent/unreadable."""
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_cache_dict(data: dict[str, Any]) -> None:
    path = _cache_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


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


# ── Download integrity (mirrors scripts/install.sh) ─────────────────────

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


# ── Frozen detection + version source ───────────────────────────────────


def is_frozen() -> bool:
    """True when running as a PyInstaller bundle (mirrors keyring_store.py)."""
    return bool(getattr(sys, "frozen", False))


def current_version() -> str:
    return __version__


def install_dir() -> Path:
    """The frozen bundle's install directory (the dir holding the `crm` launcher)."""
    return Path(sys.executable).resolve().parent


# ── Install-method detection + upgrade-command construction ──────────────

# The git source `crm` is installed from (it is not published to PyPI, so every
# non-frozen upgrade path is git-based). Shared by every constructed command.
_GITHUB_REPO = "https://github.com/Gharib89/crm"

# The fixed install-method vocabulary reported by `detect_install_method` and
# surfaced as `data.install_method` in `--json` (part of the CLI contract).
INSTALL_METHODS = ("frozen", "uv-tool", "pipx", "editable", "pip-git", "unknown")

# Methods `self-update` may auto-run for: isolated, force-reinstallable envs only.
_AUTO_RUN_METHODS = ("uv-tool", "pipx")


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
    signal; the rest keys off `sys.prefix` layout and PEP 610 metadata.
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
        # Only a git VCS install maps to `pip-git` — the constructed upgrade command
        # is a `git+…` URL, so a non-git VCS (hg/bzr/svn) would get wrong guidance.
        # It falls through to `unknown` (generic guidance, no wrong auto-action).
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
    """
    tag = f"v{latest.lstrip('vV')}"
    spec = f"git+{_GITHUB_REPO}@{tag}"
    if method == "uv-tool":
        # --force (not `uv tool upgrade`): uv pins the git commit SHA, so a plain
        # upgrade can silently no-op; a forced reinstall reliably fetches the tag.
        return f"uv tool install --force {spec}"
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


# ── Passive update notice ───────────────────────────────────────────────


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
        return None  # junk cache (partial write / manual edit) — stay silent
    notified_at = cache.get("notified_at")
    if isinstance(notified_at, (int, float)):
        ref = time.time() if now is None else now
        if (ref - notified_at) < _CHECK_INTERVAL:
            return None
    return (
        f"A new crm release is available: {current} → {latest}. Run `crm self-update` to upgrade."
    )


# ── cli.py orchestrators (guarded, at most once per process) ─────────────

_check_started = False  # background refresh spawned this process
_notified = False  # notice printed this process


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
    once a day rather than on every invocation).
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
        pass  # stamping is best-effort; never let it break the command
    return True


# ── self-update orchestration ───────────────────────────────────────────


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
    import requests  # deferred transport import (#247)

    # (connect, read) timeout: bound the connect so an unreachable network fails
    # fast rather than appearing to hang. Network/HTTP errors become UpdateError
    # so the command layer emits a clean envelope instead of a traceback.
    url = f"{base_url}/{version}/{archive}"
    try:
        resp = requests.get(url, timeout=(_INTERACTIVE_TIMEOUT, 30))
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise UpdateError(f"Failed to download {url}: {exc}") from exc
    return resp.content


def _fetch_sha256sums(base_url: str, version: str) -> dict[str, str]:
    import requests  # deferred transport import (#247)

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
    """
    dest.mkdir(parents=True, exist_ok=True)
    if archive.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if not _is_safe_member(info.filename):
                    raise UpdateError(f"Unsafe path in archive (traversal): {info.filename!r}")
                # A symlink entry (S_IFLNK in the high external-attr bits) can point
                # outside dest and be followed by a later member — reject it.
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise UpdateError(f"Unsafe link member in archive: {info.filename!r}")
            zf.extractall(dest)
    else:
        # `filter="data"` is the safe extractor (always present on the 3.13
        # floor). We additionally reject link/special members up front as
        # defense-in-depth: a symlink like `lib -> /etc` followed by `lib/x`
        # escapes dest even with no `..`.
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for member in tar.getmembers():
                if not _is_safe_member(member.name):
                    raise UpdateError(f"Unsafe path in archive (traversal): {member.name!r}")
                if not (member.isfile() or member.isdir()):
                    raise UpdateError(f"Unsafe non-regular member in archive: {member.name!r}")
            tar.extractall(dest, filter="data")


# Windows raises ERROR_SHARING_VIOLATION when another process holds a file open
# without delete-sharing. A virus scanner or the search indexer is the usual cause,
# and both react *to* writes inside a program directory, so the swap races the very
# scan it provokes. That handle lives only as long as the scan, so each step waits
# the lock out across this ladder (~2.5s in total) before it counts as terminal (#935).
_LOCK_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.0)

# ERROR_SHARING_VIOLATION and ERROR_LOCK_VIOLATION. Matched on `winerror` rather
# than `errno` because Windows maps ERROR_ACCESS_DENIED to EACCES as well, and a
# genuine ACL denial must fail at once instead of stalling through the whole ladder.
_TRANSIENT_LOCK_WINERRORS = (32, 33)


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
    """
    done: list[tuple[Path, Path]] = []
    evacuated = False
    try:
        # Symlinks move as links; only real directories are skipped, so that
        # `rmtree` below never meets one (it refuses to follow a link).
        for src in sorted(p for p in install_dir.rglob("*") if p.is_symlink() or not p.is_dir()):
            dest = old / src.relative_to(install_dir)
            _through_transient_lock(dest.parent.mkdir, parents=True, exist_ok=True)
            _through_transient_lock(src.rename, dest)
            done.append((src, dest))
        evacuated = True
        # Nothing but the emptied directory skeleton is left, and an empty directory
        # has no open handles to block its removal. It has to go: the copy below
        # would otherwise merge the new payload into leftover old directories.
        _clear_contents(install_dir)
        shutil.copytree(source, install_dir, dirs_exist_ok=True)
    except OSError as exc:
        try:
            if evacuated:
                # Whatever the copy wrote occupies names the originals need back
                # (Windows refuses a rename onto an existing name). Guarded by
                # `evacuated` because before that point `install_dir` still holds
                # files that were never moved — clearing then would destroy the only
                # copy of them. Best-effort: a leftover that cannot be removed makes
                # the rename below fail, which is the honest place to report it.
                with contextlib.suppress(OSError):
                    _clear_contents(install_dir)
            for src, dest in reversed(done):
                # Recreates any skeleton directory removed above; on Windows these
                # inherit their ACL from the install's parent, as the originals did.
                # Retried like the moves out: losing this race is what would leave
                # the install split across two trees, so it is the worst place to
                # treat a momentary scanner handle as fatal.
                _through_transient_lock(src.parent.mkdir, parents=True, exist_ok=True)
                _through_transient_lock(dest.rename, src)
        except OSError as undo_exc:
            # Undoing failed too, so the install is now split across both trees.
            # Leave `old` in place — it holds the only copy of what moved — and say
            # where it is, rather than claiming an intact install.
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
        # Say so when the retries ran out, rather than only naming the raw error:
        # a lock that survives the whole ladder is held persistently, which is a
        # different cause (and a different fix) from the scan this retries for.
        # Keyed on the type, not the error code — see `_LockPersisted`.
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
    # Only reached on success (every path above re-raises). Best-effort: a second
    # `crm` still holding one of the evacuated images keeps it undeletable, and
    # `cleanup_stale_updates` reaps whatever survives on a later run.
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
        # Promotion failed — restore the original so the install stays working.
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
    """
    handoffs = _handoffs()
    live = _payloads_in_use(handoffs.live)
    for handoff in handoffs.stale:
        handoff.unlink(missing_ok=True)
    if not handoffs.live:
        # While a finisher is mid-swap, `<name>.old-<pid>` can hold the only copy
        # of the evacuated install — reaping it would leave that swap's rollback
        # with nothing to restore. Live handoffs mark exactly that window.
        for leftover in install_dir.parent.glob(f"{install_dir.name}.old-*"):
            shutil.rmtree(leftover, ignore_errors=True)
    for leftover in install_dir.parent.glob(f"{install_dir.name}.new-*"):
        if str(leftover) not in live:
            shutil.rmtree(leftover, ignore_errors=True)


# ── deferred swap: the detached finisher (Windows) ───────────────────────

# A frozen bundle cannot replace itself on Windows. PyInstaller's C bootloader
# opens `_internal/base_library.zip` before any Python runs and holds that
# descriptor for the process's whole life, without delete-sharing — so renaming
# it is refused (`WinError 32`), writing over it is refused, and renaming the
# directory that contains it is refused (`WinError 5`). No retry helps: the handle
# is released only when the process exits (#937).
#
# So Windows stages the payload, hands off to a detached copy of the *new* binary,
# and exits. The finisher waits for us to go, performs the swap, and records the
# outcome for the next `crm` run to report. Posix needs none of this — an open
# file there can be replaced outright — and keeps swapping in-process.

_HANDOFF_STEM = "update-handoff"
_RESULT_NAME = "update-result.json"
_LOG_NAME = "update.log"

# How long the finisher waits for the process that spawned it. That process exits
# within milliseconds of the spawn, so this only has to outlast a slow interpreter
# teardown; giving up is safe, because nothing has been touched yet.
_PARENT_EXIT_TIMEOUT = 60.0
_PARENT_POLL_INTERVAL = 0.05

# When a handoff stops counting as a finisher that may still be working. The whole
# job is a wait of milliseconds plus a bundle-sized copy, so anything this old lost
# its process — and a crashed finisher must not block updates for good.
_HANDOFF_STALE_AFTER = 300.0


def result_path() -> Path:
    """Where the finisher records the outcome for the next `crm` run to report."""
    return _state_dir() / _RESULT_NAME


def log_path() -> Path:
    """The finisher's append-only log — the record survives being reported.

    `result_path()` is consumed by the run that reports it, so it is gone the moment
    anyone reads the notice. This keeps one line per attempt, so a failure the user
    scrolled past (or that happened under `--json`, where the notice never prints)
    is still there to inspect. The failure notice names this file.
    """
    return _state_dir() / _LOG_NAME


class _Handoffs(NamedTuple):
    live: list[Path]
    stale: list[Path]


def _handoffs(now: float | None = None) -> _Handoffs:
    """The handoff files on disk, split by whether their finisher may still be at
    work — see `_HANDOFF_STALE_AFTER`.
    """
    ref = time.time() if now is None else now
    found = _Handoffs(live=[], stale=[])
    for handoff in sorted(_state_dir().glob(f"{_HANDOFF_STEM}-*.json")):
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
    """The payload dirs those handoffs name — trees a finisher may still be reading."""
    live: set[str] = set()
    for handoff in handoffs:
        try:
            parsed = json.loads(handoff.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # unreadable handoff protects nothing; the payload can go
        if not isinstance(parsed, dict):
            continue
        payload = cast("dict[str, Any]", parsed).get("payload")
        if isinstance(payload, str):
            live.add(payload)
    return live


def write_handoff(*, install_dir: Path, payload: Path, from_version: str, to_version: str) -> Path:
    """Record what the finisher needs to know, and return the file's path."""
    path = _state_dir() / f"{_HANDOFF_STEM}-{os.getpid()}.json"
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
    """Windows liveness, via a handle on the process."""
    _SYNCHRONIZE = 0x00100000
    _WAIT_OBJECT_0 = 0x0
    kernel32 = _load_kernel32()
    handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
    if not handle:
        return False  # gone, or never ours to open
    try:
        # A handle can outlive the process it names, so liveness is the wait result,
        # not whether OpenProcess succeeded.
        return bool(kernel32.WaitForSingleObject(handle, 0) != _WAIT_OBJECT_0)
    finally:
        kernel32.CloseHandle(handle)


def process_alive(pid: int) -> bool:
    """True while `pid` is still running."""
    if sys.platform.startswith("win"):
        return _win_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, just not ours to signal
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
    undeletable on Windows.
    """
    import subprocess
    import tempfile

    windows = sys.platform.startswith("win")
    exe = payload / ("crm.exe" if windows else "crm")
    # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP. Spelled numerically because the
    # `subprocess` constants exist only on Windows builds.
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
    # Suppressed: `finish_deferred_swap` promises never to raise, and the finisher
    # has nowhere to report a failed write anyway — the log line (appended first)
    # is the durable account when this one cannot land.
    path = result_path()
    tmp = path.with_suffix(".json.tmp")
    with contextlib.suppress(OSError):
        tmp.write_text(json.dumps(record), encoding="utf-8")
        tmp.replace(path)


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
            # Only after a successful swap: refreshing earlier would leave a new
            # skill tree beside an old binary. Suppressed because the finisher must
            # never die with the record unwritten — the swap already landed.
            with contextlib.suppress(Exception):
                record["warnings"] = refresh(str(record["to_version"] or ""), install)
    _append_log(record)
    _write_result(record)
    # The handoff's absence is what marks the payload reapable — so it goes last,
    # after the record is durable.
    with contextlib.suppress(OSError):
        handoff_file.unlink()
    return record


def take_update_result() -> dict[str, Any] | None:
    """Read the finisher's record and delete it, so it is reported exactly once."""
    path = result_path()
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Missing (the common case), unreadable, or malformed by a partial write.
        # Remove it either way, so junk cannot suppress the next real record.
        with contextlib.suppress(OSError):
            path.unlink()
        return None
    with contextlib.suppress(OSError):
        path.unlink()
    return cast("dict[str, Any]", record) if isinstance(record, dict) else None


def update_result_lines(record: Mapping[str, Any]) -> list[str]:
    """What the next run prints about a deferred swap: the outcome, then warnings."""
    to_version = record.get("to_version") or "?"
    if record.get("ok"):
        lines = [f"Finished updating crm to {to_version}."]
    else:
        # "Unchanged" is a promise, not a platitude — a swap whose rollback also
        # failed leaves the install split, and the error text (which explains the
        # recovery) must not be contradicted by a reassurance right after it.
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
    """Download, checksum-verify, and swap the bundle. Mismatch leaves it intact."""
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
        # Refuse before the download: a second finisher would fight the first one for
        # the same install (see `pending_handoff`), and the fetch would be wasted.
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
            # This process cannot replace its own bundle — see the finisher section.
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
        # Unexpected filesystem error (rename/permission/AV lock) from the posix
        # swap, which restores the original install before re-raising, so the
        # install stays intact; surface it as UpdateError for a clean
        # command-layer envelope. The windows swap reports its own outcome —
        # including a failed restore — as UpdateError, re-raised untouched above.
        raise UpdateError(f"Update failed during install: {exc}") from exc
    finally:
        # A deferred payload is the finisher's own program — deleting it would kill
        # the swap. `cleanup_stale_updates` reaps it on a later run.
        if not deferred and staged.exists():
            shutil.rmtree(staged, ignore_errors=True)
        if not deferred and handoff is not None:
            # No finisher was launched, so this handoff guards nothing — left in
            # place it would refuse the next update as already-staged (pointing
            # at the payload just deleted) until it aged out.
            with contextlib.suppress(OSError):
                handoff.unlink(missing_ok=True)
    if deferred:
        # `updated` stays false: nothing has been replaced yet, and a scripter must
        # not read a staged payload as a completed upgrade. The finisher's record,
        # surfaced on the next run, is what reports the outcome.
        return {
            "updated": False,
            "pending": True,
            "reason": "swap-deferred",
            "from_version": current,
            "to_version": to_version,
        }
    return {"updated": True, "from_version": current, "to_version": to_version}
