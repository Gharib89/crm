"""On-disk cache for entity definitions.

Layout under ``<CRM_HOME>/cache/<profile.name>/``::

    entitydefs.json   — JSON blob with url, api_version, cached_at, definitions

The cache is opt-in and read-through: callers supply a ``fetch`` callable and
choose whether to bypass the cache (``refresh=True``) or serve from it when
fresh (``refresh=False``). A 15-minute TTL backstop guards against stale data.

Atomic writes (unique tmp via ``tempfile.mkstemp`` + ``os.replace``) make
concurrent writes safe: each writer uses its own temp file so two processes
cannot clobber the same ``.tmp`` path.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from crm.utils.d365_backend import ConnectionProfile

TTL_SECONDS: int = 900  # 15-minute backstop

# Bumped when the cached row shape changes so stale-shape caches miss and
# refetch. v2 added PrimaryIdAttribute/PrimaryNameAttribute (`primary_id` /
# `primary_name`) for the normalized `_entity_id` + human primary-name column
# (ADR 0008 / #304). A legacy payload lacking `schema` (== v1) is treated as a
# miss — a one-time refresh, not an error.
SCHEMA_VERSION: int = 2


@dataclass(frozen=True)
class CacheLookup:
    """Result of :func:`load_definitions`."""

    definitions: list[dict[str, str]]
    status: str  # "hit" | "miss" | "refreshed"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _cache_home() -> Path:
    return Path(os.environ.get("CRM_HOME", str(Path.home() / ".crm"))).expanduser()


def cache_file(profile: ConnectionProfile) -> Path:
    """Return the cache-file path for *profile* (file may not exist yet)."""
    return _cache_home() / "cache" / profile.name / "entitydefs.json"


def move_cache(old: str, new: str) -> bool:
    """Move the per-profile cache dir ``cache/old`` → ``cache/new``. Returns True
    iff a move happened. Best-effort: a no-op when the source is absent or the
    destination already exists, so a rename never clobbers an unrelated cache."""
    src = _cache_home() / "cache" / old
    dst = _cache_home() / "cache" / new
    if not src.is_dir() or dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    return True


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------


def write_definitions(
    profile: ConnectionProfile,
    definitions: list[dict[str, str]],
    *,
    now: float,
) -> None:
    """Atomically write *definitions* to the cache file for *profile*.

    Creates parent directories as needed. Each call writes to its own unique
    temp file (via ``tempfile.mkstemp``) then atomically replaces the target
    with ``Path.replace``, so concurrent writers cannot clobber each other's
    temp file and a concurrent reader never sees a partial file.
    """
    path = cache_file(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "url": profile.url.rstrip("/"),
        "api_version": profile.api_version,
        "schema": SCHEMA_VERSION,
        "cached_at": now,
        "definitions": definitions,
    }
    fd, tmp_str = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def read_definitions(
    profile: ConnectionProfile,
    *,
    now: float,
) -> list[dict[str, str]] | None:
    """Return cached definitions for *profile*, or ``None`` on any miss.

    Treats ALL of the following as a miss (never raises):
    - file absent
    - ``OSError`` reading the file
    - JSON decode error / ``ValueError``
    - payload not a ``dict``
    - ``url`` or ``api_version`` mismatch
    - ``cached_at`` older than :data:`TTL_SECONDS`
    - ``definitions`` not a list of ``{"logical": str, "set_name": str}`` dicts
    """
    path = cache_file(profile)
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None

    if not isinstance(raw, dict):
        return None

    raw_dict = cast("dict[str, Any]", raw)
    try:
        cached_url: Any = raw_dict["url"]
        cached_ver: Any = raw_dict["api_version"]
        cached_at: Any = raw_dict["cached_at"]
        definitions: Any = raw_dict["definitions"]
    except KeyError:
        return None

    if cached_url != profile.url.rstrip("/"):
        return None
    if cached_ver != profile.api_version:
        return None
    # Reject a stale-shape cache (legacy payloads have no `schema` key → v1).
    if raw_dict.get("schema") != SCHEMA_VERSION:
        return None
    if not isinstance(cached_at, (int, float)):
        return None
    if now - float(cached_at) > TTL_SECONDS:
        return None

    if not _is_definition_list(definitions):
        return None

    return cast("list[dict[str, str]]", definitions)


def _is_definition_list(value: Any) -> bool:
    """Return True iff *value* is a list of valid definition rows.

    Each row must be a dict carrying at least the keys ``"logical"`` and
    ``"set_name"``, both mapping to :class:`str` values (extra keys are
    tolerated). A row missing either key, or with a non-str value, is rejected
    so that downstream consumers that index ``row["logical"]``/``row["set_name"]``
    never see a ``KeyError``.
    """
    if not isinstance(value, list):
        return False
    items: list[Any] = cast("list[Any]", value)
    for item in items:
        if not isinstance(item, dict):
            return False
        row = cast("dict[Any, Any]", item)
        logical = row.get("logical")
        set_name = row.get("set_name")
        if not isinstance(logical, str) or not isinstance(set_name, str):
            return False
    return True


# ---------------------------------------------------------------------------
# Clear / invalidate
# ---------------------------------------------------------------------------


def clear(profile: ConnectionProfile) -> bool:
    """Unlink the cache file if it exists.

    Returns ``True`` if the file existed (and was removed), ``False`` otherwise.
    Never raises ``FileNotFoundError`` — a concurrent removal is treated as
    though the file was already absent.
    """
    try:
        cache_file(profile).unlink()
        return True
    except FileNotFoundError:
        return False


def invalidate(profile: ConnectionProfile) -> None:
    """Remove the cache file, silently swallowing any ``OSError``.

    Safe to call on the success path of write operations — never raises.
    """
    try:
        clear(profile)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# High-level orchestration
# ---------------------------------------------------------------------------


def load_definitions(
    profile: ConnectionProfile,
    fetch: Callable[[], list[dict[str, str]]],
    *,
    refresh: bool,
    now: float,
) -> CacheLookup:
    """Return entity definitions, consulting the cache unless *refresh* is True.

    Behaviour:
    - ``refresh=True``: call *fetch*, write the result, return ``"refreshed"``.
    - ``refresh=False``, cache hit: return cached data as ``"hit"`` (no fetch).
    - ``refresh=False``, cache miss: call *fetch*, write the result, return ``"miss"``.
    """
    if refresh:
        defs = fetch()
        try:
            write_definitions(profile, defs, now=now)
        except OSError:
            pass
        return CacheLookup(defs, "refreshed")

    cached = read_definitions(profile, now=now)
    if cached is not None:
        return CacheLookup(cached, "hit")

    defs = fetch()
    try:
        write_definitions(profile, defs, now=now)
    except OSError:
        pass
    return CacheLookup(defs, "miss")
