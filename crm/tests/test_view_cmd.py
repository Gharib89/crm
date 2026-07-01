# pyright: basic
"""Offline unit tests for crm/commands/view.py — _parse_width, view_edit_columns,
view_set_order."""
import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.commands.view import _parse_order, _parse_width
from crm.core import views as views_mod


# ---------------------------------------------------------------------------
# _parse_width — pure helper, tested directly (lines 82-94)
# ---------------------------------------------------------------------------

def test_parse_width_valid():
    assert _parse_width("fullname:150") == ("fullname", 150)


def test_parse_width_strips_whitespace():
    assert _parse_width(" name : 80 ") == ("name", 80)


def test_parse_width_missing_colon_raises():
    import click
    with pytest.raises(click.BadParameter, match="logical:int"):
        _parse_width("fullname")


def test_parse_width_empty_name_raises():
    import click
    with pytest.raises(click.BadParameter, match="logical:int"):
        _parse_width(":100")


def test_parse_width_non_int_raises():
    import click
    with pytest.raises(click.BadParameter, match="must be an int"):
        _parse_width("fullname:big")


def test_parse_width_zero_raises():
    import click
    with pytest.raises(click.BadParameter, match="must be positive"):
        _parse_width("fullname:0")


def test_parse_width_negative_raises():
    import click
    with pytest.raises(click.BadParameter, match="must be positive"):
        _parse_width("fullname:-5")


# ---------------------------------------------------------------------------
# _parse_order — lines 63-79 (71, 72->78, 75 uncovered)
# ---------------------------------------------------------------------------

def test_parse_order_single_token():
    assert _parse_order("createdon") == ("createdon", False)


def test_parse_order_asc():
    assert _parse_order("createdon asc") == ("createdon", False)


def test_parse_order_desc():
    assert _parse_order("createdon desc") == ("createdon", True)


def test_parse_order_bad_direction():
    import click
    with pytest.raises(click.UsageError, match="asc|desc"):
        _parse_order("createdon sideways")


def test_parse_order_too_many_tokens():
    import click
    with pytest.raises(click.UsageError):
        _parse_order("a b c")


# ---------------------------------------------------------------------------
# view edit-columns — lines 180-193
# ---------------------------------------------------------------------------

def test_edit_columns_nothing_to_do_is_usage_error(monkeypatch):
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(
        cli, ["view", "edit-columns", "account", "My View"])
    assert res.exit_code == 2
    assert "nothing to do" in res.output


def test_edit_columns_reorder_with_add_is_usage_error(monkeypatch):
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "view", "edit-columns", "account", "My View",
        "--reorder", "name,createdon",
        "--add", "statecode",
    ])
    assert res.exit_code == 2
    assert "reorder" in res.output.lower()


def test_edit_columns_bad_width_spec_is_bad_param(monkeypatch):
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "view", "edit-columns", "account", "My View",
        "--width", "name",          # missing colon → BadParameter
    ])
    assert res.exit_code != 0


def test_edit_columns_add_calls_core(monkeypatch):
    called = {}

    def fake_edit(backend, *, entity, view, query_type, add, remove,
                  width, reorder, solution, publish):
        called.update(entity=entity, view=view, add=add, remove=remove,
                      width=width)
        return {"savedqueryid": "1111" * 8, "name": view, "columns_added": 1}

    monkeypatch.setattr(views_mod, "edit_view_columns", fake_edit)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "view", "edit-columns", "account", "My View",
        "--add", "statecode:120", "--solution", "TestSol",
    ])
    assert res.exit_code == 0, res.output
    assert called["entity"] == "account"
    assert called["view"] == "My View"
    assert called["add"] == [("statecode", 120)]
    assert called["remove"] == []
    assert called["width"] == []


def test_edit_columns_remove_calls_core(monkeypatch):
    called = {}

    def fake_edit(backend, *, entity, view, query_type, add, remove,
                  width, reorder, solution, publish):
        called.update(remove=remove)
        return {"savedqueryid": "1111" * 8, "name": view}

    monkeypatch.setattr(views_mod, "edit_view_columns", fake_edit)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "view", "edit-columns", "account", "My View",
        "--remove", "statecode", "--solution", "TestSol",
    ])
    assert res.exit_code == 0, res.output
    assert called["remove"] == ["statecode"]


def test_edit_columns_width_calls_core(monkeypatch):
    called = {}

    def fake_edit(backend, *, entity, view, query_type, add, remove,
                  width, reorder, solution, publish):
        called.update(width=width)
        return {"savedqueryid": "1111" * 8, "name": view}

    monkeypatch.setattr(views_mod, "edit_view_columns", fake_edit)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "view", "edit-columns", "account", "My View",
        "--width", "name:200", "--solution", "TestSol",
    ])
    assert res.exit_code == 0, res.output
    assert called["width"] == [("name", 200)]


def test_edit_columns_reorder_calls_core(monkeypatch):
    called = {}

    def fake_edit(backend, *, entity, view, query_type, add, remove,
                  width, reorder, solution, publish):
        called.update(reorder=reorder)
        return {"savedqueryid": "1111" * 8, "name": view}

    monkeypatch.setattr(views_mod, "edit_view_columns", fake_edit)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "view", "edit-columns", "account", "My View",
        "--reorder", "name , createdon , statecode", "--solution", "TestSol",
    ])
    assert res.exit_code == 0, res.output
    assert called["reorder"] == ["name", "createdon", "statecode"]


# ---------------------------------------------------------------------------
# view set-order — lines 302-312
# ---------------------------------------------------------------------------

def test_set_order_nothing_to_do_is_usage_error(monkeypatch):
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(
        cli, ["view", "set-order", "account", "My View"])
    assert res.exit_code == 2
    assert "nothing to do" in res.output


def test_set_order_bad_order_spec_errors(monkeypatch):
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "view", "set-order", "account", "My View",
        "--order", "name baddir",   # three parts → UsageError
    ])
    assert res.exit_code != 0


def test_set_order_calls_core(monkeypatch):
    called = {}

    def fake_set(backend, *, entity, view, query_type, order, add_order,
                 clear_order, solution, publish):
        called.update(entity=entity, view=view, order=order,
                      add_order=add_order, clear_order=clear_order)
        return {"savedqueryid": "1111" * 8, "name": view}

    monkeypatch.setattr(views_mod, "set_view_order", fake_set)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "view", "set-order", "account", "My View",
        "--order", "createdon desc", "--solution", "TestSol",
    ])
    assert res.exit_code == 0, res.output
    assert called["entity"] == "account"
    assert called["order"] == [("createdon", True)]
    assert called["add_order"] == []
    assert called["clear_order"] is False


def test_set_order_add_order_calls_core(monkeypatch):
    called = {}

    def fake_set(backend, *, entity, view, query_type, order, add_order,
                 clear_order, solution, publish):
        called.update(add_order=add_order)
        return {"savedqueryid": "1111" * 8, "name": view}

    monkeypatch.setattr(views_mod, "set_view_order", fake_set)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "view", "set-order", "account", "My View",
        "--add-order", "name asc", "--solution", "TestSol",
    ])
    assert res.exit_code == 0, res.output
    assert called["add_order"] == [("name", False)]


def test_set_order_clear_order_calls_core(monkeypatch):
    called = {}

    def fake_set(backend, *, entity, view, query_type, order, add_order,
                 clear_order, solution, publish):
        called.update(clear_order=clear_order)
        return {"savedqueryid": "1111" * 8, "name": view}

    monkeypatch.setattr(views_mod, "set_view_order", fake_set)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    res = CliRunner().invoke(cli, [
        "--json", "view", "set-order", "account", "My View",
        "--clear-order", "--solution", "TestSol",
    ])
    assert res.exit_code == 0, res.output
    assert called["clear_order"] is True
