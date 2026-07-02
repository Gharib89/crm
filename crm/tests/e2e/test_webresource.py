# pyright: basic
"""E2E tests for webresource verbs: list / get / create / update."""
from __future__ import annotations

import json

import pytest

from crm.tests.e2e.coverage import covers


# ── delete ───────────────────────────────────────────────────────────────────


@covers("webresource delete")
@pytest.mark.slow
def test_webresource_delete(cli, tmp_path, unique, request, ephemeral_solution):
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
        "--solution", ephemeral_solution,
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
def test_webresource_lifecycle(cli, tmp_path, unique, request, ephemeral_solution):
    """Full lifecycle: create a JS web resource, get it, update its display name,
    confirm via list, then delete in a finalizer.

    Uses a minimal JS content (a comment) so there is no script-engine
    interference. `--publish` is omitted, so the create stages (the default
    post-#633); the web-resource record is still verifiable by a subsequent get
    without publishing.
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
        "--solution", ephemeral_solution,
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
        "--solution", ephemeral_solution,
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


# ── push (directory upsert) ──────────────────────────────────────────────────


@covers("webresource push")
@pytest.mark.slow
def test_webresource_push_directory(cli, tmp_path, unique, request, ephemeral_solution):
    """Walk a directory and upsert each file: create on first push, skip when
    byte-identical, update when changed, and preview under --dry-run.

    Names are derived by convention: ``<prefix>_<relpath>``. The unique id is
    folded into the relative path so the resource names are unique per run. A
    finalizer best-effort deletes every name so the org is left clean.
    """
    prefix = "e2e"
    root = tmp_path / "wr"
    rel_js = f"{unique}/app.js"
    rel_css = f"{unique}/styles/site.css"
    rel_new = f"{unique}/added.js"
    name_js = f"{prefix}_{rel_js}"
    name_css = f"{prefix}_{rel_css}"
    name_new = f"{prefix}_{rel_new}"

    (root / unique / "styles").mkdir(parents=True)
    (root / rel_js).write_bytes(b"// e2e push v1")
    (root / rel_css).write_bytes(b"body{color:red}")

    def _cleanup():
        for n in (name_js, name_css, name_new):
            try:
                cli(["--json", "webresource", "delete", n, "--yes"], check=False)
            except Exception:
                pass
    request.addfinalizer(_cleanup)

    # ── FIRST PUSH: both files created, published once (--publish opt-in) ────────
    result = cli(["--json", "webresource", "push", str(root), "--prefix", prefix,
                  "--publish", "--solution", ephemeral_solution])
    assert result.returncode == 0, (
        f"first push failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    data = env["data"]
    assert data["pushed"] == 2, f"expected 2 created: {data}"
    assert data["updated"] == 0 and data["skipped"] == 0, data
    assert data["published"] is True, data
    # both resources now resolvable
    for n in (name_js, name_css):
        got = cli(["--json", "webresource", "get", n])
        assert got.returncode == 0 and json.loads(got.stdout)["ok"], got.stdout

    # ── RE-PUSH IDENTICAL: both skipped, no writes ──────────────────────────────
    result = cli(["--json", "webresource", "push", str(root), "--prefix", prefix,
                  "--solution", ephemeral_solution])
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)["data"]
    assert data["skipped"] == 2, f"expected 2 skipped: {data}"
    assert data["pushed"] == 0 and data["updated"] == 0, data
    assert data["published"] is False, data

    # ── CHANGE ONE FILE: one updated, one skipped ───────────────────────────────
    (root / rel_js).write_bytes(b"// e2e push v2 changed")
    result = cli(["--json", "webresource", "push", str(root), "--prefix", prefix,
                  "--publish", "--solution", ephemeral_solution])
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)["data"]
    assert data["updated"] == 1, f"expected 1 updated: {data}"
    assert data["skipped"] == 1, data
    assert data["published"] is True, data

    # ── DRY-RUN A NEW FILE: previewed, not written ──────────────────────────────
    (root / rel_new).write_bytes(b"// added, dry-run only")
    result = cli(["--json", "--dry-run", "webresource", "push", str(root),
                  "--prefix", prefix, "--solution", ephemeral_solution])
    assert result.returncode == 0, result.stderr
    env = json.loads(result.stdout)
    data = env["data"]
    assert name_new in data["would_create"], f"new file not previewed: {data}"
    assert env["meta"]["dry_run"] is True, env
    # the dry-run must not have created it
    got = cli(["--json", "webresource", "get", name_new], check=False)
    assert got.returncode != 0, f"dry-run wrote {name_new}: {got.stdout}"
