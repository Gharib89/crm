"""Self-update + passive update-notice logic for the crm CLI.

Frozen (PyInstaller) installs can learn the latest published version with one
cheap GET against the R2 release layout and swap the bundle in place. The
passive notice is cache-only at command exit; a guarded background thread does
the network refresh, so a command is never slowed and machine-readable output
is never polluted.
"""

from __future__ import annotations

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
from typing import IO, Any, cast

# requests is imported lazily inside the network functions below so that merely
# importing this module (e.g. when `crm --help` imports the self-update command
# module to render help) never pulls in the transport stack (#247).
from crm import __version__


class UpdateError(Exception):
    """A self-update could not be completed; the existing install is untouched."""


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


def _cache_path() -> Path:
    # Resolve CRM_HOME directly (mirrors crm/core/audit.py) rather than importing
    # session's private root helper.
    root = Path(os.environ.get("CRM_HOME", str(Path.home() / ".crm"))).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root / "update-check.json"


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
# scan it provokes. That handle lives only as long as the scan, so a move waits the
# lock out across this ladder (~2.5s in total) before it counts as terminal (#935).
_MOVE_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.0)

# ERROR_SHARING_VIOLATION and ERROR_LOCK_VIOLATION. Matched on `winerror` rather
# than `errno` because Windows maps ERROR_ACCESS_DENIED to EACCES as well, and a
# genuine ACL denial must fail at once instead of stalling through the whole ladder.
_TRANSIENT_LOCK_WINERRORS = (32, 33)


def _is_transient_lock(exc: OSError) -> bool:
    """True when `exc` is another process's momentary handle, so worth retrying."""
    return getattr(exc, "winerror", None) in _TRANSIENT_LOCK_WINERRORS


def _rename_with_retry(src: Path, dest: Path) -> None:
    """`src.rename(dest)`, waiting out a transient lock on `src`.

    `_MOVE_RETRY_DELAYS` is read at call time, so a test can shorten the ladder.
    The final attempt is deliberately outside the loop: its error propagates, which
    keeps an exhausted retry indistinguishable from a plain failure to the caller.
    """
    for delay in _MOVE_RETRY_DELAYS:
        try:
            src.rename(dest)
            return
        except OSError as exc:
            if not _is_transient_lock(exc):
                raise
        time.sleep(delay)
    src.rename(dest)


def _windows_swap(install_dir: Path, staged: Path, old: Path) -> None:
    """Swap a frozen bundle whose own running executable lives inside it.

    Windows fails the rename of a directory that contains an open file with
    STATUS_ACCESS_DENIED (`WinError 5`) — see MS-FSA 2.1.5.15.12,
    `FileRenameInformation`. The process performing the swap *is* `crm.exe`
    inside `install_dir`, with its DLLs loaded from `_internal`, so renaming
    `install_dir` aside can never succeed there (#932). That check is
    directory-only: renaming an open *file* is permitted, because the loader
    opens images with `FILE_SHARE_DELETE`. So the files leave one at a time, and
    `install_dir` itself is never renamed — it keeps its identity (and its place
    on `PATH`) while its contents are replaced.

    Every rename is recorded so a failure at any point can be undone in reverse,
    leaving the existing install working. The evacuated tree stays parked for
    `cleanup_stale_updates` to reap on a later run: the images are still loaded
    now, so they cannot be deleted yet.
    """
    done: list[tuple[Path, Path]] = []
    try:
        # Symlinks move as links; only real directories are skipped, so that
        # `rmtree` below never meets one (it refuses to follow a link).
        for src in sorted(p for p in install_dir.rglob("*") if p.is_symlink() or not p.is_dir()):
            dest = old / src.relative_to(install_dir)
            dest.parent.mkdir(parents=True, exist_ok=True)
            _rename_with_retry(src, dest)
            done.append((src, dest))
        # Nothing but the emptied directory skeleton is left; the payload cannot be
        # moved in on top of it (Windows rejects a rename onto an existing name),
        # and an empty directory has no open handles to block its removal.
        for leftover in install_dir.iterdir():
            shutil.rmtree(leftover)
        for src in sorted(staged.iterdir()):
            dest = install_dir / src.name
            _rename_with_retry(src, dest)
            done.append((src, dest))
        staged.rmdir()
    except OSError as exc:
        try:
            for src, dest in reversed(done):
                # Recreates any skeleton directory removed above; on Windows these
                # inherit their ACL from the install's parent, as the originals did.
                src.parent.mkdir(parents=True, exist_ok=True)
                # Retried like the moves out: losing this race is what would leave
                # the install split across two trees, so it is the worst place to
                # treat a momentary scanner handle as fatal.
                _rename_with_retry(dest, src)
        except OSError as undo_exc:
            # Undoing failed too, so the install is now split across both trees.
            # Leave `old` in place — it holds the only copy of what moved — and say
            # where it is, rather than claiming an intact install.
            raise UpdateError(
                f"Could not replace the installed files in {install_dir} ({exc}), "
                f"and restoring the previous install then failed ({undo_exc}). "
                f"Part of it is in {old}, which holds the only copy of those "
                "files — keep it (a later self-update reaps parked directories), "
                "then re-run the Windows installer (install.ps1) to get a working "
                "install back."
            ) from undo_exc
        shutil.rmtree(old, ignore_errors=True)
        # Say so when the retries ran out, rather than only naming the raw error:
        # a lock that survives the whole ladder is held persistently, which is a
        # different cause (and a different fix) from the scan this retries for.
        stuck = (
            " That lock outlived the retry window, so a process is holding the file "
            "open persistently rather than for the moment a scan takes."
            if _is_transient_lock(exc)
            else ""
        )
        raise UpdateError(
            f"Could not replace the installed files in {install_dir}: {exc}.{stuck} "
            "The existing install is intact and still works — re-run the Windows "
            "installer (install.ps1) to update."
        ) from exc


def swap_bundle(install_dir: Path, staged: Path, *, windows: bool) -> None:
    """Replace `install_dir`'s contents with `staged`, in place.

    Posix: rename the old dir aside, promote the staged dir, delete the old.
    Windows: `install_dir` cannot be renamed at all while the running executable
    sits inside it, so its files are evacuated one by one instead — see
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
    """Remove parked `<name>.old-*` dirs left by a prior Windows swap."""
    for leftover in install_dir.parent.glob(f"{install_dir.name}.old-*"):
        shutil.rmtree(leftover, ignore_errors=True)


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
    try:
        _extract(archive, data, staged)
        swap_bundle(install_dir, staged, windows=sys.platform.startswith("win"))
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
        if staged.exists():
            shutil.rmtree(staged, ignore_errors=True)
    to_version = latest.lstrip("vV")
    return {"updated": True, "from_version": current, "to_version": to_version}
