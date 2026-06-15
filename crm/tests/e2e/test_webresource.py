# pyright: basic
"""E2E tests for webresource verbs: list / get / create / update."""
from __future__ import annotations

import json

import pytest

from crm.tests.e2e.coverage import covers


# ── delete ───────────────────────────────────────────────────────────────────


@covers("webresource delete")
@pytest.mark.slow
def test_webresource_delete(cli, tmp_path, unique, request):
    """Create a web resource then delete it via the first-class verb; assert gone.

    Deletes by unique name (the verb resolves it to the id). A subsequent get
    must fail — the record is no longer present.
    """
    name = f"new_e2e_del_{unique}.js"
    src = tmp_path / f"{unique}.js"
    src.write_bytes(b"// e2e delete test")

    # ── CREATE ────────────────────────────────────────────────────────────────
    result = cli([
        "--json", "webresource", "create",
        "--name", name,
        "--file", str(src),
        "--display-name", f"E2E WR del {unique}",
    ])
    assert result.returncode == 0, (
        f"webresource create failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    wid = env["data"].get("webresourceid")
    assert wid, f"webresourceid missing from create response: {env['data']}"

    # Best-effort id-based cleanup in case the delete-by-name path leaves it behind.
    def _cleanup():
        try:
            cli(["--json", "entity", "delete", "webresourceset", wid, "--yes"],
                check=False)
        except Exception:
            pass

    request.addfinalizer(_cleanup)

    # ── DELETE (by name) ───────────────────────────────────────────────────────
    result = cli(["--json", "webresource", "delete", name, "--yes"])
    assert result.returncode == 0, (
        f"webresource delete failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    data = env["data"]
    assert data.get("deleted") is True, f"expected deleted=True: {data}"
    assert data.get("webresourceid", "").lower() == wid.lower(), (
        f"delete returned wrong webresourceid: {data}"
    )

    # ── ASSERT GONE ────────────────────────────────────────────────────────────
    result = cli(["--json", "webresource", "get", name], check=False)
    assert result.returncode != 0, (
        f"expected get to fail after delete, got:\n{result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"] is False, f"web resource still present after delete: {env}"


# ── create + get + update + list ─────────────────────────────────────────────


@covers("webresource create", "webresource get", "webresource update", "webresource list")
@pytest.mark.slow
def test_webresource_lifecycle(cli, tmp_path, unique, request):
    """Full lifecycle: create a JS web resource, get it, update its display name,
    confirm via list, then delete in a finalizer.

    Uses a minimal JS content (a comment) so there is no script-engine
    interference. `--publish` is left at default (True) which makes the command
    slow on on-prem but keeps the create verifiable by a subsequent get.
    """
    name = f"new_e2e_{unique}.js"
    content_v1 = b"// e2e test v1"
    content_v2 = b"// e2e test v2"
    display_v2 = f"E2E WR {unique}"

    # Write the initial source file.
    src = tmp_path / f"{unique}.js"
    src.write_bytes(content_v1)

    # ── CREATE ────────────────────────────────────────────────────────────────
    result = cli([
        "--json", "webresource", "create",
        "--name", name,
        "--file", str(src),
        "--display-name", f"E2E WR {unique}",
    ])
    assert result.returncode == 0, (
        f"webresource create failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    data = env["data"]
    assert data.get("created") is True, f"expected created=True: {data}"
    wid = data.get("webresourceid")
    assert wid, f"webresourceid missing from create response: {data}"

    # Register finalizer — best-effort delete so cleanup never masks test failures.
    def _cleanup():
        try:
            cli(["--json", "entity", "delete", "webresourceset", wid, "--yes"],
                check=False)
        except Exception:
            pass

    request.addfinalizer(_cleanup)

    # ── GET ───────────────────────────────────────────────────────────────────
    result = cli(["--json", "webresource", "get", name])
    assert result.returncode == 0, (
        f"webresource get failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    record = env["data"]
    assert record.get("name") == name, (
        f"expected name={name!r}, got {record.get('name')!r}"
    )
    assert record.get("webresourceid", "").lower() == wid.lower(), (
        f"webresourceid mismatch: get returned {record.get('webresourceid')!r}, "
        f"create returned {wid!r}"
    )

    # ── UPDATE ────────────────────────────────────────────────────────────────
    src_v2 = tmp_path / f"{unique}_v2.js"
    src_v2.write_bytes(content_v2)

    result = cli([
        "--json", "webresource", "update", name,
        "--file", str(src_v2),
        "--display-name", display_v2,
    ])
    assert result.returncode == 0, (
        f"webresource update failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    upd = env["data"]
    assert upd.get("updated") is True, f"expected updated=True: {upd}"
    assert upd.get("webresourceid", "").lower() == wid.lower(), (
        f"update returned wrong webresourceid: {upd}"
    )

    # ── LIST ──────────────────────────────────────────────────────────────────
    # Use --custom-only --top 200 to avoid a full org scan; the newly created
    # (unmanaged) web resource must appear.
    result = cli(["--json", "webresource", "list", "--custom-only", "--top", "200"])
    assert result.returncode == 0, (
        f"webresource list failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    items = env["data"]
    assert isinstance(items, list), f"expected list, got {type(items)}: {env}"
    assert len(items) > 0, "webresource list returned empty list"
    names = {it.get("name") for it in items}
    assert name in names, (
        f"newly created web resource {name!r} not found in list results "
        f"(found {len(items)} items)"
    )
