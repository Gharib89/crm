"""CLI-layer tests for the metadata state-model / mapping commands:
status-add, state-relabel, create-mapping."""
# pyright: basic

from __future__ import annotations

import json

from click.testing import CliRunner

from crm.cli import cli


def _run(args, inject_backend, fake, tmp_path):
    inject_backend(fake)
    return CliRunner().invoke(cli, args, env={"CRM_HOME": str(tmp_path)})


class TestStatusAdd:
    def test_inserts_status(self, make_fake_backend, inject_backend, tmp_path):
        fake = make_fake_backend(responses={"post": {"NewOptionValue": 12}})
        res = _run(
            ["--json", "metadata", "status-add", "new_widget",
             "--solution", "MySol",
             "--state", "0", "--label", "Pending", "--no-publish"],
            inject_backend, fake, tmp_path,
        )
        assert res.exit_code == 0, res.output
        data = json.loads(res.output)["data"]
        assert data["added"] is True
        assert data["value"] == 12
        verb, path, kw = fake.calls[0]
        assert path == "InsertStatusValue"
        assert kw["json_body"]["StateCode"] == 0

    def test_dry_run_previews(self, make_fake_backend, inject_backend, tmp_path):
        fake = make_fake_backend(dry_run=True)
        res = _run(
            ["--json", "--dry-run", "metadata", "status-add", "new_widget",
             "--solution", "MySol",
             "--state", "0", "--label", "Pending"],
            inject_backend, fake, tmp_path,
        )
        assert res.exit_code == 0, res.output
        assert json.loads(res.output)["meta"]["dry_run"] is True


class TestStateRelabel:
    def test_relabels(self, make_fake_backend, inject_backend, tmp_path):
        fake = make_fake_backend(responses={"post": None})
        res = _run(
            ["--json", "metadata", "state-relabel", "new_widget",
             "--solution", "MySol",
             "--value", "1", "--label", "Dormant", "--no-publish"],
            inject_backend, fake, tmp_path,
        )
        assert res.exit_code == 0, res.output
        assert json.loads(res.output)["data"]["updated"] is True
        assert fake.calls[0][1] == "UpdateStateValue"

    def test_dry_run_previews(self, make_fake_backend, inject_backend, tmp_path):
        fake = make_fake_backend(dry_run=True)
        res = _run(
            ["--json", "--dry-run", "metadata", "state-relabel", "new_widget",
             "--solution", "MySol",
             "--value", "1", "--label", "Dormant"],
            inject_backend, fake, tmp_path,
        )
        assert res.exit_code == 0, res.output
        assert json.loads(res.output)["meta"]["dry_run"] is True


class TestCreateMapping:
    def _pair_backend(self, make_fake_backend):
        def _get(path):
            if path.startswith("RelationshipDefinitions"):
                return {"ReferencedEntity": "account", "ReferencingEntity": "new_widget"}
            if path == "entitymaps":
                return {"value": [{"entitymapid": "abc"}]}
            return {"value": []}
        return make_fake_backend(responses={"get": _get, "post": None})

    def test_create_from_to(self, make_fake_backend, inject_backend, tmp_path):
        fake = self._pair_backend(make_fake_backend)
        res = _run(
            ["--json", "metadata", "create-mapping", "new_account_new_widget",
             "--solution", "MySol",
             "--from", "name", "--to", "new_name"],
            inject_backend, fake, tmp_path,
        )
        assert res.exit_code == 0, res.output
        assert json.loads(res.output)["data"]["created"] is True
        assert any(p == "attributemaps" for _v, p, _k in fake.calls)

    def test_auto(self, make_fake_backend, inject_backend, tmp_path):
        fake = self._pair_backend(make_fake_backend)
        res = _run(
            ["--json", "metadata", "create-mapping", "new_account_new_widget",
             "--solution", "MySol", "--auto"],
            inject_backend, fake, tmp_path,
        )
        assert res.exit_code == 0, res.output
        assert json.loads(res.output)["data"]["auto_mapped"] is True
        assert any(p == "AutoMapEntity" for _v, p, _k in fake.calls)

    def test_dry_run_previews(self, make_fake_backend, inject_backend, tmp_path):
        fake = self._pair_backend(make_fake_backend)
        fake.dry_run = True
        res = _run(
            ["--json", "--dry-run", "metadata", "create-mapping",
             "new_account_new_widget", "--solution", "MySol",
             "--from", "name", "--to", "new_name"],
            inject_backend, fake, tmp_path,
        )
        assert res.exit_code == 0, res.output
        assert json.loads(res.output)["meta"]["dry_run"] is True

    def test_auto_with_from_is_usage_error(self, make_fake_backend, inject_backend, tmp_path):
        fake = make_fake_backend()
        res = _run(
            ["metadata", "create-mapping", "rel", "--solution", "MySol",
             "--auto", "--from", "x"],
            inject_backend, fake, tmp_path,
        )
        assert res.exit_code != 0
        assert "--auto cannot be combined" in res.output

    def test_missing_from_to_is_usage_error(self, make_fake_backend, inject_backend, tmp_path):
        fake = make_fake_backend()
        res = _run(
            ["metadata", "create-mapping", "rel", "--solution", "MySol",
             "--from", "x"],
            inject_backend, fake, tmp_path,
        )
        assert res.exit_code != 0
        assert "both --from and --to" in res.output
