"""Validation-before-backend bad-argument sites are usage errors (exit 2).

ADR 0001 fixes the contract to 0 = success / 1 = operational failure / 2 = Click
usage error. A caller input mistake caught *before any backend call* — mutually
exclusive or mutually required flags, a required-one-of choice, a malformed
argument value, an unknown local target — is a usage error and must exit 2 with
the usage-error envelope (no ``meta``), matching the existing Click-layer usage
errors. Historically several such sites emitted ``ok:false``/exit 1 (the
transport-failure shape), which made agents misclassify a caller bug as a server
error (issue #713).

Transport/platform failures on the same commands are unchanged: they still exit
1 with the ``{ok:false, ..., meta}`` envelope — proven by the regression test at
the bottom and by ``test_exit_codes.py::test_d365_server_error_exits_1``.
"""

# pyright: basic
from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from crm.cli import cli
from crm.utils.d365_backend import D365Error


def _run(args, **kwargs):
    return CliRunner().invoke(cli, args, **kwargs)


def _assert_usage_error(result, needle: str = ""):
    """A usage error: exit 2, parseable ``{ok:false}`` envelope, no ``meta`` key
    (ADR 0001 renders usage errors without meta), and ``needle`` in the message.
    """
    assert result.exit_code == 2, result.output
    env = json.loads(result.output)
    assert env["ok"] is False
    assert "meta" not in env
    assert needle in env["error"]
    assert "Traceback" not in result.output


# --- action ---------------------------------------------------------------- #
def test_action_function_bind_id_without_bind_set_exits_2():
    _assert_usage_error(
        _run(["--json", "action", "function", "WhoAmI", "--bind-id", "x"]),
        "--bind-id requires --bind-set",
    )


def test_action_invoke_bind_flags_not_paired_exits_2():
    _assert_usage_error(
        _run(["--json", "action", "invoke", "foo", "--bind-set", "workflows"]),
        "--bind-set and --bind-id must be used together",
    )


def test_action_function_non_object_params_exits_2():
    _assert_usage_error(
        _run(["--json", "action", "function", "WhoAmI", "--params", "[1, 2]"]),
        "--params must be a JSON object",
    )


def test_action_function_malformed_params_json_exits_2():
    _assert_usage_error(
        _run(["--json", "action", "function", "WhoAmI", "--params", "{bad"]),
    )


# --- query ------------------------------------------------------------------ #
def test_query_fetchxml_xml_and_file_exits_2(tmp_path: Path):
    f = tmp_path / "q.xml"
    f.write_text("<fetch/>", encoding="utf-8")
    _assert_usage_error(
        _run(["--json", "query", "fetchxml", "--xml", "<fetch/>", "--file", str(f)]),
        "Provide --xml or --file, not both",
    )


def test_query_fetchxml_neither_xml_nor_file_exits_2():
    _assert_usage_error(
        _run(["--json", "query", "fetchxml"]),
        "Either --xml or --file is required",
    )


def test_query_fetchxml_empty_file_is_operational_failure(tmp_path: Path):
    """An empty --file is a file-content problem, not a bad argument: it keeps the
    exit-1 clean envelope, not the exit-2 usage error — the flag *was* provided, so
    'required' would misclassify it.
    """
    f = tmp_path / "empty.xml"
    f.write_text("", encoding="utf-8")
    result = _run(["--json", "query", "fetchxml", "--file", str(f)])
    assert result.exit_code == 1, result.output
    env = json.loads(result.output)
    assert env["ok"] is False
    assert "empty" in env["error"]


# --- solution --------------------------------------------------------------- #
def test_solution_create_both_publisher_forms_exits_2():
    _assert_usage_error(
        _run(
            [
                "--json",
                "solution",
                "create",
                "--name",
                "sol",
                "--publisher",
                "p",
                "--publisher-id",
                "00000000-0000-0000-0000-000000000000",
            ]
        ),
        "Provide exactly one of --publisher or --publisher-id",
    )


def test_solution_create_no_publisher_exits_2():
    _assert_usage_error(
        _run(["--json", "solution", "create", "--name", "sol"]),
        "Provide exactly one of --publisher or --publisher-id",
    )


def test_solution_publish_xml_and_file_exits_2(tmp_path: Path):
    f = tmp_path / "p.xml"
    f.write_text("<importexportxml/>", encoding="utf-8")
    _assert_usage_error(
        _run(["--json", "solution", "publish", "--xml", "<x/>", "--xml-file", str(f)]),
        "Provide --xml or --xml-file, not both",
    )


def test_solution_publish_neither_xml_nor_file_exits_2():
    _assert_usage_error(
        _run(["--json", "solution", "publish"]),
        "Either --xml or --xml-file is required",
    )


def test_solution_publish_empty_file_is_operational_failure(tmp_path: Path):
    """An empty --xml-file is a file-content problem → exit-1 clean envelope, not
    the exit-2 usage error (the flag was provided).
    """
    f = tmp_path / "empty.xml"
    f.write_text("", encoding="utf-8")
    result = _run(["--json", "solution", "publish", "--xml-file", str(f)])
    assert result.exit_code == 1, result.output
    env = json.loads(result.output)
    assert env["ok"] is False
    assert "empty" in env["error"]


# --- describe --------------------------------------------------------------- #
def test_describe_unknown_group_exits_2():
    _assert_usage_error(
        _run(["--json", "describe", "no-such-group"]),
        "No such command",
    )


# --- regression: transport failure on a converted command stays exit 1 ------ #
def test_converted_command_transport_failure_still_exits_1(
    make_fake_backend, inject_backend, isolated_home
):
    """A backend (transport) failure on a converted command keeps the exit-1
    ``ok:false``/``meta`` envelope — the conversion only reclassifies the
    validate-before-backend path, never the operational-failure path.
    """
    inject_backend(
        make_fake_backend(errors={"get": D365Error("Boom", status=500, code="0x80040216")})
    )
    result = _run(["--json", "action", "function", "WhoAmI"])
    assert result.exit_code == 1, result.output
    env = json.loads(result.output)
    assert env["ok"] is False
    assert env["meta"]["status"] == 500
