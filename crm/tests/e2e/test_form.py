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
@pytest.mark.requires_cloud
def test_form_clone_account_to_ephemeral(cli, backend, ephemeral_entity):
    """Clone a Main form from 'account' into ephemeral_entity; assert created;
    delete the clone in a finally block.

    We clone from account (always has forms) into the ephemeral entity so we
    never pollute real entity forms.  The ephemeral entity itself will be deleted
    at session teardown, but we still clean up the clone immediately so the
    entity can be deleted cleanly without stranded form dependencies.
    --no-publish avoids the slow PublishAllXml call.

    Requires cloud (OAuth): on-prem v9.1 reuses the formid embedded in the source
    formxml as the new record's PK, so a second run cloning the same source form
    collides. Cloud v9.2 assigns a fresh GUID server-side. SUSPECTED PRODUCT BUG:
    crm.core.forms.retarget_formxml does not strip/regenerate the embedded
    <form id=...> before POST, relying on a server-side reassign that on-prem v9.1
    does not perform.
    """
    # Resolve a form on account to use as the source.
    r_list = cli(["--json", "form", "list", "account"])
    assert r_list.returncode == 0, r_list.stderr
    forms = json.loads(r_list.stdout).get("data", [])
    if not forms:
        pytest.skip("no Main forms found for 'account'; cannot test form clone")
    form_name = forms[0]["name"]

    clone_formid: str | None = None
    try:
        result = cli([
            "--json", "form", "clone",
            "account", form_name,
            "--to", ephemeral_entity,
            "--no-publish",
        ])
        assert result.returncode == 0, (
            f"form clone failed:\n{result.stderr}\nstdout: {result.stdout}"
        )
        env = json.loads(result.stdout)
        assert env["ok"], env
        data = env["data"]
        assert data.get("created") is True, f"expected created=True: {data}"
        clone_formid = data.get("formid")
        assert clone_formid, f"formid missing from clone response: {data}"
        assert data.get("objecttypecode") == ephemeral_entity, (
            f"objecttypecode mismatch: {data}"
        )
    finally:
        if clone_formid:
            try:
                backend.delete(f"systemforms({clone_formid})")
            except Exception:
                pass
