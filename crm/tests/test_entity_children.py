"""Tests for `entity children` — per-relationship related-record counts (#234)."""
# pyright: basic

from __future__ import annotations

import json

import pytest
import requests_mock
from click.testing import CliRunner

from crm import cli as crm_cli
from crm.cli import CLIContext
from crm.core import entity as entity_mod
from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error

_GUID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        api_version="v9.2",
        verify_ssl=False,
    )


@pytest.fixture
def backend(profile):
    return D365Backend(profile, password="pw", dry_run=False)


# ── multipart $batch response builders ───────────────────────────────────


def _batch_count_response(counts: list[int], boundary: str = "batchresp") -> bytes:
    """Build a multipart/mixed $batch response of `?$count=true` collection bodies.

    Each part carries a JSON envelope with `@odata.count` (and a 1-row `value`,
    mirroring the `$top=1` the implementation sends) — the on-prem-safe count form.
    """
    parts = [
        "Content-Type: application/http\r\n"
        "Content-Transfer-Encoding: binary\r\n"
        "\r\n"
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: application/json\r\n"
        "\r\n"
        + json.dumps({"@odata.count": c, "value": [{}] if c else []})
        for c in counts
    ]
    text = (
        f"--{boundary}\r\n"
        + f"\r\n--{boundary}\r\n".join(parts)
        + f"\r\n--{boundary}--\r\n"
    )
    return text.encode("utf-8")


def _batch_mixed_response(items: list, boundary: str = "batchresp") -> bytes:
    """Build a $batch response where each item is an int count or ('err', msg).

    Mirrors a non-transactional continue-on-error batch: successful counts come
    back as 200 + `@odata.count`; uncountable child entities as a 400 sub-part
    carrying an OData error (e.g. RetrieveMultiple-unsupported system entities).
    """
    parts = []
    for it in items:
        if isinstance(it, int):
            parts.append(
                "Content-Type: application/http\r\n"
                "Content-Transfer-Encoding: binary\r\n"
                "\r\n"
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/json\r\n"
                "\r\n"
                + json.dumps({"@odata.count": it, "value": [{}] if it else []})
            )
        else:
            parts.append(
                "Content-Type: application/http\r\n"
                "Content-Transfer-Encoding: binary\r\n"
                "\r\n"
                "HTTP/1.1 400 Bad Request\r\n"
                "Content-Type: application/json\r\n"
                "\r\n"
                + json.dumps({"error": {"code": "0x80040800", "message": it[1]}})
            )
    text = (
        f"--{boundary}\r\n"
        + f"\r\n--{boundary}\r\n".join(parts)
        + f"\r\n--{boundary}--\r\n"
    )
    return text.encode("utf-8")


def _stub_defs(m, backend, defs: list[tuple[str, str]]) -> None:
    """Stub the bulk EntityDefinitions logical↔set call."""
    m.get(
        backend.url_for("EntityDefinitions"),
        json={"value": [{"LogicalName": lg, "EntitySetName": st} for lg, st in defs]},
    )


def _stub_one_to_many(m, backend, parent_logical: str, rels: list[tuple[str, str]]) -> None:
    """Stub the OneToManyRelationships call (child logical, referencing attribute)."""
    m.get(
        backend.url_for(
            f"EntityDefinitions(LogicalName='{parent_logical}')/OneToManyRelationships"
        ),
        json={
            "value": [
                {"ReferencingEntity": child, "ReferencingAttribute": attr,
                 "ReferencedEntity": parent_logical}
                for child, attr in rels
            ]
        },
    )


class TestCountChildren:
    def test_returns_one_row_per_relationship_with_counts(self, backend, profile):
        with requests_mock.Mocker() as m:
            _stub_defs(m, backend, [
                ("account", "accounts"),
                ("contact", "contacts"),
                ("contoso_invoice", "contoso_invoices"),
            ])
            _stub_one_to_many(m, backend, "account", [
                ("contact", "parentaccountid"),
                ("contoso_invoice", "contoso_account"),
            ])
            m.post(
                profile.api_base + "$batch",
                content=_batch_count_response([12, 7]),
                headers={"Content-Type": "multipart/mixed; boundary=batchresp"},
                status_code=200,
            )
            rows = entity_mod.count_children(backend, "accounts", _GUID)

        assert rows == [
            {"entity": "contact", "attribute": "parentaccountid",
             "set": "contacts", "count": 12},
            {"entity": "contoso_invoice", "attribute": "contoso_account",
             "set": "contoso_invoices", "count": 7},
        ]

    def test_zero_count_rows_are_kept(self, backend, profile):
        with requests_mock.Mocker() as m:
            _stub_defs(m, backend, [("account", "accounts"), ("contact", "contacts")])
            _stub_one_to_many(m, backend, "account", [("contact", "parentaccountid")])
            m.post(
                profile.api_base + "$batch",
                content=_batch_count_response([0]),
                headers={"Content-Type": "multipart/mixed; boundary=batchresp"},
                status_code=200,
            )
            rows = entity_mod.count_children(backend, "accounts", _GUID)
        assert rows == [
            {"entity": "contact", "attribute": "parentaccountid",
             "set": "contacts", "count": 0},
        ]

    def test_no_relationships_returns_empty_without_batch(self, backend):
        with requests_mock.Mocker() as m:
            _stub_defs(m, backend, [("account", "accounts")])
            _stub_one_to_many(m, backend, "account", [])
            # No $batch stub: an empty batch must never be POSTed (server errors on it).
            rows = entity_mod.count_children(backend, "accounts", _GUID)
            assert "POST" not in {req.method for req in m.request_history}
        assert rows == []

    def test_non_empty_drops_zero_count_rows(self, backend, profile):
        with requests_mock.Mocker() as m:
            _stub_defs(m, backend, [
                ("account", "accounts"),
                ("contact", "contacts"),
                ("contoso_invoice", "contoso_invoices"),
            ])
            _stub_one_to_many(m, backend, "account", [
                ("contact", "parentaccountid"),
                ("contoso_invoice", "contoso_account"),
            ])
            m.post(
                profile.api_base + "$batch",
                content=_batch_count_response([0, 7]),
                headers={"Content-Type": "multipart/mixed; boundary=batchresp"},
                status_code=200,
            )
            rows = entity_mod.count_children(backend, "accounts", _GUID, non_empty=True)
        assert rows == [
            {"entity": "contoso_invoice", "attribute": "contoso_account",
             "set": "contoso_invoices", "count": 7},
        ]

    def test_uncountable_child_reported_not_fatal(self, backend, profile):
        # A child entity that rejects RetrieveMultiple (e.g. postregarding) must
        # surface as count=null + error, not abort the whole audit.
        with requests_mock.Mocker() as m:
            _stub_defs(m, backend, [
                ("account", "accounts"),
                ("contact", "contacts"),
                ("postregarding", "postregardings"),
            ])
            _stub_one_to_many(m, backend, "account", [
                ("contact", "parentcustomerid"),
                ("postregarding", "regardingobjectid"),
            ])
            m.post(
                profile.api_base + "$batch",
                content=_batch_mixed_response([
                    5,
                    ("err", "The 'RetrieveMultiple' method does not support "
                            "entities of type 'postregarding'."),
                ]),
                headers={"Content-Type": "multipart/mixed; boundary=batchresp"},
                status_code=200,
            )
            rows = entity_mod.count_children(backend, "accounts", _GUID)
        assert rows[0] == {"entity": "contact", "attribute": "parentcustomerid",
                           "set": "contacts", "count": 5}
        assert rows[1]["entity"] == "postregarding"
        assert rows[1]["count"] is None
        assert "RetrieveMultiple" in rows[1]["error"]

    def test_non_empty_keeps_uncountable_rows(self, backend, profile):
        # --non-empty drops confirmed-zero rows but keeps count=null (unknown,
        # not zero) — silently dropping them would hide them from an audit.
        with requests_mock.Mocker() as m:
            _stub_defs(m, backend, [
                ("account", "accounts"),
                ("contact", "contacts"),
                ("postregarding", "postregardings"),
            ])
            _stub_one_to_many(m, backend, "account", [
                ("contact", "parentcustomerid"),
                ("postregarding", "regardingobjectid"),
            ])
            m.post(
                profile.api_base + "$batch",
                content=_batch_mixed_response([0, ("err", "not supported")]),
                headers={"Content-Type": "multipart/mixed; boundary=batchresp"},
                status_code=200,
            )
            rows = entity_mod.count_children(backend, "accounts", _GUID, non_empty=True)
        assert [r["entity"] for r in rows] == ["postregarding"]

    def test_self_referential_relationship_is_a_normal_row(self, backend, profile):
        # account → account via parentaccountid: child logical == parent, no special case.
        with requests_mock.Mocker() as m:
            _stub_defs(m, backend, [("account", "accounts")])
            _stub_one_to_many(m, backend, "account", [("account", "parentaccountid")])
            m.post(
                profile.api_base + "$batch",
                content=_batch_count_response([3]),
                headers={"Content-Type": "multipart/mixed; boundary=batchresp"},
                status_code=200,
            )
            rows = entity_mod.count_children(backend, "accounts", _GUID)
        assert rows == [
            {"entity": "account", "attribute": "parentaccountid",
             "set": "accounts", "count": 3},
        ]

    def test_filter_entities_reduces_relationships_queried(self, backend, profile):
        with requests_mock.Mocker() as m:
            _stub_defs(m, backend, [
                ("account", "accounts"),
                ("contact", "contacts"),
                ("contoso_invoice", "contoso_invoices"),
                ("task", "tasks"),
            ])
            _stub_one_to_many(m, backend, "account", [
                ("contact", "parentaccountid"),
                ("contoso_invoice", "contoso_account"),
                ("task", "regardingobjectid"),
            ])
            m.post(
                profile.api_base + "$batch",
                content=_batch_count_response([7]),
                headers={"Content-Type": "multipart/mixed; boundary=batchresp"},
                status_code=200,
            )
            rows = entity_mod.count_children(
                backend, "accounts", _GUID, filter_entities=r"^contoso_",
            )
            posts = [req for req in m.request_history if req.method == "POST"]
            body = posts[0].text
        # Only the matching child was counted — non-matching children never hit
        # the wire (pre-filter, not a post-filter on the results).
        assert "contoso_invoices?$filter" in body
        assert "contacts?$filter" not in body
        assert "tasks?$filter" not in body
        # narrow $select keeps each counted row to the one lookup column
        assert "$select=_contoso_account_value" in body
        assert rows == [
            {"entity": "contoso_invoice", "attribute": "contoso_account",
             "set": "contoso_invoices", "count": 7},
        ]

    def test_counts_are_batched_not_sequential(self, backend, profile):
        # AC#4: round trips are O(rels / chunk), not O(rels). 3 rels @ chunk=2 →
        # 2 metadata GETs + ceil(3/2)=2 $batch POSTs, and zero standalone $count GETs.
        hdr = {"Content-Type": "multipart/mixed; boundary=batchresp"}
        with requests_mock.Mocker() as m:
            _stub_defs(m, backend, [
                ("account", "accounts"),
                ("contact", "contacts"),
                ("contoso_invoice", "contoso_invoices"),
                ("task", "tasks"),
            ])
            _stub_one_to_many(m, backend, "account", [
                ("contact", "parentaccountid"),
                ("contoso_invoice", "contoso_account"),
                ("task", "regardingobjectid"),
            ])
            m.post(profile.api_base + "$batch", [
                {"content": _batch_count_response([5, 6]), "headers": hdr, "status_code": 200},
                {"content": _batch_count_response([7]), "headers": hdr, "status_code": 200},
            ])
            rows = entity_mod.count_children(backend, "accounts", _GUID, batch_chunk_size=2)
            methods = [req.method for req in m.request_history]
            count_gets = [
                req for req in m.request_history
                if req.method == "GET" and "%24count=true" in req.url.replace("$", "%24")
            ]
        assert methods.count("GET") == 2          # the two metadata reads only
        assert methods.count("POST") == 2          # ceil(3 / 2) batches
        assert count_gets == []                    # no per-relationship sequential GETs
        assert [row["count"] for row in rows] == [5, 6, 7]


class TestCountChildrenGuards:
    def test_dry_run_issues_real_get_counts_not_batch(self, profile):
        # Read-only: under --dry-run the $batch POST would be short-circuited, so
        # counts must run as direct GETs and report REAL numbers, not a preview stub.
        dry = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            _stub_defs(m, dry, [
                ("account", "accounts"),
                ("contact", "contacts"),
                ("contoso_invoice", "contoso_invoices"),
            ])
            _stub_one_to_many(m, dry, "account", [
                ("contact", "parentaccountid"),
                ("contoso_invoice", "contoso_account"),
            ])
            m.get(dry.url_for("contacts"), json={"@odata.count": 4, "value": [{}]})
            m.get(dry.url_for("contoso_invoices"), json={"@odata.count": 0, "value": []})
            rows = entity_mod.count_children(dry, "accounts", _GUID)
            methods = [r.method for r in m.request_history]
        assert "POST" not in methods  # no $batch under dry-run
        assert rows == [
            {"entity": "contact", "attribute": "parentaccountid",
             "set": "contacts", "count": 4},
            {"entity": "contoso_invoice", "attribute": "contoso_account",
             "set": "contoso_invoices", "count": 0},
        ]

    def test_invalid_filter_regex_raises_before_any_request(self, backend):
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="regular expression"):
                entity_mod.count_children(backend, "accounts", _GUID, filter_entities="[")
            assert m.request_history == []  # validated before any round trip

    def test_non_positive_chunk_size_raises(self, backend):
        with pytest.raises(D365Error, match="positive"):
            entity_mod.count_children(backend, "accounts", _GUID, batch_chunk_size=0)


class TestChildrenCommand:
    def _bind(self, monkeypatch, backend):
        monkeypatch.setattr(CLIContext, "backend", lambda self: backend)

    def test_json_envelope(self, backend, profile, monkeypatch):
        self._bind(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            _stub_defs(m, backend, [("account", "accounts"), ("contact", "contacts")])
            _stub_one_to_many(m, backend, "account", [("contact", "parentaccountid")])
            m.post(
                profile.api_base + "$batch",
                content=_batch_count_response([4]),
                headers={"Content-Type": "multipart/mixed; boundary=batchresp"},
                status_code=200,
            )
            result = CliRunner().invoke(
                crm_cli.cli, ["--json", "entity", "children", "accounts", _GUID]
            )
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"] == [
            {"entity": "contact", "attribute": "parentaccountid",
             "set": "contacts", "count": 4},
        ]

    def test_non_empty_flag_wires_through(self, backend, profile, monkeypatch):
        self._bind(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            _stub_defs(m, backend, [
                ("account", "accounts"),
                ("contact", "contacts"),
                ("contoso_invoice", "contoso_invoices"),
            ])
            _stub_one_to_many(m, backend, "account", [
                ("contact", "parentaccountid"),
                ("contoso_invoice", "contoso_account"),
            ])
            m.post(
                profile.api_base + "$batch",
                content=_batch_count_response([0, 9]),
                headers={"Content-Type": "multipart/mixed; boundary=batchresp"},
                status_code=200,
            )
            result = CliRunner().invoke(
                crm_cli.cli,
                ["--json", "entity", "children", "accounts", _GUID, "--non-empty"],
            )
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert [row["entity"] for row in env["data"]] == ["contoso_invoice"]

    def test_filter_entities_flag_wires_through(self, backend, profile, monkeypatch):
        self._bind(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            _stub_defs(m, backend, [
                ("account", "accounts"),
                ("contact", "contacts"),
                ("contoso_invoice", "contoso_invoices"),
            ])
            _stub_one_to_many(m, backend, "account", [
                ("contact", "parentaccountid"),
                ("contoso_invoice", "contoso_account"),
            ])
            m.post(
                profile.api_base + "$batch",
                content=_batch_count_response([7]),
                headers={"Content-Type": "multipart/mixed; boundary=batchresp"},
                status_code=200,
            )
            result = CliRunner().invoke(
                crm_cli.cli,
                ["--json", "entity", "children", "accounts", _GUID,
                 "--filter-entities", r"^contoso_"],
            )
            body = [r for r in m.request_history if r.method == "POST"][0].text
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert [row["entity"] for row in env["data"]] == ["contoso_invoice"]
        assert "contacts?$filter" not in body  # pre-filter reached core

    def test_invalid_filter_regex_is_usage_error_before_backend(self, monkeypatch):
        # Untrusted regex validated at the CLI boundary: usage error (exit 2),
        # backend never constructed (ctx.backend would raise if called).
        def _boom(self):
            raise AssertionError("backend constructed before regex validation")
        monkeypatch.setattr(CLIContext, "backend", _boom)
        result = CliRunner().invoke(
            crm_cli.cli,
            ["--json", "entity", "children", "accounts", _GUID, "--filter-entities", "["],
        )
        assert result.exit_code == 2
        assert "filter-entities" in result.output
