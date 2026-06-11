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
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any

import requests

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
    try:
        resp = requests.get(f"{base_url}/latest/VERSION", timeout=timeout)
        resp.raise_for_status()
    except Exception:
        return None
    text = resp.text.strip()
    return text or None


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


def write_cache(latest: str, now: float) -> None:
    """Atomically record the latest-version probe result (tmp + replace)."""
    path = _cache_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"checked_at": now, "latest": latest}), encoding="utf-8"
    )
    tmp.replace(path)


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
    """`vX.Y.Z` / `X.Y.Z` -> int tuple. A leading `v` is tolerated on either side."""
    core = version.strip().lstrip("vV").split("+", 1)[0].split("-", 1)[0]
    return tuple(int(part) for part in core.split("."))


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


# ── Passive update notice ───────────────────────────────────────────────


def is_check_enabled(
    *, json_mode: bool, stderr_isatty: bool, env: Mapping[str, str]
) -> bool:
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


def pending_notice(current: str, *, frozen: bool = False) -> str | None:
    """One-line notice from the cache (no network) if a newer version is known."""
    cache = read_cache()
    if cache is None:
        return None
    latest = cache.get("latest")
    if not isinstance(latest, str) or compare_versions(current, latest) >= 0:
        return None
    how = "crm self-update" if frozen else "pip install -U crm"
    return f"A new crm release is available: {current} → {latest}. Run `{how}` to upgrade."


# ── cli.py orchestrators (guarded, at most once per process) ─────────────

_check_started = False  # background refresh spawned this process
_notified = False       # notice printed this process


def run_background_check(
    *, json_mode: bool, stderr_isatty: bool, env: Mapping[str, str], now: float
) -> "threading.Thread | None":
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
    *, json_mode: bool, stderr_isatty: bool, env: Mapping[str, str],
    stream: "IO[str] | None" = None,
) -> bool:
    """Print the cached update notice once per process, if enabled. Returns printed?"""
    global _notified
    if _notified:
        return False
    if not is_check_enabled(json_mode=json_mode, stderr_isatty=stderr_isatty, env=env):
        return False
    message = pending_notice(current_version(), frozen=is_frozen())
    if message is None:
        return False
    _notified = True
    print(message, file=stream if stream is not None else sys.stderr)
    return True


# ── self-update orchestration ───────────────────────────────────────────


def check_for_update(base_url: str | None = None) -> dict[str, Any]:
    """Compare the running version to the published latest. Network, no fs change."""
    current = current_version()
    latest = fetch_latest_version(base_url or default_base_url(), _INTERACTIVE_TIMEOUT)
    if latest is None:
        raise UpdateError("Could not determine the latest version (network unreachable).")
    return {
        "current": current,
        "latest": latest,
        "update_available": compare_versions(current, latest) < 0,
    }


def _download_archive(base_url: str, version: str, archive: str) -> bytes:
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
    distribution endpoint — and the 3.9 floor lacks tarfile's `filter=` guard.
    """
    dest.mkdir(parents=True, exist_ok=True)
    if archive.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if not _is_safe_member(info.filename):
                    raise UpdateError(
                        f"Unsafe path in archive (traversal): {info.filename!r}"
                    )
                # A symlink entry (S_IFLNK in the high external-attr bits) can point
                # outside dest and be followed by a later member — reject it.
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise UpdateError(
                        f"Unsafe link member in archive: {info.filename!r}"
                    )
            zf.extractall(dest)
    else:
        # `filter="data"` is the safe extractor, but it only exists on 3.12+
        # (our floor is 3.9) — pass it conditionally via kwargs so the older
        # signature is not referenced directly. We additionally reject link/special
        # members up front so the <3.12 path (no filter) is also safe: a symlink
        # like `lib -> /etc` followed by `lib/x` escapes dest even with no `..`.
        extract_kwargs: dict[str, Any] = {}
        if sys.version_info >= (3, 12):
            extract_kwargs["filter"] = "data"
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for member in tar.getmembers():
                if not _is_safe_member(member.name):
                    raise UpdateError(
                        f"Unsafe path in archive (traversal): {member.name!r}"
                    )
                if not (member.isfile() or member.isdir()):
                    raise UpdateError(
                        f"Unsafe non-regular member in archive: {member.name!r}"
                    )
            tar.extractall(dest, **extract_kwargs)


def swap_bundle(install_dir: Path, staged: Path, *, windows: bool) -> None:
    """Replace `install_dir`'s contents with `staged`, in place.

    Posix: rename the old dir aside, promote the staged dir, delete the old.
    Windows: the running executable is locked, so the old dir cannot be deleted
    now — it is renamed aside and cleaned up on a later run (`cleanup_stale_updates`).
    """
    parent = install_dir.parent
    old = parent / f"{install_dir.name}.old-{os.getpid()}"
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)
    install_dir.rename(old)
    try:
        staged.rename(install_dir)
    except Exception:
        # Promotion failed — restore the original so the install stays working.
        old.rename(install_dir)
        raise
    if windows:
        return  # leave `old` parked; a locked exe inside blocks deletion now
    shutil.rmtree(old, ignore_errors=True)


def cleanup_stale_updates(install_dir: Path) -> None:
    """Remove parked `<name>.old-*` dirs left by a prior Windows swap."""
    for leftover in install_dir.parent.glob(f"{install_dir.name}.old-*"):
        shutil.rmtree(leftover, ignore_errors=True)


def perform_update(
    *, install_dir: Path, base_url: str | None = None
) -> dict[str, Any]:
    """Download, checksum-verify, and swap the bundle. Mismatch leaves it intact."""
    base = base_url or default_base_url()
    current = current_version()
    latest = fetch_latest_version(base, _INTERACTIVE_TIMEOUT)
    if latest is None:
        raise UpdateError("Could not determine the latest version (network unreachable).")
    if compare_versions(current, latest) >= 0:
        return {"updated": False, "current": current, "latest": latest,
                "reason": "up-to-date"}

    archive = platform_archive()
    data = _download_archive(base, latest, archive)
    sums = _fetch_sha256sums(base, latest)
    expected = sums.get(archive)
    if not expected or not verify_sha256(data, expected):
        raise UpdateError(
            f"Checksum mismatch for {archive}; install left untouched."
        )

    staged = install_dir.parent / f"{install_dir.name}.new-{os.getpid()}"
    if staged.exists():
        shutil.rmtree(staged, ignore_errors=True)
    try:
        _extract(archive, data, staged)
        swap_bundle(install_dir, staged, windows=sys.platform.startswith("win"))
    except UpdateError:
        raise
    except Exception as exc:
        # Unexpected filesystem error (rename/permission/AV lock). swap_bundle
        # restores the original install before re-raising, so the install stays
        # intact; surface it as UpdateError for a clean command-layer envelope.
        raise UpdateError(f"Update failed during install: {exc}") from exc
    finally:
        if staged.exists():
            shutil.rmtree(staged, ignore_errors=True)
    return {"updated": True, "current": current, "latest": latest}
