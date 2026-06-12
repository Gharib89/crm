# pyright: basic
"""Walker test for the e2e coverage gate (leaf-count sanity check)."""

from crm.tests.e2e.coverage import walk_commands


def test_walk_finds_all_leaves_via_lazy_loader():
    leaves = set(walk_commands())
    # Sanity floor — the keystone rail. A naive `.commands` walk on the lazy root
    # returns 0; the real walker must drive list_commands/get_command.
    assert len(leaves) > 100, f"walk returned only {len(leaves)} leaves; lazy load broke"
    # Known deep leaves across groups:
    for leaf in ("entity create", "metadata add-attribute", "workflow activate",
                 "solution export", "query odata"):
        assert leaf in leaves, f"missing {leaf!r} from walk: {sorted(leaves)[:20]}..."


import importlib
import pkgutil

import crm.tests.e2e as e2e_pkg
from crm.tests.e2e.coverage import COVERED, E2E_SKIP, LOCAL_GROUPS


def _import_all_e2e_test_modules():
    """Populate COVERED by importing every e2e test module. Auto-discovered so a
    new test file is never silently uncounted. Imports must be side-effect-free
    (no module-level skipif touching live env, no connection at import)."""
    for mod in pkgutil.walk_packages(e2e_pkg.__path__, e2e_pkg.__name__ + "."):
        name = mod.name.rsplit(".", 1)[-1]
        if name.startswith("test_"):
            importlib.import_module(mod.name)


def _expected(walked: set[str]) -> set[str]:
    return {lf for lf in walked if lf.split(" ", 1)[0] not in LOCAL_GROUPS} - set(E2E_SKIP)


def test_every_d365_command_has_e2e_coverage():
    _import_all_e2e_test_modules()
    walked = set(walk_commands())
    assert len(walked) > 100, f"walk returned {len(walked)} leaves; lazy load broke"
    missing = _expected(walked) - COVERED
    assert not missing, (
        "D365 commands lacking e2e coverage (add a @covers test or an E2E_SKIP "
        "entry with a reason):\n  " + "\n  ".join(sorted(missing))
    )


def test_no_stale_skips_or_local_groups():
    walked = set(walk_commands())
    first_tokens = {lf.split(" ", 1)[0] for lf in walked}
    stale_local = LOCAL_GROUPS - first_tokens
    stale_skip = set(E2E_SKIP) - walked
    assert not stale_local, f"LOCAL_GROUPS no longer in CLI: {sorted(stale_local)}"
    assert not stale_skip, f"E2E_SKIP entries no longer in CLI: {sorted(stale_skip)}"
