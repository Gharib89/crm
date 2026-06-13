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
