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


@covers("form list")
def test_form_list_type_and_all(cli):
    """`--all` lists a superset of the default main-only set; `--type quickcreate`
    filters to type 7 only (issue #360). Both assertions are org-independent — they
    don't assume the org carries any particular non-main form type."""
    main_only = json.loads(cli(["--json", "form", "list", "account"]).stdout)["data"]
    all_forms = cli(["--json", "form", "list", "account", "--all"])
    assert all_forms.returncode == 0, all_forms.stderr
    all_data = json.loads(all_forms.stdout)["data"]
    # --all omits the type filter, so it returns at least the default main forms.
    all_ids = {f.get("formid") for f in all_data}
    assert all_ids >= {f.get("formid") for f in main_only}, (
        "--all did not return a superset of the default main forms"
    )

    # --type filters server-side: every returned quick-create form must be type 7
    # (empty is fine — the org may have none; the type constraint is what matters).
    qc = cli(["--json", "form", "list", "account", "--type", "quickcreate"])
    assert qc.returncode == 0, qc.stderr
    qc_data = json.loads(qc.stdout)["data"]
    assert all(f.get("type") == 7 for f in qc_data), (
        f"--type quickcreate returned non-type-7 forms: {qc_data}"
    )

    # --type and --all together is a usage error (Click UsageError → exit 2).
    both = cli(["--json", "form", "list", "account", "--all", "--type", "main"],
               check=False)
    assert both.returncode == 2, f"expected --all + --type rejected with exit 2: {both}"
    assert "mutually exclusive" in (both.stderr + both.stdout).lower()


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


# ── form tab editors: add-tab / remove-tab / rename-tab / move-tab (#460) ───────
#
# Operate on the session ephemeral_entity's Main form, adding throwaway tabs we
# remove again in `finally` so the shared form is left clean. As with the field
# editors, each verb publishes (the GET returns the *published* snapshot, so the
# edit is only visible on re-export after PublishAllXml) and T3 is the re-export
# assertion that the named tab is present (add/rename/move) or absent (remove).
#
# Not @requires_cloud — pure FormXml surgery runs on both targets. CI executes the
# cloud leg; the on-prem v9.x leg is local-only (VPN) and run by the maintainer
# against the agent-on-prem profile, matching the form clone precedent (#268).


@covers("form add-tab")
@covers("form rename-tab")
@covers("form move-tab")
@covers("form remove-tab")
@pytest.mark.slow
def test_form_tab_editors_roundtrip(cli, ephemeral_entity, tmp_path):
    """Add two tabs (the second placed `--after` the first), rename the first,
    reorder the second to the front, then remove both — asserting presence on
    re-export at each step and absence after removal (T3)."""
    forms = json.loads(cli(["--json", "form", "list", ephemeral_entity]).stdout)["data"]
    assert forms, "ephemeral entity has no Main form"
    form_name = forms[0]["name"]
    tab_a, tab_b = "cwx_e2e_tab_a", "cwx_e2e_tab_b"
    try:
        r = cli(["--json", "form", "add-tab", ephemeral_entity, tab_a, "--publish"])
        assert r.returncode == 0, f"add-tab failed:\n{r.stderr}\n{r.stdout}"
        xml = _export_formxml(cli, ephemeral_entity, form_name, tmp_path)
        assert f'name="{tab_a}"' in xml, "added tab not on re-exported form"

        r = cli(["--json", "form", "add-tab", ephemeral_entity, tab_b,
                 "--after", tab_a, "--publish"])
        assert r.returncode == 0, f"add-tab --after failed:\n{r.stderr}\n{r.stdout}"
        assert f'name="{tab_b}"' in _export_formxml(
            cli, ephemeral_entity, form_name, tmp_path)

        r = cli(["--json", "form", "rename-tab", ephemeral_entity, tab_a,
                 "--label", "E2E Renamed", "--publish"])
        assert r.returncode == 0, f"rename-tab failed:\n{r.stderr}\n{r.stdout}"
        assert "E2E Renamed" in _export_formxml(
            cli, ephemeral_entity, form_name, tmp_path)

        r = cli(["--json", "form", "move-tab", ephemeral_entity, tab_b, "--publish"])
        assert r.returncode == 0, f"move-tab failed:\n{r.stderr}\n{r.stdout}"
    finally:
        for tab in (tab_a, tab_b):
            cli(["--json", "form", "remove-tab", ephemeral_entity, tab, "--force",
                 "--publish"], check=False)
    xml = _export_formxml(cli, ephemeral_entity, form_name, tmp_path)
    assert f'name="{tab_a}"' not in xml and f'name="{tab_b}"' not in xml, (
        "removed tabs still present on re-export")


# ── form section editors: add/remove/rename/move-section (#460) ──────────────────
#
# Host the throwaway sections inside a freshly-added tab so the test fully controls
# their container regardless of the stock form's shape; the tab (and its sections)
# are removed in `finally`.


@covers("form add-section")
@covers("form rename-section")
@covers("form move-section")
@covers("form remove-section")
@pytest.mark.slow
def test_form_section_editors_roundtrip(cli, ephemeral_entity, tmp_path):
    """Within a throwaway tab, add two sections (the second `--after` the first),
    rename the first, reorder the second, then remove one — asserting presence on
    re-export and absence after removal (T3)."""
    forms = json.loads(cli(["--json", "form", "list", ephemeral_entity]).stdout)["data"]
    form_name = forms[0]["name"]
    host_tab = "cwx_e2e_stab"
    sec_a, sec_b = "cwx_e2e_sec_a", "cwx_e2e_sec_b"
    try:
        cli(["--json", "form", "add-tab", ephemeral_entity, host_tab, "--publish"])

        r = cli(["--json", "form", "add-section", ephemeral_entity, sec_a,
                 "--tab", host_tab, "--publish"])
        assert r.returncode == 0, f"add-section failed:\n{r.stderr}\n{r.stdout}"
        assert f'name="{sec_a}"' in _export_formxml(
            cli, ephemeral_entity, form_name, tmp_path)

        r = cli(["--json", "form", "add-section", ephemeral_entity, sec_b,
                 "--tab", host_tab, "--after", sec_a, "--publish"])
        assert r.returncode == 0, f"add-section --after failed:\n{r.stderr}\n{r.stdout}"
        assert f'name="{sec_b}"' in _export_formxml(
            cli, ephemeral_entity, form_name, tmp_path)

        r = cli(["--json", "form", "rename-section", ephemeral_entity, sec_a,
                 "--tab", host_tab, "--label", "E2E Section", "--publish"])
        assert r.returncode == 0, f"rename-section failed:\n{r.stderr}\n{r.stdout}"
        assert "E2E Section" in _export_formxml(
            cli, ephemeral_entity, form_name, tmp_path)

        r = cli(["--json", "form", "move-section", ephemeral_entity, sec_b,
                 "--tab", host_tab, "--publish"])
        assert r.returncode == 0, f"move-section failed:\n{r.stderr}\n{r.stdout}"

        r = cli(["--json", "form", "remove-section", ephemeral_entity, sec_a,
                 "--tab", host_tab, "--publish"])
        assert r.returncode == 0, f"remove-section failed:\n{r.stderr}\n{r.stdout}"
        assert f'name="{sec_a}"' not in _export_formxml(
            cli, ephemeral_entity, form_name, tmp_path)
    finally:
        cli(["--json", "form", "remove-tab", ephemeral_entity, host_tab, "--force",
             "--publish"], check=False)


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


@covers("form set-field-props")
@pytest.mark.slow
def test_form_set_field_props_roundtrip(cli, ephemeral_entity, tmp_path):
    """Add `createdon`, toggle its presentation props with set-field-props
    (publishing), then assert each flag landed on the right cell/control in the
    re-exported, published FormXml: disabled on the <control>, and
    locklevel/showlabel/visible on its <cell>. Removes the field to leave the
    form clean."""
    import re as _re
    forms = json.loads(cli(["--json", "form", "list", ephemeral_entity]).stdout)["data"]
    form_name = forms[0]["name"]
    try:
        cli(["--json", "form", "add-field", ephemeral_entity, "createdon", "--publish"])
        r = cli(["--json", "form", "set-field-props", ephemeral_entity, "createdon",
                 "--disabled", "--hidden", "--locked", "--no-show-label", "--publish"])
        assert r.returncode == 0, f"set-field-props failed:\n{r.stderr}\n{r.stdout}"
        data = json.loads(r.stdout)["data"]
        assert data["updated"] is True, data

        xml = _export_formxml(cli, ephemeral_entity, form_name, tmp_path)
        # `disabled` is a <control> attribute.
        ctrl = _re.search(r'<control\b[^>]*datafieldname="createdon"[^>]*/?>', xml)
        assert ctrl, f"createdon control missing from exported form:\n{xml}"
        assert 'disabled="true"' in ctrl.group(0), ctrl.group(0)
        # locklevel / showlabel / visible are <cell> attributes (the schema rejects
        # `visible` on a <control>); locklevel is an integer flag, the rest booleans.
        cell = _re.search(r'(<cell\b[^>]*>)(?:(?!</cell>).)*?datafieldname="createdon"',
                          xml, _re.DOTALL)
        assert cell, "createdon cell missing from exported form"
        cell_tag = cell.group(1)  # the cell's opening tag, captured above
        assert 'locklevel="1"' in cell_tag, cell_tag
        assert 'showlabel="false"' in cell_tag, cell_tag
        assert 'visible="false"' in cell_tag, cell_tag
    finally:
        cli(["--json", "form", "remove-field", ephemeral_entity, "createdon",
             "--publish"], check=False)


# ── event-handler & library wiring (issue #459) ─────────────────────────────────
#
# One round-trip over the four wiring verbs against the session ephemeral_entity's
# Main form, publishing each change and asserting on the re-exported (published)
# FormXml — the same publish-then-read-back contract as the field editors above.
# Needs a real JS web resource to reference (the editor never creates one), so the
# test creates a throwaway one and deletes it in teardown.


@covers("form add-library", "form add-handler", "form remove-handler",
        "form list-handlers")
@pytest.mark.slow
def test_form_handler_wiring_roundtrip(cli, ephemeral_entity, tmp_path, unique):
    """add-library → add-handler (onload + onchange) → list-handlers → remove-handler,
    publishing each step and verifying via re-export that the handler lands under
    <Handlers> (not <InternalHandlers>) and is gone after removal."""
    forms = json.loads(cli(["--json", "form", "list", ephemeral_entity]).stdout)["data"]
    form_name = forms[0]["name"]
    wr_name = f"new_e2e_lib_{unique}.js"
    src = tmp_path / f"{unique}.js"
    src.write_bytes(b"// e2e handler-wiring test")
    create = cli(["--json", "webresource", "create", "--name", wr_name,
                  "--file", str(src), "--display-name", f"E2E lib {unique}"])
    assert create.returncode == 0, f"wr create failed:\n{create.stderr}\n{create.stdout}"
    try:
        # add-library (idempotent register)
        r = cli(["--json", "form", "add-library", ephemeral_entity,
                 "--library", wr_name, "--publish"])
        assert r.returncode == 0, f"add-library failed:\n{r.stderr}\n{r.stdout}"
        xml = _export_formxml(cli, ephemeral_entity, form_name, tmp_path)
        assert f'name="{wr_name}"' in xml, "library not registered in published form"

        # add-handler onload
        r = cli(["--json", "form", "add-handler", ephemeral_entity,
                 "--event", "onload", "--library", wr_name,
                 "--function", "App.onLoad", "--publish"])
        assert r.returncode == 0, f"add-handler failed:\n{r.stderr}\n{r.stdout}"
        xml = _export_formxml(cli, ephemeral_entity, form_name, tmp_path)
        assert 'functionName="App.onLoad"' in xml, "handler not in published form"
        # the wired handler must sit under <Handlers>, never <InternalHandlers>
        import re as _re
        onload_m = _re.search(r'<event name="onload".*?</event>', xml, _re.S)
        assert onload_m is not None, "onload event missing from published form"
        assert "App.onLoad" in onload_m.group(0).split("<Handlers>", 1)[1]

        # add-handler onchange on a field that is on the form
        cli(["--json", "form", "add-field", ephemeral_entity, "createdon", "--publish"])
        r = cli(["--json", "form", "add-handler", ephemeral_entity,
                 "--event", "onchange", "--field", "createdon", "--library", wr_name,
                 "--function", "App.onChange", "--publish"])
        assert r.returncode == 0, f"onchange add-handler failed:\n{r.stderr}\n{r.stdout}"

        # list-handlers reflects what was wired
        listed = json.loads(cli(["--json", "form", "list-handlers",
                                 ephemeral_entity]).stdout)["data"]
        fns = {h["function"] for h in listed}
        assert {"App.onLoad", "App.onChange"} <= fns, f"list-handlers missing wiring: {listed}"

        # remove-handler, then assert absent on read-back
        r = cli(["--json", "form", "remove-handler", ephemeral_entity,
                 "--event", "onload", "--function", "App.onLoad", "--publish"])
        assert r.returncode == 0, f"remove-handler failed:\n{r.stderr}\n{r.stdout}"
        xml = _export_formxml(cli, ephemeral_entity, form_name, tmp_path)
        assert 'functionName="App.onLoad"' not in xml, "handler still present after remove"
    finally:
        cli(["--json", "form", "remove-handler", ephemeral_entity, "--event",
             "onchange", "--field", "createdon", "--function", "App.onChange",
             "--publish"], check=False)
        cli(["--json", "form", "remove-field", ephemeral_entity, "createdon",
             "--publish"], check=False)
        cli(["--json", "webresource", "delete", wr_name, "--yes"], check=False)
