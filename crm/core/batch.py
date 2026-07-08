"""$batch JSON-file loader + result rendering + chunked-execution helper."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from crm.utils.d365_backend import D365Backend, D365Error
from crm.utils.d365_types import BatchOperation, BatchResult

_VALID_METHODS = ("GET", "POST", "PATCH", "DELETE")

# Ops per $batch POST. Matches the batch-size limit the other batched hot paths
# use (entity children counts, data import) — the platform caps a $batch at ~1000
# subrequests, so 100 keeps each request comfortably within it (issue #703).
BATCH_CHUNK_SIZE = 100


def run_batched(
    backend: D365Backend,
    ops: Sequence[BatchOperation],
    *,
    transactional: bool = False,
    continue_on_error: bool = True,
    chunk_size: int = BATCH_CHUNK_SIZE,
) -> list[BatchResult]:
    """Execute ``ops`` via ``$batch`` in chunks, one HTTP round trip per chunk.

    Collapses an N+1 request loop into ``ceil(N / chunk_size)`` ``$batch`` POSTs,
    returning one :class:`BatchResult` per op in input order (chunks
    concatenated). An empty ``ops`` issues no request — a zero-op ``$batch`` is
    a server error, never sent.

    Defaults suit independent read/delete fan-out: ``transactional=False`` (each
    subrequest stands alone; GETs cannot ride a changeset) and
    ``continue_on_error=True`` (one failing subrequest surfaces as that result's
    ``error`` instead of aborting the batch). The caller maps each result back to
    its originating item. ``$batch`` is short-circuited under ``--dry-run``, so a
    caller that must still issue real reads there branches on ``backend.dry_run``
    before calling this.
    """
    ops_list = list(ops)
    results: list[BatchResult] = []
    for start in range(0, len(ops_list), chunk_size):
        results.extend(backend.batch(
            ops_list[start:start + chunk_size],
            transactional=transactional,
            continue_on_error=continue_on_error,
        ))
    return results


def parse_batch_file(path: str | Path) -> list[dict[str, Any]]:
    """Load a $batch JSON file and return a validated list of operation dicts."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise D365Error(f"Could not read {p}: {exc}") from exc
    try:
        data: Any = json.loads(text)
    except ValueError as exc:
        raise D365Error(f"Could not parse {p}: {exc}") from exc
    if not isinstance(data, list):
        raise D365Error(f"{p}: expected a JSON list at root, got {type(data).__name__}")

    raw_list = cast(list[Any], data)
    out: list[dict[str, Any]] = []
    for i, raw_op in enumerate(raw_list):
        if not isinstance(raw_op, dict):
            raise D365Error(f"{p} op #{i}: expected an object, got {type(raw_op).__name__}")
        op = cast(dict[str, Any], raw_op)
        method_raw = op.get("method")
        if not isinstance(method_raw, str):
            raise D365Error(f"{p} op #{i}: missing or invalid 'method'")
        method = method_raw.upper()
        if method not in _VALID_METHODS:
            raise D365Error(
                f"{p} op #{i}: invalid method {method_raw!r} "
                f"(must be one of {_VALID_METHODS})"
            )
        url = op.get("url")
        if not isinstance(url, str) or not url:
            raise D365Error(f"{p} op #{i}: missing or empty 'url'")
        body = op.get("body")
        if method in ("GET", "DELETE") and body is not None:
            raise D365Error(f"{p} op #{i}: body not allowed on {method}")
        if method in ("POST", "PATCH") and not isinstance(body, dict):
            raise D365Error(
                f"{p} op #{i}: {method} requires a JSON object 'body' "
                f"(got {type(body).__name__ if body is not None else 'missing'})"
            )
        validated: dict[str, Any] = {"method": method, "url": url}
        if body is not None:
            if not isinstance(body, dict):
                raise D365Error(f"{p} op #{i}: body must be an object")
            validated["body"] = cast(dict[str, Any], body)
        headers = op.get("headers")
        if headers is not None:
            if not isinstance(headers, dict):
                raise D365Error(f"{p} op #{i}: headers must be an object")
            headers_obj = cast(dict[str, Any], headers)
            for hk, hv in headers_obj.items():
                if not isinstance(hv, str):
                    raise D365Error(
                        f"{p} op #{i}: header {hk!r} value must be a string "
                        f"(got {type(hv).__name__})"
                    )
            validated["headers"] = headers_obj
        cid = op.get("content_id")
        if cid is not None:
            if isinstance(cid, bool):
                raise D365Error(f"{p} op #{i}: content_id must be a string or int, not bool")
            if isinstance(cid, str):
                if not cid:
                    raise D365Error(f"{p} op #{i}: content_id must be a non-empty string")
                validated["content_id"] = cid
            elif isinstance(cid, int):
                if cid <= 0:
                    raise D365Error(f"{p} op #{i}: content_id int must be positive")
                validated["content_id"] = cid
            else:
                raise D365Error(
                    f"{p} op #{i}: content_id must be a string or int, "
                    f"got {type(cid).__name__}"
                )
        out.append(validated)
    return out


def render_batch_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate counts for human-readable CLI output."""
    total = len(results)
    success = sum(1 for r in results if 200 <= int(r.get("status", 0) or 0) < 300)
    failed = total - success
    return {"total": total, "success": success, "failed": failed}
