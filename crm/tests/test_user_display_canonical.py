# pyright: basic
"""Canonical flag spellings with hidden back-compat aliases (#712).

Three flag-naming inconsistencies, each fixed the same way — a visible canonical
spelling plus a **hidden** alias (identical behavior, off the public catalogue):

- `chart` verbs: `--user-owned` is canonical; `--user` (the old boolean) stays a
  hidden alias. (Disambiguates it from fieldsec's GUID-valued `--user`.)
- `fieldsec assign`: `--user-id` is canonical; `--user` (the old GUID) stays a
  hidden alias.
- `webresource create/update`: `--display` is canonical (matching metadata and
  solution); `--display-name` stays a hidden alias.
"""

from __future__ import annotations

import json

import requests_mock as rm_module
from click.testing import CliRunner

from crm.cli import cli

_PROFILE_ID = "11112222-3333-4444-5555-666677778888"
_USER_ID = "aaaa1111-2222-3333-4444-555566667777"
_WR_ID = "22223333-4444-5555-6666-777788889999"


def _use_backend(monkeypatch, backend) -> None:
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)


# --- chart: --user-owned canonical, --user hidden alias -----------------------


def _chart_list_url(flag: str | None, backend, monkeypatch) -> str:
    """Invoke `chart list` with an optional flag; return the URL that was hit."""
    _use_backend(monkeypatch, backend)
    args = ["--json", "chart", "list", "new_project"]
    if flag is not None:
        args.append(flag)
    with rm_module.Mocker() as m:
        m.get(backend.url_for("userqueryvisualizations"), json={"value": []})
        m.get(backend.url_for("savedqueryvisualizations"), json={"value": []})
        result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    return m.last_request.url


def test_chart_user_owned_canonical_hits_userset(backend, monkeypatch):
    assert "userqueryvisualizations" in _chart_list_url("--user-owned", backend, monkeypatch)


def test_chart_user_alias_identical(backend, monkeypatch):
    assert "userqueryvisualizations" in _chart_list_url("--user", backend, monkeypatch)


def test_chart_default_is_system_charts(backend, monkeypatch):
    # Neither flag → system charts, unchanged.
    assert "savedqueryvisualizations" in _chart_list_url(None, backend, monkeypatch)


# --- fieldsec assign: --user-id canonical, --user hidden alias ----------------


def _assign_principal_type(flag: str, backend, monkeypatch) -> str:
    _use_backend(monkeypatch, backend)
    ref_url = backend.url_for(
        f"fieldsecurityprofiles({_PROFILE_ID})/systemuserprofiles_association/$ref"
    )
    with rm_module.Mocker() as m:
        m.post(ref_url, status_code=204)
        result = CliRunner().invoke(
            cli, ["--json", "fieldsec", "assign", _PROFILE_ID, flag, _USER_ID]
        )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["data"]["principal_type"]


def test_fieldsec_user_id_canonical_assigns_user(backend, monkeypatch):
    assert _assign_principal_type("--user-id", backend, monkeypatch) == "user"


def test_fieldsec_user_alias_identical(backend, monkeypatch):
    assert _assign_principal_type("--user", backend, monkeypatch) == "user"


# --- webresource create: --display canonical, --display-name hidden alias -----


def _create_display_name(flag: str, monkeypatch) -> str:
    captured: dict = {}
    monkeypatch.setattr(
        "crm.core.webresource.create_webresource",
        lambda backend, **kw: (
            captured.update(kw) or {"created": True, "webresourceid": _WR_ID, "name": kw["name"]}
        ),
    )
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    monkeypatch.setattr("crm.core.solution.publish_all", lambda b: {"ok": True})
    runner = CliRunner()
    with runner.isolated_filesystem():
        with open("foo.js", "w", encoding="utf-8") as fh:
            fh.write("x()")
        result = runner.invoke(
            cli,
            [
                "--json",
                "webresource",
                "create",
                "--name",
                "cwx_/foo.js",
                "--file",
                "foo.js",
                flag,
                "Pretty",
                "--no-publish",
                "--solution",
                "cwx_sol",
            ],
        )
    assert result.exit_code == 0, result.output
    return captured["display_name"]


def test_webresource_display_canonical(monkeypatch):
    assert _create_display_name("--display", monkeypatch) == "Pretty"


def test_webresource_display_name_alias_identical(monkeypatch):
    assert _create_display_name("--display-name", monkeypatch) == "Pretty"


# --- vocabulary regression: catalogue advertises only the canonical spellings -


def _catalogue() -> dict:
    result = CliRunner().invoke(cli, ["--json", "describe"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["data"]


def _opts(cmd: dict) -> set:
    return {o for p in cmd["params"] for o in p["opts"]}


def test_canonical_flags_are_the_only_visible_spellings():
    """Derived from the `describe` catalogue: the hidden aliases (`--user`,
    `--display-name`) never surface, and each canonical spelling appears on
    exactly the verbs it belongs to.
    """
    data = _catalogue()
    by_path = {c["path"]: c for c in data["commands"]}

    # Hidden aliases are off the catalogue everywhere.
    for cmd in data["commands"]:
        assert "--user" not in _opts(cmd), f"{cmd['path']} exposes hidden --user"
        assert "--display-name" not in _opts(cmd), f"{cmd['path']} exposes hidden --display-name"

    # `--user-owned` is the canonical boolean on every chart verb that had `--user`.
    chart_user_verbs = [
        "chart list",
        "chart get",
        "chart delete",
        "chart create",
        "chart update",
        "chart set-fetch",
        "chart add-series",
        "chart remove-series",
        "chart set-groupby",
    ]
    for path in chart_user_verbs:
        assert "--user-owned" in _opts(by_path[path]), f"{path} missing --user-owned"

    # `--user-id` is the canonical GUID flag on fieldsec assign.
    assert "--user-id" in _opts(by_path["fieldsec assign"])

    # `--display` is the canonical display-name flag on the webresource verbs.
    for path in ["webresource create", "webresource update"]:
        assert "--display" in _opts(by_path[path]), f"{path} missing --display"
