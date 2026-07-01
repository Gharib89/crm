"""Guards the public import surface of ``crm.commands._helpers`` (#271).

``_helpers`` was split from a single module into a re-exporting package. This
test pins the flat namespace contract: every symbol the rest of the tree
imports via ``from crm.commands._helpers import <name>`` must keep resolving
off the package, identically to the old module. A regression here means a
command module (or another test) would fail at import time.

The expected set is the union of every distinct name imported from
``crm.commands._helpers`` anywhere in the tree at the time of the split. It is
hard-coded on purpose: this test is the canary, so adding a name here should be
a deliberate act, not an automatic mirror of whatever the package happens to
expose.
"""
# pyright: basic
from __future__ import annotations

import importlib

import pytest

# Every symbol imported via `from crm.commands._helpers import ...` across the
# whole tree (commands + tests), plus the two module references the old module
# exposed at top level (`session_mod`, `_stdin_is_tty`) that the suite
# monkeypatches through the package. Internal-only names (`_infer_columns`,
# `_ASYNC_STATE_NAMES`, `_CLOUD_HOST_MARKER`) are deliberately
# NOT re-exported — nothing imports them — so they are not pinned here.
_PUBLIC_SURFACE = [
    # rendering / output envelope
    "_sanitize", "_short_repr", "_emit_with_warning", "_emit_query_result",
    "_infer_columns", "_prune_annotations", "_emit_expectation_failure",
    # d365 errors
    "_handle_d365_error", "d365_errors", "usage_guard", "_auth_error_hint",
    # solution resolution
    "_resolve_solution", "_solution_option",
    "_publish_option", "_resolve_publish", "_active_profile",
    "_resolve_schema_name", "_EXPORT_SETTING_KEYS",
    # confirm / secret UX
    "_confirm_destructive", "_destructive_option", "_plaintext_secret_warning",
    "select_one",
    # admin headers
    "_admin_header_options", "_admin_kwargs",
    # input parsing / expectations
    "_load_payload", "_parse_expect", "_parse_value_labels",
    "_check_expectations", "_odata_literal",
    "_resolve_async_state", "_CASCADE", "_MENU", "_REQUIRED",
    # profile inference
    "infer_auth_scheme", "default_profile_name",
    # session / journal
    "_journal", "_touch_session", "_no_retry_scope",
    # module references preserved at the package top level (monkeypatch targets)
    "session_mod", "_stdin_is_tty",
]


@pytest.mark.parametrize("name", _PUBLIC_SURFACE)
def test_symbol_resolves_from_package(name):
    mod = importlib.import_module("crm.commands._helpers")
    assert hasattr(mod, name), f"{name} no longer resolves from crm.commands._helpers"


def test_d365_errors_seam_is_a_context_manager():
    # The #264 seam relocated into the errors submodule; it must still be the
    # context-manager factory the ~21 verb call sites use as `with d365_errors(ctx):`.
    # Assert the context-manager protocol rather than a concrete (and private,
    # version-dependent) type, since that protocol is the actual contract.
    from crm.commands._helpers import d365_errors

    cm = d365_errors(object())  # type: ignore[arg-type]
    assert hasattr(cm, "__enter__") and hasattr(cm, "__exit__"), \
        "d365_errors(...) must return a context manager"


def test_session_mod_attribute_is_core_session():
    # test_solution_targeting patches `_helpers.session_mod.load_profile`; the
    # package must keep exposing `session_mod` bound to the real core module.
    import crm.commands._helpers as helpers
    from crm.core import session as core_session

    assert helpers.session_mod is core_session
