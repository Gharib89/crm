"""Global `--fields` output-shaping contract (ADR 0023 / #735, addendum to ADR 0008).

`--fields` projects the curated `data` payload down to the named top-level keys at
the single emit seam — after ADR 0008 curation, before serialization/render — so
every command inherits it with no per-command code. Tests exercise external
behavior only: invoke through the CLI runner with a mocked backend (or drive the
public `emit` seam directly) and assert on the emitted envelope — never on shaper
internals.
"""
# pyright: basic
from __future__ import annotations

import contextlib
import io
import json

import click
import pytest
from click.testing import CliRunner

from crm.cli import CLIContext, cli

pytestmark = pytest.mark.usefixtures("isolated_home")

GUID = "00000000-0000-0000-0000-000000000001"


def _collection(*records: dict, **envelope: object) -> dict:
    return {
        "@odata.context": "https://crm.contoso.local/contoso/api/data/v9.2/$metadata#accounts",
        "value": list(records),
        **envelope,
    }


def _row(name: str = "Contoso Ltd", **extra: object) -> dict:
    return {
        "@odata.etag": 'W/"123"',
        "accountid": GUID,
        "name": name,
        "statuscode": 1,
        **extra,
    }


def _emit_json(**kwargs: object) -> dict:
    """Drive the public emit seam in JSON mode and return the parsed envelope.

    `fields` is passed as the shaping selection; any other kwarg forwards to emit.
    """
    ctx = CLIContext()
    ctx.json_mode = True
    fields = kwargs.pop("fields", None)
    if fields is not None:
        ctx.fields = list(fields)  # type: ignore[arg-type]
    dry_run = kwargs.pop("dry_run", False)
    ctx.dry_run = bool(dry_run)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with contextlib.suppress(SystemExit):
            ctx.emit(True, **kwargs)  # type: ignore[arg-type]
    return json.loads(buf.getvalue())


class TestListProjection:
    def test_rows_projected_to_named_keys(self, make_fake_backend, inject_backend):
        inject_backend(make_fake_backend(responses={"get": _collection(_row())}))
        result = CliRunner().invoke(
            cli, ["--json", "--fields", "name", "query", "odata", "accounts"]
        )
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["data"] == [{"name": "Contoso Ltd"}]

    def test_field_order_follows_flag_not_row(self, make_fake_backend, inject_backend):
        inject_backend(make_fake_backend(responses={"get": _collection(_row())}))
        result = CliRunner().invoke(
            cli, ["--json", "--fields", "name,accountid", "query", "odata", "accounts"]
        )
        env = json.loads(result.output)
        assert list(env["data"][0].keys()) == ["name", "accountid"]

    def test_missing_key_omitted_per_row(self):
        # One row has `nickname`, the other doesn't; the projection omits the key
        # from the row that lacks it rather than emitting a null.
        env = _emit_json(
            fields=["name", "nickname"],
            data=[{"name": "A", "nickname": "Ace", "drop": 1}, {"name": "B", "drop": 2}],
        )
        assert env["data"] == [{"name": "A", "nickname": "Ace"}, {"name": "B"}]


class TestDictProjection:
    def test_single_record_projected(self, make_fake_backend, inject_backend):
        single = {
            "@odata.context": "https://crm.contoso.local/contoso/api/data/v9.2/$metadata#accounts/$entity",
            "@odata.etag": 'W/"1"',
            "accountid": GUID,
            "name": "Contoso Ltd",
            "statuscode": 1,
        }
        inject_backend(make_fake_backend(responses={"get": single}))
        result = CliRunner().invoke(
            cli, ["--json", "--fields", "name", "entity", "get", "accounts", GUID]
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["data"] == {"name": "Contoso Ltd"}


class TestZeroMatchWarning:
    def test_unmatched_field_warns(self):
        env = _emit_json(fields=["name", "typo"], data=[{"name": "A"}])
        assert env["data"] == [{"name": "A"}]
        assert any("typo" in w for w in env["meta"]["warnings"])

    def test_all_matched_no_warning(self):
        env = _emit_json(fields=["name"], data=[{"name": "A"}])
        assert "meta" not in env or "warnings" not in env.get("meta", {})


class TestNonObjectPassthrough:
    def test_string_payload_passes_through_with_warning(self):
        env = _emit_json(fields=["name"], data="<formxml>...</formxml>")
        assert env["data"] == "<formxml>...</formxml>"
        assert env["meta"]["warnings"]  # non-empty advisory

    def test_list_of_scalars_passes_through_with_warning(self):
        env = _emit_json(fields=["name"], data=["a", "b"])
        assert env["data"] == ["a", "b"]
        assert env["meta"]["warnings"]

    def test_empty_list_projects_to_empty_no_warning(self):
        # A zero-row result is not a typo — project to [] without a warning.
        env = _emit_json(fields=["name"], data=[])
        assert env["data"] == []
        assert "meta" not in env or "warnings" not in env.get("meta", {})


class TestEnvelopePreserved:
    def test_meta_paging_survives_projection(self, make_fake_backend, inject_backend):
        resp = _collection(_row(), **{
            "@odata.count": 42,
            "@odata.nextLink": "https://crm.contoso.local/contoso/api/data/v9.2/accounts?$skiptoken=x",
        })
        inject_backend(make_fake_backend(responses={"get": resp}))
        result = CliRunner().invoke(
            cli, ["--json", "--fields", "name", "query", "odata", "accounts"]
        )
        env = json.loads(result.output)
        assert env["data"] == [{"name": "Contoso Ltd"}]
        # Envelope keys untouched: paging still relocated to meta.
        assert env["meta"]["count"] == 42
        assert env["meta"]["next_link"].endswith("$skiptoken=x")

    def test_preexisting_warnings_not_clobbered(self):
        env = _emit_json(fields=["typo"], data=[{"name": "A"}],
                         warnings=["pre-existing"])
        ws = env["meta"]["warnings"]
        assert "pre-existing" in ws
        assert any("typo" in w for w in ws)

    def test_unshaped_output_byte_identical(self, make_fake_backend, inject_backend):
        # No --fields → output must be exactly what it was before this feature.
        be1 = make_fake_backend(responses={"get": _collection(_row())})
        inject_backend(be1)
        base = CliRunner().invoke(cli, ["--json", "query", "odata", "accounts"])
        assert base.exit_code == 0, base.output
        assert '"name": "Contoso Ltd"' in base.output
        assert json.loads(base.output)["data"][0]["accountid"] == GUID


class TestErrorEnvelopeBypass:
    def test_error_envelope_not_shaped(self):
        ctx = CLIContext()
        ctx.json_mode = True
        ctx.fields = ["name"]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with contextlib.suppress(click.exceptions.Exit):
                ctx.emit(False, error="boom", meta={"status": 412})
        env = json.loads(buf.getvalue())
        assert env["ok"] is False
        assert env["error"] == "boom"
        # No shaping warning leaked into the reserved error meta.
        assert "warnings" not in env.get("meta", {})


class TestDryRunShaped:
    def test_dry_run_preview_is_shaped(self):
        env = _emit_json(
            fields=["would_create"],
            dry_run=True,
            data={"_dry_run": True, "would_create": {"body": {"name": "x"}},
                  "would_skip": False},
        )
        assert env["data"] == {"would_create": {"body": {"name": "x"}}}
        assert env["meta"]["dry_run"] is True


class TestHumanColumnSelection:
    def test_human_table_columns_narrowed_and_ordered(self, make_fake_backend, inject_backend):
        inject_backend(make_fake_backend(responses={"get": _collection(_row())}))
        result = CliRunner().invoke(
            cli, ["--fields", "name", "query", "odata", "accounts"]
        )
        assert result.exit_code == 0, result.output
        assert "Contoso Ltd" in result.output
        # The projected-away accountid GUID is not rendered.
        assert GUID not in result.output


class TestUsageErrors:
    def test_empty_fields_is_usage_error(self, make_fake_backend, inject_backend):
        inject_backend(make_fake_backend(responses={"get": _collection(_row())}))
        result = CliRunner().invoke(
            cli, ["--json", "--fields", "  ", "query", "odata", "accounts"]
        )
        assert result.exit_code == 2, result.output
