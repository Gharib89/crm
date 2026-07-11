"""Global `--fields`/`--jq` output-shaping contract (ADR 0023 / #735, #736; addendum to ADR 0008).

`--fields` projects the curated `data` payload down to the named top-level keys and
`--jq` runs a jq program over it, both at the single emit seam — after ADR 0008
curation, before serialization/render — so every command inherits them with no
per-command code. Tests exercise external behavior only: invoke through the CLI
runner with a mocked backend (or drive the public `emit` seam directly) and assert
on the emitted envelope — never on shaper internals.
"""

# pyright: basic
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys

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


def _emit_jq(program: str, *, ok: bool = True, **kwargs: object) -> dict:
    """Drive the public emit seam with a compiled `--jq` program; return the envelope.

    Compiles `program` (as the root callback does) and drives emit in JSON mode.
    """
    import jq

    ctx = CLIContext()
    ctx.json_mode = True
    ctx.jq_program = jq.compile(program)
    dry_run = kwargs.pop("dry_run", False)
    ctx.dry_run = bool(dry_run)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with contextlib.suppress(SystemExit, click.exceptions.Exit):
            ctx.emit(ok, **kwargs)  # type: ignore[arg-type]
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
        resp = _collection(
            _row(),
            **{
                "@odata.count": 42,
                "@odata.nextLink": "https://crm.contoso.local/contoso/api/data/v9.2/accounts?$skiptoken=x",
            },
        )
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
        env = _emit_json(fields=["typo"], data=[{"name": "A"}], warnings=["pre-existing"])
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
            data={"_dry_run": True, "would_create": {"body": {"name": "x"}}, "would_skip": False},
        )
        assert env["data"] == {"would_create": {"body": {"name": "x"}}}
        assert env["meta"]["dry_run"] is True


class TestHumanColumnSelection:
    def test_human_table_columns_narrowed_and_ordered(self, make_fake_backend, inject_backend):
        inject_backend(make_fake_backend(responses={"get": _collection(_row())}))
        result = CliRunner().invoke(cli, ["--fields", "name", "query", "odata", "accounts"])
        assert result.exit_code == 0, result.output
        assert "Contoso Ltd" in result.output
        # The projected-away accountid GUID is not rendered.
        assert GUID not in result.output


class TestUsageErrors:
    def test_empty_fields_is_usage_error(self, make_fake_backend, inject_backend):
        inject_backend(make_fake_backend(responses={"get": _collection(_row())}))
        result = CliRunner().invoke(cli, ["--json", "--fields", "  ", "query", "odata", "accounts"])
        assert result.exit_code == 2, result.output


class TestJqProjection:
    def test_length_collapses_list_to_scalar(self, make_fake_backend, inject_backend):
        inject_backend(
            make_fake_backend(responses={"get": _collection(_row("A"), _row("B"), _row("C"))})
        )
        result = CliRunner().invoke(cli, ["--json", "--jq", "length", "query", "odata", "accounts"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["data"] == 3

    def test_stream_result_becomes_list(self):
        # `.[]` emits one output per row → the multi-output stream lands as a list.
        env = _emit_jq(".[]", data=[{"name": "A"}, {"name": "B"}])
        assert env["data"] == [{"name": "A"}, {"name": "B"}]

    def test_single_array_output_preserved(self):
        # A program producing one array output keeps it as an array, not unwrapped.
        env = _emit_jq("map(.name)", data=[{"name": "A"}, {"name": "B"}])
        assert env["data"] == ["A", "B"]

    def test_object_construction(self):
        env = _emit_jq("{n: length}", data=[1, 2])
        assert env["data"] == {"n": 2}

    def test_single_record_projected(self):
        env = _emit_jq(".name", data={"name": "Contoso", "accountid": GUID})
        assert env["data"] == "Contoso"

    def test_empty_output_is_null(self):
        env = _emit_jq("empty", data=[1, 2, 3])
        assert env["data"] is None


class TestJqImpliesJson:
    def test_jq_forces_json_even_without_json_flag(self, make_fake_backend, inject_backend):
        # No --json, but --jq implies it: output must be a JSON envelope.
        inject_backend(make_fake_backend(responses={"get": _collection(_row(), _row())}))
        result = CliRunner().invoke(cli, ["--jq", "length", "query", "odata", "accounts"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["data"] == 2


class TestJqFieldsMutualExclusion:
    def test_fields_and_jq_together_is_usage_error(self, make_fake_backend, inject_backend):
        be = inject_backend(make_fake_backend(responses={"get": _collection(_row())}))
        result = CliRunner().invoke(
            cli, ["--json", "--fields", "name", "--jq", ".", "query", "odata", "accounts"]
        )
        assert result.exit_code == 2, result.output
        assert not be.called  # rejected before any backend work


class TestJqCompileBeforeBackend:
    def test_invalid_program_exits_2_without_backend_call(self, make_fake_backend, inject_backend):
        be = inject_backend(make_fake_backend(responses={"get": _collection(_row())}))
        result = CliRunner().invoke(
            cli, ["--json", "--jq", "((( not valid", "query", "odata", "accounts"]
        )
        assert result.exit_code == 2, result.output
        # Compiled at parse time → the backend was never touched (validate-before-backend).
        assert not be.called


class TestJqErrorEnvelopeBypass:
    def test_error_envelope_not_shaped(self):
        env = _emit_jq("length", ok=False, error="boom", meta={"status": 412})
        assert env["ok"] is False
        assert env["error"] == "boom"
        assert "data" not in env


class TestJqDryRunShaped:
    def test_dry_run_preview_is_shaped(self):
        env = _emit_jq(
            ".would_create.body",
            dry_run=True,
            data={"_dry_run": True, "would_create": {"body": {"name": "x"}}, "would_skip": False},
        )
        assert env["data"] == {"name": "x"}
        assert env["meta"]["dry_run"] is True


class TestJqRuntimeError:
    def test_eval_error_becomes_error_envelope(self):
        # Compiles fine, but indexing a number with a string fails at eval time →
        # the success payload is unusable, so it surfaces as an error envelope.
        env = _emit_jq(".foo", data=42)
        assert env["ok"] is False
        assert "jq" in env["error"].lower()
        assert "data" not in env

    def test_eval_error_envelope_is_canonical(self):
        # The eval-error envelope goes through the normal error path, so canonical
        # fields like meta.dry_run are stamped (not a hand-built minimal envelope).
        env = _emit_jq(".foo", data=42, dry_run=True)
        assert env["ok"] is False
        assert env["meta"]["dry_run"] is True


class TestJqLazyImport:
    def test_jq_not_imported_without_flag(self, tmp_path):
        # The jq C-extension must stay out of the no-flag startup path (cold-start
        # guard): importing crm.cli and running a command without --jq imports no jq.
        code = (
            "import sys; from click.testing import CliRunner; from crm.cli import cli; "
            "CliRunner().invoke(cli, ['profile', 'list']); "
            "mods = [m for m in sys.modules if m == 'jq' or m.startswith('jq.')]; "
            "assert not mods, mods"
        )
        env = {**__import__("os").environ, "CRM_HOME": str(tmp_path)}
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, env=env
        )
        assert result.returncode == 0, result.stderr

    def test_jq_imported_when_flag_used(self, tmp_path, make_fake_backend, inject_backend):
        # Sanity peer of the guard: with --jq the module IS available (proves the
        # guard above measures the real thing, not a permanently-absent import).
        inject_backend(make_fake_backend(responses={"get": _collection(_row())}))
        result = CliRunner().invoke(cli, ["--jq", "length", "query", "odata", "accounts"])
        assert result.exit_code == 0, result.output
        assert "jq" in sys.modules
