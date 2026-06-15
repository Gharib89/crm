# pyright: basic
"""Mapping tests for the machine-readable D365 error taxonomy (issue #62)."""
import json

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.utils.d365_backend import D365Error, classify_d365_error


@pytest.mark.parametrize(
    "status, code, message, expected",
    [
        # Status-less transport path carries a machine signal.
        (None, None, "HTTP transport failure: Connection refused", ("transport_error", True)),
        (None, None, "HTTP transport failure on $batch: timed out", ("transport_error", True)),
        # Status-less client-side validation (many core helpers raise D365Error
        # with no status) must NOT be mistaken for a retryable transport failure.
        (None, None, "entity logical name is required", ("validation", False)),
        (None, None, "--top must be >= 1", ("validation", False)),
        (401, None, "Unauthorized", ("auth_failed", False)),
        (403, "0x80040220", "Insufficient privileges", ("forbidden", False)),
        # not_found by status and by D365 code.
        (404, "0x80040217", "Record Not Found", ("not_found", False)),
        (200, "0x80040217", "Does Not Exist", ("not_found", False)),
        (412, "0x80048d04", "Concurrency mismatch", ("concurrency_conflict", True)),
        (429, None, "Too Many Requests", ("throttled", True)),
        # duplicate_detected wins over a 412 status when the duplicate code rides on it.
        (412, "0x80040237", "A duplicate record exists", ("duplicate_detected", False)),
        # appmodule uniquename collision rides its own code; same category (issue #322).
        (400, "0x80050135", "A duplicate uniquename exists", ("duplicate_detected", False)),
        (500, None, "Internal Server Error", ("server_error", True)),
        (503, None, "Service Unavailable", ("server_error", True)),
        (400, None, "Bad payload", ("validation", False)),
    ],
)
def test_classify_maps_each_category(status, code, message, expected):
    assert classify_d365_error(status, code, message) == expected


def test_envelope_carries_category_and_retryable(make_fake_backend, inject_backend, isolated_home):
    """The JSON error envelope additively gains meta.category + meta.retryable,
    without dropping the existing meta.status / meta.code keys."""
    inject_backend(make_fake_backend(errors={"get": D365Error("Record Not Found", status=404, code="0x80040217")}))
    result = CliRunner().invoke(cli, ["--json", "query", "count", "account"])
    assert result.exit_code == 1, result.output
    meta = json.loads(result.output)["meta"]
    assert meta["status"] == 404
    assert meta["code"] == "0x80040217"
    assert meta["category"] == "not_found"
    assert meta["retryable"] is False
