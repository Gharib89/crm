# pyright: basic
"""Unit tests for crm.core.webresource.

The webresource_webresourcetype map (CSS=2, Silverlight/XAP=8, SVG=11, etc.) is
verified against MS Learn's webresource entity reference. The issue text's
"8=CSS" was wrong; the map here is correct.
"""
from __future__ import annotations

import base64

import pytest
import requests_mock

from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice", api_version="v9.2", verify_ssl=False,
    )


@pytest.fixture
def backend(profile):
    return D365Backend(profile, password="pw", dry_run=False)


_WR_ID = "99999999-9999-9999-9999-999999999999"


def _posts(m):
    return [r for r in m.request_history if r.method == "POST"]


def _patches(m):
    return [r for r in m.request_history if r.method == "PATCH"]


class TestResolveWebresourcetype:
    @pytest.mark.parametrize(
        "fname,expected",
        [
            ("foo.html", 1),
            ("foo.htm", 1),
            ("foo.css", 2),
            ("foo.js", 3),
            ("foo.xml", 4),
            ("foo.png", 5),
            ("foo.jpg", 6),
            ("foo.jpeg", 6),
            ("foo.gif", 7),
            ("foo.xap", 8),
            ("foo.xsl", 9),
            ("foo.xslt", 9),
            ("foo.ico", 10),
            ("foo.svg", 11),
            ("foo.resx", 12),
        ],
    )
    def test_maps_by_extension(self, fname, expected):
        from crm.core import webresource
        assert webresource.resolve_webresourcetype(fname) == expected

    def test_extension_is_case_insensitive(self):
        from crm.core import webresource
        assert webresource.resolve_webresourcetype("FOO.PNG") == 5
        assert webresource.resolve_webresourcetype("Bar.Js") == 3

    def test_unknown_extension_raises(self):
        from crm.core import webresource
        with pytest.raises(D365Error, match="Cannot infer web resource type"):
            webresource.resolve_webresourcetype("foo.foo")

    def test_override_wins_over_extension(self):
        from crm.core import webresource
        # .css would map to 2, but the explicit override takes precedence
        assert webresource.resolve_webresourcetype("foo.css", 3) == 3

    def test_override_wins_for_unknown_extension(self):
        from crm.core import webresource
        assert webresource.resolve_webresourcetype("foo.unknown", 7) == 7


class TestCreateWebresource:
    def test_create_posts_base64_content_and_fields(self, backend):
        from crm.core import webresource
        raw = b"body{color:red}"
        wr_url = backend.url_for(f"webresourceset({_WR_ID})")
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("webresourceset"), status_code=204,
                   headers={"OData-EntityId": wr_url})
            out = webresource.create_webresource(
                backend, name="new_style.css", content=raw, webresourcetype=2,
            )
        assert out["created"] is True
        assert out["webresourceid"] == _WR_ID
        assert out["webresourcetype"] == 2
        body = _posts(m)[0].json()
        assert body["name"] == "new_style.css"
        assert body["webresourcetype"] == 2
        assert body["content"] == base64.b64encode(raw).decode("ascii")
        # displayname defaults to name when display_name omitted
        assert body["displayname"] == "new_style.css"

    def test_create_uses_display_name_when_given(self, backend):
        from crm.core import webresource
        wr_url = backend.url_for(f"webresourceset({_WR_ID})")
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("webresourceset"), status_code=204,
                   headers={"OData-EntityId": wr_url})
            webresource.create_webresource(
                backend, name="new_style.css", content=b"x", webresourcetype=2,
                display_name="Pretty Style",
            )
        body = _posts(m)[0].json()
        assert body["displayname"] == "Pretty Style"

    def test_create_routes_solution_header(self, backend):
        from crm.core import webresource
        wr_url = backend.url_for(f"webresourceset({_WR_ID})")
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("webresourceset"), status_code=204,
                   headers={"OData-EntityId": wr_url})
            out = webresource.create_webresource(
                backend, name="new_x.js", content=b"x", webresourcetype=3,
                solution="cwx_sol",
            )
        post = _posts(m)[0]
        assert post.headers["MSCRM.SolutionUniqueName"] == "cwx_sol"
        assert out["solution"] == "cwx_sol"

    def test_create_empty_name_raises(self, backend):
        from crm.core import webresource
        with pytest.raises(D365Error):
            webresource.create_webresource(
                backend, name="", content=b"x", webresourcetype=2)

    def test_create_publishes_when_requested(self, backend):
        from crm.core import webresource
        wr_url = backend.url_for(f"webresourceset({_WR_ID})")
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("webresourceset"), status_code=204,
                   headers={"OData-EntityId": wr_url})
            m.post(backend.url_for("PublishAllXml"), status_code=204)
            out = webresource.create_webresource(
                backend, name="new_x.js", content=b"x", webresourcetype=3,
                publish=True,
            )
        assert out["published"] is True
        assert any("PublishAllXml" in r.url for r in _posts(m))

    def test_create_dry_run_returns_preview_no_post(self, profile):
        from crm.core import webresource
        dry = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            out = webresource.create_webresource(
                dry, name="new_x.js", content=b"x", webresourcetype=3)
        assert out["_dry_run"] is True
        assert not _posts(m)

    def test_create_unparseable_id_sets_lookup_error(self, backend):
        from crm.core import webresource
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("webresourceset"), status_code=204,
                   headers={"OData-EntityId": "https://x/webresourceset(bogus)"})
            out = webresource.create_webresource(
                backend, name="new_x.js", content=b"x", webresourcetype=3)
        assert out["created"] is True
        assert out["webresourceid"] is None
        assert "webresource_lookup_error" in out


class TestUpdateWebresource:
    def test_update_resolves_id_and_patches_content_only(self, backend):
        from crm.core import webresource
        raw = b"updated()"
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"),
                  json={"value": [{"webresourceid": _WR_ID}]})
            m.patch(backend.url_for(f"webresourceset({_WR_ID})"), status_code=204)
            out = webresource.update_webresource(
                backend, "new_x.js", content=raw)
        assert out["updated"] is True
        assert out["webresourceid"] == _WR_ID
        patch = _patches(m)[0]
        assert f"webresourceset({_WR_ID})" in patch.url
        body = patch.json()
        assert body["content"] == base64.b64encode(raw).decode("ascii")
        # content-only PATCH must NOT carry displayname
        assert "displayname" not in body
        assert out["fields"] == ["content"]

    def test_update_display_name_only(self, backend):
        from crm.core import webresource
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"),
                  json={"value": [{"webresourceid": _WR_ID}]})
            m.patch(backend.url_for(f"webresourceset({_WR_ID})"), status_code=204)
            out = webresource.update_webresource(
                backend, "new_x.js", display_name="Renamed")
        body = _patches(m)[0].json()
        assert body["displayname"] == "Renamed"
        # display-name-only PATCH must NOT carry content
        assert "content" not in body
        assert out["fields"] == ["displayname"]

    def test_update_routes_solution_header_and_publishes(self, backend):
        from crm.core import webresource
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"),
                  json={"value": [{"webresourceid": _WR_ID}]})
            m.patch(backend.url_for(f"webresourceset({_WR_ID})"), status_code=204)
            m.post(backend.url_for("PublishAllXml"), status_code=204)
            out = webresource.update_webresource(
                backend, "new_x.js", content=b"x", solution="cwx_sol", publish=True)
        assert _patches(m)[0].headers["MSCRM.SolutionUniqueName"] == "cwx_sol"
        assert out["published"] is True

    def test_update_nothing_to_change_raises(self, backend):
        from crm.core import webresource
        with pytest.raises(D365Error, match="nothing to update"):
            webresource.update_webresource(backend, "new_x.js")

    def test_update_name_not_found_raises(self, backend):
        from crm.core import webresource
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json={"value": []})
            with pytest.raises(D365Error, match="not found"):
                webresource.update_webresource(backend, "missing.js", content=b"x")

    def test_update_dry_run_returns_preview(self, profile):
        from crm.core import webresource
        dry = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            # _resolve_id_by_name force-reads even under dry-run
            m.get(dry.url_for("webresourceset"),
                  json={"value": [{"webresourceid": _WR_ID}]})
            out = webresource.update_webresource(dry, "new_x.js", content=b"x")
        assert out["_dry_run"] is True
        assert not _patches(m)


class TestGetWebresource:
    def test_get_resolves_by_name(self, backend):
        from crm.core import webresource
        row = {"webresourceid": _WR_ID, "name": "new_x.js",
               "displayname": "X", "webresourcetype": 3, "ismanaged": False}
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json={"value": [row]})
            out = webresource.get_webresource(backend, "new_x.js")
        assert out["webresourceid"] == _WR_ID
        assert out["name"] == "new_x.js"
        # name is filtered (percent-encoded in the URL)
        get = m.request_history[0]
        assert "name+eq+%27new_x.js%27" in get.url

    def test_get_escapes_quotes_in_name(self, backend):
        from crm.core import webresource
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"),
                  json={"value": [{"webresourceid": _WR_ID, "name": "o'brien.js"}]})
            webresource.get_webresource(backend, "o'brien.js")
        # single quote doubled per OData escaping (percent-encoded in the URL)
        assert "o%27%27brien.js" in m.request_history[0].url

    def test_get_not_found_raises(self, backend):
        from crm.core import webresource
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json={"value": []})
            with pytest.raises(D365Error, match="not found"):
                webresource.get_webresource(backend, "missing.js")


class TestListWebresources:
    def test_list_returns_rows(self, backend):
        from crm.core import webresource
        rows = [
            {"name": "a.js", "ismanaged": True},
            {"name": "b.js", "ismanaged": False},
        ]
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json={"value": rows})
            out = webresource.list_webresources(backend)
        assert len(out) == 2

    def test_list_custom_only_filters_managed(self, backend):
        from crm.core import webresource
        rows = [
            {"name": "a.js", "ismanaged": True},
            {"name": "b.js", "ismanaged": False},
            {"name": "c.js", "ismanaged": False},
        ]
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json={"value": rows})
            out = webresource.list_webresources(backend, custom_only=True)
        assert [r["name"] for r in out] == ["b.js", "c.js"]

    def test_list_top_slices(self, backend):
        from crm.core import webresource
        rows = [{"name": f"{i}.js", "ismanaged": False} for i in range(5)]
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json={"value": rows})
            out = webresource.list_webresources(backend, top=2)
        assert len(out) == 2

    def test_list_top_below_one_raises(self, backend):
        from crm.core import webresource
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json={"value": []})
            with pytest.raises(D365Error, match=">= 1"):
                webresource.list_webresources(backend, top=0)


class TestResolveWebresourceId:
    def test_guid_returned_unchanged_no_http(self, backend):
        from crm.core import webresource
        guid = _WR_ID
        with requests_mock.Mocker() as m:
            out = webresource.resolve_webresource_id(backend, guid)
        assert out == guid
        assert m.request_history == []

    def test_guid_with_whitespace_is_stripped(self, backend):
        from crm.core import webresource
        with requests_mock.Mocker() as m:
            out = webresource.resolve_webresource_id(backend, f"  {_WR_ID}  ")
        assert out == _WR_ID
        assert m.request_history == []

    def test_name_resolves_via_get(self, backend):
        from crm.core import webresource
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"),
                  json={"value": [{"webresourceid": _WR_ID}]})
            out = webresource.resolve_webresource_id(backend, "new_icon.png")
        assert out == _WR_ID
        assert any(r.method == "GET" for r in m.request_history)

    def test_name_not_found_raises(self, backend):
        from crm.core import webresource
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json={"value": []})
            with pytest.raises(D365Error, match="not found"):
                webresource.resolve_webresource_id(backend, "missing.png")

    def test_resolve_id_works_under_dry_run(self, profile):
        from crm.core import webresource
        dry = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            m.get(dry.url_for("webresourceset"),
                  json={"value": [{"webresourceid": _WR_ID}]})
            out = webresource.resolve_webresource_id(dry, "new_icon.png")
        assert out == _WR_ID


class TestWebresourceCommands:
    def test_create_command_wires_core(self, monkeypatch):
        import json
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}

        def fake_create(backend, **kw):
            captured.update(kw)
            return {"created": True, "webresourceid": _WR_ID, "name": kw["name"]}

        monkeypatch.setattr("crm.core.webresource.create_webresource", fake_create)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        monkeypatch.setattr("crm.core.solution.publish_all", lambda b: {"ok": True})
        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("foo.js", "w", encoding="utf-8") as fh:
                fh.write("alert(1)")
            result = runner.invoke(cli, [
                "--json", "webresource", "create",
                "--name", "cwx_/scripts/foo.js", "--file", "foo.js", "--no-publish",
            ])
        assert result.exit_code == 0, result.output
        # real resolve_webresourcetype ran: .js -> 3
        assert captured["webresourcetype"] == 3
        assert captured["name"] == "cwx_/scripts/foo.js"
        assert captured["content"] == b"alert(1)"
        env = json.loads(result.output)
        assert env["ok"] is True

    def test_create_type_override_wins(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}
        monkeypatch.setattr(
            "crm.core.webresource.create_webresource",
            lambda backend, **kw: captured.update(kw)
            or {"created": True, "webresourceid": _WR_ID, "name": kw["name"]})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        monkeypatch.setattr("crm.core.solution.publish_all", lambda b: {"ok": True})
        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("foo.js", "w", encoding="utf-8") as fh:
                fh.write("x")
            result = runner.invoke(cli, [
                "--json", "webresource", "create",
                "--name", "cwx_/foo.js", "--file", "foo.js",
                "--type", "1", "--no-publish",
            ])
        assert result.exit_code == 0, result.output
        # --type 1 overrides the inferred .js -> 3
        assert captured["webresourcetype"] == 1

    def test_create_unknown_extension_errors(self, monkeypatch):
        import json
        from click.testing import CliRunner
        from crm.cli import cli
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("foo.xyz", "w", encoding="utf-8") as fh:
                fh.write("x")
            result = runner.invoke(cli, [
                "--json", "webresource", "create",
                "--name", "cwx_/foo.xyz", "--file", "foo.xyz", "--no-publish",
            ])
        assert result.exit_code != 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is False
        assert "infer" in env["error"].lower()

    def test_update_command_wires_core(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}
        monkeypatch.setattr(
            "crm.core.webresource.update_webresource",
            lambda backend, name, **kw: captured.update({"name": name, **kw})
            or {"updated": True, "webresourceid": _WR_ID, "name": name})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        monkeypatch.setattr("crm.core.solution.publish_all", lambda b: {"ok": True})
        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("foo.js", "w", encoding="utf-8") as fh:
                fh.write("updated()")
            result = runner.invoke(cli, [
                "--json", "webresource", "update", "cwx_/foo.js",
                "--file", "foo.js", "--display-name", "Foo", "--no-publish",
            ])
        assert result.exit_code == 0, result.output
        assert captured["name"] == "cwx_/foo.js"
        assert captured["content"] == b"updated()"
        assert captured["display_name"] == "Foo"

    def test_get_command_wires_core(self, monkeypatch):
        import json
        from click.testing import CliRunner
        from crm.cli import cli
        row = {"webresourceid": _WR_ID, "name": "cwx_/foo.js",
               "displayname": "Foo", "webresourcetype": 3, "ismanaged": False}
        monkeypatch.setattr(
            "crm.core.webresource.get_webresource",
            lambda backend, name: row)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "webresource", "get", "cwx_/foo.js",
        ])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["webresourceid"] == _WR_ID

    def test_list_command_wires_core_json(self, monkeypatch):
        import json
        from click.testing import CliRunner
        from crm.cli import cli
        items = [{"name": "a.js", "displayname": "A",
                  "webresourcetype": 3, "ismanaged": False}]
        monkeypatch.setattr(
            "crm.core.webresource.list_webresources",
            lambda backend, **kw: items)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "webresource", "list",
        ])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["meta"]["count"] == 1
        assert env["data"][0]["name"] == "a.js"

    def test_list_command_table_path(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        items = [{"name": "a.js", "displayname": "A",
                  "webresourcetype": 3, "ismanaged": False}]
        monkeypatch.setattr(
            "crm.core.webresource.list_webresources",
            lambda backend, **kw: items)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, ["webresource", "list"])
        assert result.exit_code == 0, result.output
