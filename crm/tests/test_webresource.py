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

from crm.utils.d365_backend import D365Backend, D365Error


_WR_ID = "99999999-9999-9999-9999-999999999999"


def _posts(m):
    return [r for r in m.request_history if r.method == "POST"]


def _patches(m):
    return [r for r in m.request_history if r.method == "PATCH"]


def _wr_get_cb(existing):
    """requests_mock GET callback for webresourceset.

    `existing` maps a web resource name to its live row (incl. base64 `content`);
    a name not present resolves to an empty collection (resource missing).
    Parses `$filter` from the raw URL so the looked-up name keeps its case.
    """
    import re
    from urllib.parse import urlparse, parse_qs

    def cb(request, context):
        flt = parse_qs(urlparse(request.url).query).get("$filter", [""])[0]
        m = re.search(r"name eq '([^']+)'", flt)
        name = m.group(1) if m else None
        context.status_code = 200
        row = existing.get(name)
        return {"value": [row] if row else []}

    return cb


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


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

    def test_list_custom_only_pushes_filter_server_side(self, backend):
        from crm.core import webresource
        # server does the filtering; mock returns the already-filtered rows
        rows = [
            {"name": "b.js", "ismanaged": False},
            {"name": "c.js", "ismanaged": False},
        ]
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json={"value": rows})
            out = webresource.list_webresources(backend, custom_only=True)
        assert [r["name"] for r in out] == ["b.js", "c.js"]
        # the $filter is pushed to D365, not applied client-side
        assert m.last_request.qs["$filter"] == ["ismanaged eq false"]

    def test_list_top_pushes_server_side(self, backend):
        from crm.core import webresource
        rows = [{"name": f"{i}.js", "ismanaged": False} for i in range(5)]
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json={"value": rows})
            out = webresource.list_webresources(backend, top=5)
        assert len(out) == 5
        # $top is a server-side param, not a client-side slice
        assert m.last_request.qs["$top"] == ["5"]

    def test_list_top_below_one_raises_without_request(self, backend):
        from crm.core import webresource
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json={"value": []})
            with pytest.raises(D365Error, match=">= 1"):
                webresource.list_webresources(backend, top=0)
        # validation happens before any HTTP call
        assert m.request_history == []


def _deletes(m):
    return [r for r in m.request_history if r.method == "DELETE"]


class TestDeleteWebresource:
    def test_delete_resolves_by_name_and_deletes(self, backend):
        from crm.core import webresource
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"),
                  json={"value": [{"webresourceid": _WR_ID}]})
            m.delete(backend.url_for(f"webresourceset({_WR_ID})"), status_code=204)
            out = webresource.delete_webresource(backend, "new_x.js")
        assert out["deleted"] is True
        assert out["name"] == "new_x.js"
        assert out["webresourceid"] == _WR_ID
        delete = _deletes(m)[0]
        assert f"webresourceset({_WR_ID})" in delete.url

    def test_delete_by_guid_skips_name_lookup(self, backend):
        from crm.core import webresource
        with requests_mock.Mocker() as m:
            m.delete(backend.url_for(f"webresourceset({_WR_ID})"), status_code=204)
            out = webresource.delete_webresource(backend, _WR_ID)
        assert out["deleted"] is True
        assert out["webresourceid"] == _WR_ID
        # a GUID is deleted directly — no resolve-by-name GET
        assert not [r for r in m.request_history if r.method == "GET"]

    def test_delete_not_found_raises(self, backend):
        from crm.core import webresource
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json={"value": []})
            with pytest.raises(D365Error, match="not found"):
                webresource.delete_webresource(backend, "missing.js")
        assert not _deletes(m)

    def test_delete_dry_run_returns_preview_no_delete(self, profile):
        from crm.core import webresource
        dry = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            # resolve-by-name force-reads even under dry-run
            m.get(dry.url_for("webresourceset"),
                  json={"value": [{"webresourceid": _WR_ID}]})
            out = webresource.delete_webresource(dry, "new_x.js")
        assert out["_dry_run"] is True
        assert out["would_delete"] is True
        assert "deleted" not in out
        assert out["webresourceid"] == _WR_ID
        assert not _deletes(m)

    def test_check_dependencies_with_blockers(self, backend):
        """check_dependencies=True fires the dependency function; blockers in result."""
        from crm.core import webresource
        dep_url = backend.url_for(
            f"RetrieveDependenciesForDelete(ObjectId={_WR_ID},ComponentType=61)"
        )
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"),
                  json={"value": [{"webresourceid": _WR_ID}]})
            m.get(dep_url, json={"value": [
                {
                    "dependentcomponenttype": 24,  # Form
                    "dependentcomponentobjectid": "1111ffff-0000-0000-0000-000000000000",
                    "dependentcomponentparentid": None,
                    "requiredcomponenttype": 61,
                    "dependencytype": 1,
                },
            ]})
            m.delete(backend.url_for(f"webresourceset({_WR_ID})"), status_code=204)
            out = webresource.delete_webresource(
                backend, "new_x.js", check_dependencies=True)
        # informational only — the delete still runs
        assert out["deleted"] is True
        assert out["can_delete"] is False
        assert len(out["blockers"]) == 1
        assert out["blockers"][0]["dependent_type"] == "Form"

    def test_check_dependencies_clear_allows_delete(self, backend):
        from crm.core import webresource
        dep_url = backend.url_for(
            f"RetrieveDependenciesForDelete(ObjectId={_WR_ID},ComponentType=61)"
        )
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"),
                  json={"value": [{"webresourceid": _WR_ID}]})
            m.get(dep_url, json={"value": []})
            m.delete(backend.url_for(f"webresourceset({_WR_ID})"), status_code=204)
            out = webresource.delete_webresource(
                backend, "new_x.js", check_dependencies=True)
        assert out["deleted"] is True
        assert out["can_delete"] is True
        assert out["blockers"] == []

    def test_referenced_fault_surfaces_as_d365error(self, backend):
        """A 0x8004f01f (still referenced) server fault propagates as D365Error."""
        from crm.core import webresource
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"),
                  json={"value": [{"webresourceid": _WR_ID}]})
            m.delete(
                backend.url_for(f"webresourceset({_WR_ID})"),
                status_code=400,
                json={"error": {"code": "0x8004f01f", "message":
                                "Web resource cannot be deleted because it is "
                                "referenced by a ribbon button."}},
            )
            with pytest.raises(D365Error, match="referenced"):
                webresource.delete_webresource(backend, "new_x.js")


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

    def test_name_with_whitespace_is_trimmed_before_lookup(self, backend):
        from crm.core import webresource
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"),
                  json={"value": [{"webresourceid": _WR_ID}]})
            out = webresource.resolve_webresource_id(backend, "  new_icon.png  ")
        assert out == _WR_ID
        # the OData filter must use the trimmed name, not the padded input
        assert m.last_request.qs["$filter"] == ["name eq 'new_icon.png'"]

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

    def test_delete_command_wires_core(self, monkeypatch):
        import json
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}
        monkeypatch.setattr(
            "crm.core.webresource.delete_webresource",
            lambda backend, name, **kw: captured.update({"name": name, **kw})
            or {"deleted": True, "webresourceid": _WR_ID, "name": name})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "webresource", "delete", "cwx_/foo.js", "--yes",
        ])
        assert result.exit_code == 0, result.output
        assert captured["name"] == "cwx_/foo.js"
        assert captured["check_dependencies"] is False
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["deleted"] is True

    def test_delete_command_passes_check_dependencies(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        captured = {}
        monkeypatch.setattr(
            "crm.core.webresource.delete_webresource",
            lambda backend, name, **kw: captured.update({"name": name, **kw})
            or {"deleted": True, "webresourceid": _WR_ID, "name": name})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "webresource", "delete", "cwx_/foo.js",
            "--check-dependencies", "--yes",
        ])
        assert result.exit_code == 0, result.output
        assert captured["check_dependencies"] is True

    def test_delete_command_aborts_without_confirmation(self, monkeypatch):
        from click.testing import CliRunner
        from crm.cli import cli
        called = {"deleted": False}
        monkeypatch.setattr(
            "crm.core.webresource.delete_webresource",
            lambda backend, name, **kw: called.update(deleted=True))
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        # No --yes and a non-TTY stdin (EOF) → click.confirm aborts before any
        # delete; the documented JSON envelope is still emitted.
        result = CliRunner().invoke(cli, [
            "--json", "webresource", "delete", "cwx_/foo.js",
        ])
        assert result.exit_code == 1
        assert called["deleted"] is False
        assert '"error": "aborted by user"' in result.output

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


class TestPushWebresources:
    def test_creates_missing_and_publishes_once(self, backend, tmp_path):
        from crm.core import webresource
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "app.js").write_bytes(b"console.log(1)")
        wr_url = backend.url_for(f"webresourceset({_WR_ID})")
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json=_wr_get_cb({}))
            m.post(backend.url_for("webresourceset"), status_code=204,
                   headers={"OData-EntityId": wr_url})
            m.post(backend.url_for("PublishAllXml"), status_code=204)
            out = webresource.push_webresources(
                backend, str(tmp_path), prefix="cwx")
        assert out["pushed"] == 1
        assert out["updated"] == 0
        assert out["skipped"] == 0
        assert out["published"] is True
        assert out["failed"] == []
        # name by convention: <prefix>_<relpath>, forward slashes
        body = _posts(m)[0].json()
        assert body["name"] == "cwx_scripts/app.js"
        assert body["webresourcetype"] == 3  # .js
        # published exactly once, at the end
        assert sum("PublishAllXml" in r.url for r in _posts(m)) == 1

    def test_updates_changed_content_patches_once(self, backend, tmp_path):
        from crm.core import webresource
        (tmp_path / "app.js").write_bytes(b"NEW BODY")
        existing = {"cwx_app.js": {
            "webresourceid": _WR_ID, "name": "cwx_app.js",
            "content": _b64(b"OLD BODY")}}
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json=_wr_get_cb(existing))
            m.patch(backend.url_for(f"webresourceset({_WR_ID})"), status_code=204)
            m.post(backend.url_for("PublishAllXml"), status_code=204)
            out = webresource.push_webresources(backend, str(tmp_path), prefix="cwx")
        assert out["pushed"] == 0
        assert out["updated"] == 1
        assert out["skipped"] == 0
        assert out["published"] is True
        assert len(_patches(m)) == 1
        assert _b64(b"NEW BODY") == _patches(m)[0].json()["content"]
        assert sum("PublishAllXml" in r.url for r in _posts(m)) == 1

    def test_skips_byte_identical_no_patch_no_publish(self, backend, tmp_path):
        from crm.core import webresource
        (tmp_path / "app.js").write_bytes(b"SAME")
        existing = {"cwx_app.js": {
            "webresourceid": _WR_ID, "name": "cwx_app.js", "content": _b64(b"SAME")}}
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json=_wr_get_cb(existing))
            m.post(backend.url_for("PublishAllXml"), status_code=204)
            out = webresource.push_webresources(backend, str(tmp_path), prefix="cwx")
        assert out["skipped"] == 1
        assert out["pushed"] == 0 and out["updated"] == 0
        assert out["published"] is False
        assert _patches(m) == []
        assert not any("PublishAllXml" in r.url for r in _posts(m))

    def test_mixed_create_update_skip_counts(self, backend, tmp_path):
        from crm.core import webresource
        (tmp_path / "new.js").write_bytes(b"new")
        (tmp_path / "changed.css").write_bytes(b"changed-new")
        (tmp_path / "same.html").write_bytes(b"<p>same</p>")
        existing = {
            "cwx_changed.css": {"webresourceid": _WR_ID, "name": "cwx_changed.css",
                              "content": _b64(b"changed-old")},
            "cwx_same.html": {"webresourceid": _WR_ID, "name": "cwx_same.html",
                            "content": _b64(b"<p>same</p>")},
        }
        wr_url = backend.url_for(f"webresourceset({_WR_ID})")
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json=_wr_get_cb(existing))
            m.post(backend.url_for("webresourceset"), status_code=204,
                   headers={"OData-EntityId": wr_url})
            m.patch(backend.url_for(f"webresourceset({_WR_ID})"), status_code=204)
            m.post(backend.url_for("PublishAllXml"), status_code=204)
            out = webresource.push_webresources(backend, str(tmp_path), prefix="cwx")
        assert out["pushed"] == 1
        assert out["updated"] == 1
        assert out["skipped"] == 1
        assert out["published"] is True
        assert sum("PublishAllXml" in r.url for r in _posts(m)) == 1

    def test_dry_run_lists_would_sets_and_writes_nothing(self, dry_backend, tmp_path):
        from crm.core import webresource
        (tmp_path / "new.js").write_bytes(b"new")
        (tmp_path / "changed.css").write_bytes(b"changed-new")
        (tmp_path / "same.html").write_bytes(b"<p>same</p>")
        existing = {
            "cwx_changed.css": {"webresourceid": _WR_ID, "name": "cwx_changed.css",
                              "content": _b64(b"changed-old")},
            "cwx_same.html": {"webresourceid": _WR_ID, "name": "cwx_same.html",
                            "content": _b64(b"<p>same</p>")},
        }
        with requests_mock.Mocker() as m:
            m.get(dry_backend.url_for("webresourceset"), json=_wr_get_cb(existing))
            out = webresource.push_webresources(dry_backend, str(tmp_path), prefix="cwx")
        assert out["_dry_run"] is True
        assert out["would_create"] == ["cwx_new.js"]
        assert out["would_update"] == ["cwx_changed.css"]
        assert out["skipped"] == 1
        assert out["published"] is False
        # reads-execute: GETs ran, no writes
        assert _posts(m) == [] and _patches(m) == []

    def test_partial_failure_reported_rest_still_push(self, backend, tmp_path):
        from crm.core import webresource
        (tmp_path / "good.js").write_bytes(b"ok")
        (tmp_path / "bad.unknownext").write_bytes(b"x")
        wr_url = backend.url_for(f"webresourceset({_WR_ID})")
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json=_wr_get_cb({}))
            m.post(backend.url_for("webresourceset"), status_code=204,
                   headers={"OData-EntityId": wr_url})
            m.post(backend.url_for("PublishAllXml"), status_code=204)
            out = webresource.push_webresources(backend, str(tmp_path), prefix="cwx")
        assert out["pushed"] == 1  # good.js still pushed
        assert len(out["failed"]) == 1
        assert out["failed"][0]["file"] == "bad.unknownext"
        assert out["published"] is True  # the one success still publishes

    def test_publishes_even_when_one_file_fails(self, backend, tmp_path):
        from crm.core import webresource
        (tmp_path / "good.js").write_bytes(b"ok")
        (tmp_path / "bad.zzz").write_bytes(b"x")
        wr_url = backend.url_for(f"webresourceset({_WR_ID})")
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json=_wr_get_cb({}))
            m.post(backend.url_for("webresourceset"), status_code=204,
                   headers={"OData-EntityId": wr_url})
            m.post(backend.url_for("PublishAllXml"), status_code=204)
            out = webresource.push_webresources(backend, str(tmp_path), prefix="cwx")
        assert sum("PublishAllXml" in r.url for r in _posts(m)) == 1

    def test_invalid_prefix_raises_before_any_http(self, backend, tmp_path):
        from crm.core import webresource
        (tmp_path / "app.js").write_bytes(b"x")
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json=_wr_get_cb({}))
            with pytest.raises(D365Error, match="customizationprefix"):
                webresource.push_webresources(backend, str(tmp_path), prefix="bad prefix")
        assert m.request_history == []

    def test_missing_directory_raises(self, backend, tmp_path):
        from crm.core import webresource
        missing = tmp_path / "does-not-exist"
        with pytest.raises(D365Error, match="not found or not a directory"):
            webresource.push_webresources(backend, str(missing), prefix="cwx")

    def test_empty_directory_is_noop(self, backend, tmp_path):
        from crm.core import webresource
        out = webresource.push_webresources(backend, str(tmp_path), prefix="cwx")
        assert out == {"pushed": 0, "updated": 0, "skipped": 0,
                       "published": False, "failed": [], "files": []}

    def test_no_publish_creates_but_skips_publish(self, backend, tmp_path):
        from crm.core import webresource
        (tmp_path / "app.js").write_bytes(b"x")
        wr_url = backend.url_for(f"webresourceset({_WR_ID})")
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json=_wr_get_cb({}))
            m.post(backend.url_for("webresourceset"), status_code=204,
                   headers={"OData-EntityId": wr_url})
            m.post(backend.url_for("PublishAllXml"), status_code=204)
            out = webresource.push_webresources(
                backend, str(tmp_path), prefix="cwx", publish=False)
        assert out["pushed"] == 1
        assert out["published"] is False
        assert not any("PublishAllXml" in r.url for r in _posts(m))

    def test_unreadable_file_reported_rest_still_push(self, backend, tmp_path):
        from pathlib import Path
        from crm.core import webresource
        (tmp_path / "good.js").write_bytes(b"ok")
        (tmp_path / "locked.js").write_bytes(b"x")
        real_read = Path.read_bytes

        def fake_read(self):
            if self.name == "locked.js":
                raise OSError("permission denied")
            return real_read(self)

        wr_url = backend.url_for(f"webresourceset({_WR_ID})")
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("webresourceset"), json=_wr_get_cb({}))
            m.post(backend.url_for("webresourceset"), status_code=204,
                   headers={"OData-EntityId": wr_url})
            m.post(backend.url_for("PublishAllXml"), status_code=204)
            import pytest as _pytest
            with _pytest.MonkeyPatch.context() as mp:
                mp.setattr(Path, "read_bytes", fake_read)
                out = webresource.push_webresources(backend, str(tmp_path), prefix="cwx")
        assert out["pushed"] == 1  # good.js still pushed despite locked.js
        assert len(out["failed"]) == 1
        assert out["failed"][0]["file"] == "locked.js"
        assert "permission denied" in out["failed"][0]["error"]


class TestPushCommand:
    def test_push_command_wires_core_json(self, monkeypatch, tmp_path):
        import json
        from click.testing import CliRunner
        from crm.cli import cli
        (tmp_path / "a.js").write_bytes(b"x")
        res = {"pushed": 1, "updated": 0, "skipped": 0, "published": True,
               "failed": [], "files": [{"file": "a.js", "name": "cwx_a.js",
                                        "action": "created"}]}
        monkeypatch.setattr("crm.core.webresource.push_webresources",
                            lambda backend, directory, **kw: res)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "webresource", "push", str(tmp_path), "--prefix", "cwx"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["pushed"] == 1
        assert env["data"]["files"][0]["name"] == "cwx_a.js"

    def test_push_command_exit_1_on_failure(self, monkeypatch, tmp_path):
        import json
        from click.testing import CliRunner
        from crm.cli import cli
        (tmp_path / "a.js").write_bytes(b"x")
        res = {"pushed": 0, "updated": 0, "skipped": 0, "published": False,
               "failed": [{"file": "a.js", "name": "cwx_a.js", "error": "boom"}],
               "files": [{"file": "a.js", "name": "cwx_a.js",
                          "action": "failed", "error": "boom"}]}
        monkeypatch.setattr("crm.core.webresource.push_webresources",
                            lambda backend, directory, **kw: res)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "webresource", "push", str(tmp_path), "--prefix", "cwx"])
        assert result.exit_code == 1, result.output
        env = json.loads(result.output)
        assert env["ok"] is False
        assert "boom" in env["error"]


    def test_push_command_threads_no_publish(self, monkeypatch, tmp_path):
        from click.testing import CliRunner
        from crm.cli import cli
        (tmp_path / "a.js").write_bytes(b"x")
        captured = {}

        def fake(backend, directory, **kw):
            captured.update(kw)
            return {"pushed": 0, "updated": 0, "skipped": 0, "published": False,
                    "failed": [], "files": []}

        monkeypatch.setattr("crm.core.webresource.push_webresources", fake)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "webresource", "push", str(tmp_path),
            "--prefix", "cwx", "--no-publish"])
        assert result.exit_code == 0, result.output
        assert captured["publish"] is False

    def test_push_command_invalid_prefix_is_usage_error(self, monkeypatch, tmp_path):
        from click.testing import CliRunner
        from crm.cli import cli
        (tmp_path / "a.js").write_bytes(b"x")
        # backend must never be touched — the bad prefix fails at parse time.
        def _boom(self):
            raise AssertionError("backend resolved before prefix validation")
        monkeypatch.setattr("crm.cli.CLIContext.backend", _boom)
        result = CliRunner().invoke(cli, [
            "--json", "webresource", "push", str(tmp_path), "--prefix", "bad prefix"])
        assert result.exit_code == 2, result.output  # Click usage error
        assert "customizationprefix" in result.output
