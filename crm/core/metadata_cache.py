"""On-disk cache for entity definitions.

Layout under ``<CRM_HOME>/cache/<profile.name>/``::

    entitydefs.json   — JSON blob with url, api_version, cached_at, definitions

The cache is opt-in and read-through: callers supply a ``fetch`` callable and
choose whether to bypass the cache (``refresh=True``) or serve from it when
fresh (``refresh=False``). A 15-minute TTL backstop guards against stale data.

Atomic writes (tmp + ``os.replace``) make concurrent one-shot agent calls safe.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from crm.utils.d365_backend import ConnectionProfile

TTL_SECONDS: int = 900  # 15-minute backstop


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

    Creates parent directories as needed. Writes via a ``.tmp`` sibling then
    ``os.replace`` so a concurrent reader never sees a partial file.
    """
    path = cache_file(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "url": profile.url.rstrip("/"),
        "api_version": profile.api_version,
        "cached_at": now,
        "definitions": definitions,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


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
    - ``definitions`` not a list of ``{str: str}`` dicts
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
    if not isinstance(cached_at, (int, float)):
        return None
    if now - float(cached_at) > TTL_SECONDS:
        return None

    if not _is_str_dict_list(definitions):
        return None

    return cast("list[dict[str, str]]", definitions)


def _is_str_dict_list(value: Any) -> bool:
    """Return True iff *value* is a ``list[dict[str, str]]``."""
    if not isinstance(value, list):
        return False
    items: list[Any] = cast("list[Any]", value)
    for item in items:
        if not isinstance(item, dict):
            return False
        for k, v in cast("dict[Any, Any]", item).items():
            if not isinstance(k, str) or not isinstance(v, str):
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
        write_definitions(profile, defs, now=now)
        return CacheLookup(defs, "refreshed")

    cached = read_definitions(profile, now=now)
    if cached is not None:
        return CacheLookup(cached, "hit")

    defs = fetch()
    write_definitions(profile, defs, now=now)
    return CacheLookup(defs, "miss")
