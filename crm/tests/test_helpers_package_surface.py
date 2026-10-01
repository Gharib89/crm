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

# Symbols the command layer imports via `from crm.commands._helpers import ...`.
# Names only tests reach (`_infer_columns`) are not pinned here. A member with a
# single consuming command lives in that command module instead (#996).
_PUBLIC_SURFACE = [
    # rendering / output envelope
    "_sanitize",
    "_short_repr",
    "_emit_with_warning",
    "_emit_query_result",
    "_prune_annotations",
    "_emit_expectation_failure",
    # d365 errors
    "_handle_d365_error",
    "d365_errors",
    "usage_guard",
    # solution resolution
    "_resolve_solution",
    "_solution_option",
    "_publish_option",
    "_resolve_publish",
    "_active_profile",
    # confirm / secret UX
    "_confirm_destructive",
    "_destructive_option",
    "select_one",
    # admin headers
    "_admin_header_options",
    "_admin_kwargs",
    # input parsing / expectations
    "_load_payload",
    "_parse_expect",
    "_parse_value_labels",
    "_check_expectations",
    # session / journal
    "_journal",
    "_touch_session",
    "_no_retry_scope",
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
    assert hasattr(cm, "__enter__") and hasattr(cm, "__exit__"), (
        "d365_errors(...) must return a context manager"
    )


# Single-consumer members moved into their only command module (#996); none may
# drift back into the shared package.
_MOVED_OUT = [
    "_plaintext_secret_warning",
    "prompt_secret",
    "infer_auth_scheme",
    "default_profile_name",
    "_CASCADE",
    "_MENU",
    "_REQUIRED",
    "_optional_solution_option",
    "_resolve_schema_name",
    "encode_function_params",
    "_resolve_async_state",
    "_EXPORT_SETTING_KEYS",
]


@pytest.mark.parametrize("name", _MOVED_OUT)
def test_moved_member_not_exported(name):
    mod = importlib.import_module("crm.commands._helpers")
    assert not hasattr(mod, name), f"{name} belongs in its only consuming command module"
