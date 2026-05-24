"""$batch JSON-file loader + result rendering."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from crm.utils.d365_backend import D365Error

_VALID_METHODS = ("GET", "POST", "PATCH", "DELETE")


def parse_batch_file(path: str | Path) -> list[dict[str, Any]]:
    """Load a $batch JSON file and return a validated list of operation dicts."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
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
        validated: dict[str, Any] = {"method": method, "url": url}
        if body is not None:
            if not isinstance(body, dict):
                raise D365Error(f"{p} op #{i}: body must be an object")
            validated["body"] = cast(dict[str, Any], body)
        headers = op.get("headers")
        if headers is not None:
            if not isinstance(headers, dict):
                raise D365Error(f"{p} op #{i}: headers must be an object")
            validated["headers"] = cast(dict[str, Any], headers)
        cid = op.get("content_id")
        if cid is not None:
            if not isinstance(cid, str) or not cid:
                raise D365Error(f"{p} op #{i}: content_id must be a non-empty string")
            validated["content_id"] = cid
        out.append(validated)
    return out


def render_batch_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate counts for human-readable CLI output."""
    total = len(results)
    success = sum(1 for r in results if 200 <= int(r.get("status", 0) or 0) < 300)
    failed = total - success
    return {"total": total, "success": success, "failed": failed}
