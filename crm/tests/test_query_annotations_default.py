"""--annotations defaults to True across all query/get verbs (#358).

`query odata`/`query fetchxml` previously defaulted False while
`query saved`/`query user`/`entity get` defaulted True, so moving a filter
between commands silently dropped formatted values. The default is now True
everywhere; opting out still works via --no-annotations.

These assert the *request* behavior (the Prefer header), not the rendered
output, since that is what the default actually controls.
"""
# pyright: basic
from __future__ import annotations

from click.testing import CliRunner

from crm.cli import cli

_PREFER = 'odata.include-annotations="*"'


def _prefer_header(backend) -> str | None:
    """The Prefer header sent on the last `get` call, if any."""
    _verb, _path, kwargs = backend.calls[-1]
    headers = kwargs.get("extra_headers") or {}
    return headers.get("Prefer")


class TestOdataDefault:
    def test_default_requests_annotations(self, make_fake_backend, inject_backend):
        b = inject_backend(make_fake_backend(responses={"get": {"value": []}}))
        result = CliRunner().invoke(cli, ["--json", "query", "odata", "accounts"])
        assert result.exit_code == 0, result.output
        assert _prefer_header(b) == _PREFER

    def test_no_annotations_opts_out(self, make_fake_backend, inject_backend):
        b = inject_backend(make_fake_backend(responses={"get": {"value": []}}))
        result = CliRunner().invoke(
            cli, ["--json", "query", "odata", "accounts", "--no-annotations"]
        )
        assert result.exit_code == 0, result.output
        assert _prefer_header(b) is None


class TestFetchxmlDefault:
    _FETCH = "<fetch><entity name='account'/></fetch>"

    def test_default_requests_annotations(self, make_fake_backend, inject_backend):
        b = inject_backend(make_fake_backend(responses={"get": {"value": []}}))
        result = CliRunner().invoke(
            cli, ["--json", "query", "fetchxml", "accounts", "--xml", self._FETCH]
        )
        assert result.exit_code == 0, result.output
        assert _prefer_header(b) == _PREFER

    def test_no_annotations_opts_out(self, make_fake_backend, inject_backend):
        b = inject_backend(make_fake_backend(responses={"get": {"value": []}}))
        result = CliRunner().invoke(
            cli,
            ["--json", "query", "fetchxml", "accounts", "--xml", self._FETCH,
             "--no-annotations"],
        )
        assert result.exit_code == 0, result.output
        assert _prefer_header(b) is None
