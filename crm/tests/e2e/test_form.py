# pyright: basic
"""E2E tests for form verbs: list / export / clone."""
from __future__ import annotations

import json

import pytest

from crm.tests.e2e.coverage import covers


# ── form list ─────────────────────────────────────────────────────────────────


@covers("form list")
def test_form_list_account(cli):
    """Every D365 org ships at least one Main form for 'account'; assert non-empty."""
    result = cli(["--json", "form", "list", "account"])
    assert result.returncode == 0, (
        f"form list failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    items = env["data"]
    assert isinstance(items, list), f"expected list, got {type(items)}: {env}"
    assert len(items) > 0, "form list returned empty list for 'account'"
    first = items[0]
    assert "formid" in first, f"formid missing from first form: {first}"
    assert "name" in first, f"name missing from first form: {first}"


# ── form export ───────────────────────────────────────────────────────────────


@covers("form export")
def test_form_export_account(cli, tmp_path):
    """List account forms, pick the first, export its formxml to a file; assert
    the file is written and contains XML (starts with '<')."""
    # Resolve the first account form name.
    r_list = cli(["--json", "form", "list", "account"])
    assert r_list.returncode == 0, r_list.stderr
    forms = json.loads(r_list.stdout)["data"]
    assert forms, "no account forms returned; cannot test form export"
    form_name = forms[0]["name"]

    out_file = tmp_path / "account_form.xml"
    result = cli([
        "--json", "form", "export", "account", form_name,
        "--output", str(out_file),
    ])
    assert result.returncode == 0, (
        f"form export failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    assert env["data"].get("entity") == "account"
    assert env["data"].get("form") == form_name

    # The file must exist and contain XML.
    assert out_file.exists(), f"output file not written: {out_file}"
    xml_text = out_file.read_text(encoding="utf-8")
    assert xml_text.lstrip().startswith("<"), (
        f"exported content does not look like XML: {xml_text[:80]!r}"
    )

    # With no --output, --json must still emit a valid {ok,data} envelope
    # carrying the full FormXml in data (issue #349), not bare XML.
    r_stdout = cli(["--json", "form", "export", "account", form_name])
    assert r_stdout.returncode == 0, (
        f"form export (stdout) failed:\n{r_stdout.stderr}\nstdout: {r_stdout.stdout}"
    )
    env_stdout = json.loads(r_stdout.stdout)
    assert env_stdout["ok"], env_stdout
    assert env_stdout["data"].get("entity") == "account"
    assert env_stdout["data"].get("form") == form_name
    assert env_stdout["data"].get("formxml", "").lstrip().startswith("<")


# ── form clone ────────────────────────────────────────────────────────────────


@covers("form clone")
@pytest.mark.slow
def test_form_clone_account_to_ephemeral(cli, backend, ephemeral_entity):
    """Clone a Main form from 'account' into ephemeral_entity **twice** from the
    same source; assert both succeed with distinct formids; delete both clones.

    We clone from account (always has forms) into the ephemeral entity so we
    never pollute real entity forms.  The ephemeral entity itself will be deleted
    at session teardown, but we still clean up the clones immediately so the
    entity can be deleted cleanly without stranded form dependencies.
    --no-publish avoids the slow PublishAllXml call.

    Issue #268 regression guard: a source form's FormXML reuses its internal
    registration GUIDs (``labelid`` / layout ``id`` / ``uniqueid`` / handler- &
    library-``UniqueId``), which on-prem v9.x enforces as org-unique. Cloning the
    *same* source twice (or even once) collided with ``0x8004f658`` — e.g. *"The
    label '…', id '…' already exists. Supply unique labelid values."* (Dataverse
    online silently reassigns them, which is why cloud never saw it; the ``<form>``
    root carries no ``id`` at all, so there is no single PK to regenerate — the
    original brief's mechanism was wrong). ``crm.core.forms.regenerate_form_clone_ids``
    now assigns a fresh GUID to each internal id per clone while preserving
    external references (control ``classid``, ``<Role Id>``, ``<ViewId>``,
    ``<QuickFormId>``), so repeat clones succeed on every target — hence this runs
    on cloud **and** on-prem (no @requires_cloud gate). A single clone could not
    catch the repeat-collision, so we clone twice and assert two different formids.

    CI executes only the cloud leg (on-prem v9.1 is local-only); the maintainer
    runs the on-prem leg locally against the ``crmworx`` profile to confirm the
    collision no longer occurs (verified for #268).
    """
    # Resolve a form on account to use as the source.
    r_list = cli(["--json", "form", "list", "account"])
    assert r_list.returncode == 0, r_list.stderr
    forms = json.loads(r_list.stdout).get("data", [])
    if not forms:
        pytest.skip("no Main forms found for 'account'; cannot test form clone")
    form_name = forms[0]["name"]

    clone_formids: list[str] = []
    try:
        for attempt in (1, 2):
            result = cli([
                "--json", "form", "clone",
                "account", form_name,
                "--to", ephemeral_entity,
                "--no-publish",
            ])
            assert result.returncode == 0, (
                f"form clone #{attempt} failed:\n{result.stderr}\n"
                f"stdout: {result.stdout}"
            )
            env = json.loads(result.stdout)
            assert env["ok"], env
            data = env["data"]
            assert data.get("created") is True, (
                f"clone #{attempt} expected created=True: {data}"
            )
            formid = data.get("formid")
            assert formid, f"formid missing from clone #{attempt} response: {data}"
            assert data.get("objecttypecode") == ephemeral_entity, (
                f"clone #{attempt} objecttypecode mismatch: {data}"
            )
            clone_formids.append(formid)
        assert clone_formids[0] != clone_formids[1], (
            f"repeat clones of the same source reused a formid (#268 regression): "
            f"{clone_formids}"
        )
    finally:
        for formid in clone_formids:
            try:
                backend.delete(f"systemforms({formid})")
            except Exception:
                pass


# ── form add-field / remove-field / set-field (#326) ────────────────────────────
#
# All three operate on the session ephemeral_entity's auto-created Main form, so
# they never touch a real entity's UI. `createdon` (DateTime) is a stock attribute
# on every entity that a fresh custom entity's default form does not show — a clean
# add/remove target.
#
# IMPORTANT: a plain `GET /systemforms` returns the *published* FormXml, so a field
# edit is only visible on re-export AFTER PublishAllXml (the unpublished PATCH alone
# round-trips as a no-op — the documented form-surgery gotcha). Hence these tests
# pass `--publish` and assert on the re-exported, published form. Acceptance is
# FormXml structure + successful publish + round-trip, NOT visual render (an
# accepted limitation per the issue).

_DATETIME_CLASSID = "{5B773807-9FB2-42DB-97C3-7A91EFF8ADFF}"


def _export_formxml(cli, entity, form_name, tmp_path):
    out_file = tmp_path / "form.xml"
    r = cli(["--json", "form", "export", entity, form_name, "--output", str(out_file)])
    assert r.returncode == 0, r.stderr
    return out_file.read_text(encoding="utf-8")


@covers("form add-field")
@pytest.mark.slow
def test_form_add_field_roundtrip(cli, ephemeral_entity, tmp_path):
    """Add `createdon` to the entity's Main form (publishing the change); assert the
    DateTime classid was resolved from live metadata and the control round-trips via
    a re-export. Removes it again so the shared session form is left clean."""
    forms = json.loads(cli(["--json", "form", "list", ephemeral_entity]).stdout)["data"]
    assert forms, "ephemeral entity has no Main form"
    form_name = forms[0]["name"]
    try:
        r = cli(["--json", "form", "add-field", ephemeral_entity, "createdon",
                 "--publish"])
        assert r.returncode == 0, f"add-field failed:\n{r.stderr}\n{r.stdout}"
        data = json.loads(r.stdout)["data"]
        assert data["updated"] is True, data
        assert data["classid"] == _DATETIME_CLASSID, data
        xml = _export_formxml(cli, ephemeral_entity, form_name, tmp_path)
        assert 'datafieldname="createdon"' in xml, "added control not in exported form"
        assert _DATETIME_CLASSID.lower() in xml.lower(), "classid not in exported form"
    finally:
        cli(["--json", "form", "remove-field", ephemeral_entity, "createdon",
             "--publish"], check=False)


@covers("form remove-field")
@pytest.mark.slow
def test_form_remove_field_roundtrip(cli, ephemeral_entity, tmp_path):
    """Add then remove `createdon` (publishing each); assert it is gone from the
    re-exported form."""
    forms = json.loads(cli(["--json", "form", "list", ephemeral_entity]).stdout)["data"]
    form_name = forms[0]["name"]
    cli(["--json", "form", "add-field", ephemeral_entity, "createdon", "--publish"])
    r = cli(["--json", "form", "remove-field", ephemeral_entity, "createdon",
             "--publish"])
    assert r.returncode == 0, f"remove-field failed:\n{r.stderr}\n{r.stdout}"
    xml = _export_formxml(cli, ephemeral_entity, form_name, tmp_path)
    assert 'datafieldname="createdon"' not in xml, "control still present after remove"


@covers("form set-field")
@pytest.mark.slow
def test_form_set_field_roundtrip(cli, ephemeral_entity, tmp_path):
    """Add `createdon`, then relocate it with set-field into a section targeted by
    its live id (publishing each); assert the command succeeds and the field is
    still present exactly once. (Cross-section relocation is covered by the offline
    unit tests; a fresh form has a single section, so this exercises the live
    read→detach→re-append→publish→round-trip path.) Removes it to leave the form
    clean."""
    import re as _re
    forms = json.loads(cli(["--json", "form", "list", ephemeral_entity]).stdout)["data"]
    form_name = forms[0]["name"]
    try:
        cli(["--json", "form", "add-field", ephemeral_entity, "createdon", "--publish"])
        xml = _export_formxml(cli, ephemeral_entity, form_name, tmp_path)
        section_ids = _re.findall(r'<section\b[^>]*\bid="(\{[^"]+\})"', xml)
        assert section_ids, "no section id on the form to target"
        r = cli(["--json", "form", "set-field", ephemeral_entity, "createdon",
                 "--section", section_ids[0], "--publish"])
        assert r.returncode == 0, f"set-field failed:\n{r.stderr}\n{r.stdout}"
        xml2 = _export_formxml(cli, ephemeral_entity, form_name, tmp_path)
        assert xml2.count('datafieldname="createdon"') == 1, "field not present once"
    finally:
        cli(["--json", "form", "remove-field", ephemeral_entity, "createdon",
             "--publish"], check=False)
