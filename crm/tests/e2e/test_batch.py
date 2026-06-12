# pyright: basic
"""E2E tests for the batch command."""
from __future__ import annotations

import json

from crm.tests.e2e.coverage import covers


@covers("batch")
def test_batch_read_operations(cli, tmp_path):
    """A batch of two GET read operations completes successfully.

    WhoAmI() is a zero-side-effect function; fetching one contact (top=1) is
    equally safe. Both ops use absolute paths so no OData prefix is needed.
    The batch command takes a JSON file of operation dicts with method + url.
    """
    ops = [
        {"method": "GET", "url": "WhoAmI()"},
        {"method": "GET", "url": "contacts?$top=1&$select=fullname"},
    ]
    batch_file = tmp_path / "batch_ops.json"
    batch_file.write_text(json.dumps(ops), encoding="utf-8")

    result = cli(["--json", "batch", str(batch_file)])
    data = json.loads(result.stdout)
    assert data["ok"] is True, f"batch command failed: {data}"
    # data is a list of BatchResult dicts; each carries a 'status' code
    results = data["data"]
    assert isinstance(results, list), f"expected list of results, got: {type(results)}"
    assert len(results) == 2, f"expected 2 results, got {len(results)}: {results}"
    for r in results:
        status = int(r.get("status", 0))
        assert 200 <= status < 300, f"batch op returned non-2xx status: {r}"
