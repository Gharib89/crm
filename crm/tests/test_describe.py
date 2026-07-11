"""`crm describe` — machine-readable command/option/choice discovery (#63).

Walks the live Click tree; needs no D365 connection. Tests assert the envelope
shape, that known Choice enums surface verbatim, and that the enumerated
top-level commands track the lazy-command registry.
"""

# pyright: basic
import json
import subprocess
import sys

from click.testing import CliRunner

from crm.cli import cli


def _describe(*args):
    """Invoke `crm describe ... --json` and return the parsed data envelope."""
    result = CliRunner().invoke(cli, ["--json", "describe", *args])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["ok"] is True, envelope
    return envelope["data"]


def _by_path(data, path):
    """Return the single command dict whose `path` matches, or fail."""
    matches = [c for c in data["commands"] if c["path"] == path]
    assert len(matches) == 1, f"expected exactly one {path!r}, got {len(matches)}"
    return matches[0]


def _param(cmd, name):
    matches = [p for p in cmd["params"] if p["name"] == name]
    assert len(matches) == 1, f"expected exactly one param {name!r} on {cmd['path']!r}"
    return matches[0]


def test_describe_json_enumerates_commands_without_connection():
    data = _describe()
    assert isinstance(data["commands"], list)
    assert data["commands"], "expected a non-empty command list"


def test_each_command_carries_args_and_params():
    data = _describe()
    install = _by_path(data, "skill install")
    assert isinstance(install["args"], list)
    assert isinstance(install["params"], list)
    # --force is a boolean flag; --dest is a plain value option.
    force = _param(install, "force")
    assert force["is_flag"] is True
    assert force["type"] == "boolean"
    assert force["multiple"] is False
    dest = _param(install, "dest")
    assert dest["is_flag"] is False
    # Every option dict exposes the full field set.
    assert set(force) >= {
        "name",
        "type",
        "required",
        "is_flag",
        "multiple",
        "choices",
        "default",
        "envvar",
    }


def _all_choice_lists(data):
    """Every non-None `choices` list across all commands' args and params."""
    out = []
    for cmd in data["commands"]:
        for p in (*cmd["args"], *cmd["params"]):
            if p["choices"] is not None:
                out.append(p["choices"])
    return out


def test_choice_enums_surface_verbatim():
    data = _describe()
    choice_lists = _all_choice_lists(data)
    # The 15 attribute kinds — exact order preserved.
    assert [
        "string",
        "memo",
        "integer",
        "bigint",
        "decimal",
        "double",
        "money",
        "boolean",
        "datetime",
        "picklist",
        "multiselect",
        "lookup",
        "customer",
        "image",
        "file",
    ] in choice_lists
    # --behavior DateTimeBehavior choices — exact order preserved.
    assert ["UserLocal", "DateOnly", "TimeZoneIndependent"] in choice_lists
    assert ["UserOwned", "OrganizationOwned"] in choice_lists  # ownership
    assert ["error", "skip"] in choice_lists  # --if-exists
    assert [  # cascade behaviors
        "NoCascade",
        "Cascade",
        "Active",
        "UserOwned",
        "RemoveLink",
        "Restrict",
    ] in choice_lists


def test_root_global_options_included():
    data = _describe()
    flags = {f for opt in data["root_options"] for f in opt["opts"]}
    for expected in [
        "--json",
        "--dry-run",
        "--profile",
        "--auth-scheme",
        "--log-level",
        "--stage-only",
        "--session",
    ]:
        assert expected in flags, f"missing sticky global {expected}"


def test_top_level_commands_track_lazy_registry_minus_repl():
    from crm.cli import _LazyJsonAwareGroup

    data = _describe()
    top = {c["name"] for c in data["commands"] if " " not in c["path"]}
    expected = set(_LazyJsonAwareGroup._lazy_commands) - {"repl"}
    assert top == expected
    assert "repl" not in top  # interactive leaf excluded


def test_describe_single_group_scopes_to_subtree():
    data = _describe("metadata")
    paths = {c["path"] for c in data["commands"]}
    assert "metadata" in paths
    assert "metadata add-attribute" in paths
    assert all(p == "metadata" or p.startswith("metadata ") for p in paths)
    # Sibling groups are absent — only the requested subtree is walked.
    assert not any(p == "entity" or p.startswith("entity ") for p in paths)


def test_describe_unknown_group_errors():
    result = CliRunner().invoke(cli, ["--json", "describe", "nope"])
    assert result.exit_code != 0
    assert json.loads(result.output)["ok"] is False


def test_flag_pair_secondary_opts_are_captured():
    """Boolean flag-pairs (e.g. --publish/--no-publish) must expose both forms,
    or the catalogue silently drops every --no-* flag the CLI accepts.
    """
    data = _describe()
    secondaries = {s for c in data["commands"] for p in c["params"] for s in p["secondary_opts"]}
    assert "--no-publish" in secondaries
    assert "--no-annotations" in secondaries


def test_describe_repl_excluded_even_when_named_explicitly():
    """The repl leaf is excluded from the catalogue everywhere — naming it
    explicitly must not bypass the exclusion.
    """
    result = CliRunner().invoke(cli, ["--json", "describe", "repl"])
    assert result.exit_code != 0
    assert json.loads(result.output)["ok"] is False


def test_no_unset_sentinel_leaks_into_defaults():
    """A param with no declared default must serialize as a real default or null —
    never Click's internal UNSET sentinel stringified to 'Sentinel.UNSET', which
    would mislead an agent generating invocations from the catalogue.
    """
    result = CliRunner().invoke(cli, ["--json", "describe"])
    assert result.exit_code == 0, result.output
    assert "Sentinel.UNSET" not in result.output
    data = json.loads(result.output)["data"]
    # An option with no declared default reports null, not the sentinel.
    export = _by_path(data, "data export")
    assert _param(export, "filter_")["default"] is None


def test_hidden_options_are_omitted():
    """Options marked hidden=True are excluded from the catalogue everywhere —
    the catalogue advertises only the documented public surface. `solution pack`
    carries a hidden `--solutionpackager-path` (deprecated alias) and
    `workflow export` a hidden `--out`; neither may appear.
    """
    data = _describe()
    pack = _by_path(data, "solution pack")
    assert "solutionpackager_path" not in {p["name"] for p in pack["params"]}
    # A visible sibling option on the same command is still present.
    assert "pac_path" in {p["name"] for p in pack["params"]}
    wf_export = _by_path(data, "workflow export")
    assert "out_path" not in {p["name"] for p in wf_export["params"]}


def test_describe_human_mode_lists_command_paths():
    result = CliRunner().invoke(cli, ["describe"])
    assert result.exit_code == 0, result.output
    assert "metadata add-attribute" in result.output


def test_describe_single_group_is_a_lazy_win():
    """`describe <group>` imports only that group's module, not its siblings —
    run in a subprocess so sys.modules starts clean.
    """
    probe = (
        "import sys, json\n"
        "from click.testing import CliRunner\n"
        "from crm.cli import cli\n"
        "r = CliRunner().invoke(cli, ['--json', 'describe', 'metadata'])\n"
        "siblings = ['crm.commands.entity', 'crm.commands.solution', 'crm.commands.workflow']\n"
        "leaked = sorted(m for m in siblings if m in sys.modules)\n"
        "print(json.dumps({'exit': r.exit_code, 'leaked': leaked}))\n"
    )
    proc = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["exit"] == 0
    assert data["leaked"] == [], f"describe metadata imported siblings: {data['leaked']}"
