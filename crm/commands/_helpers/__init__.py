"""Shared helpers used across crm.commands.* (#271).

Formerly a single ~630-line module, now a package of cohesive submodules — one
per concern. This ``__init__`` re-exports the full flat namespace, so every
existing ``from crm.commands._helpers import <name>`` keeps resolving
identically off the package. Pure reorganization: no behavior, signature, or
call-site change.

Cross-submodule callers import from their sibling submodule directly (not back
through this ``__init__``) to keep the package import-cycle-free.
"""

# pyright: basic
from __future__ import annotations

from crm.commands._tty import _stdin_is_tty

# Module references kept at the package top level for fidelity with the old
# single-module namespace. `session_mod` is monkeypatched in the test suite via
# `_helpers.session_mod.load_profile`, and is the same module object the
# solution/session submodules use, so the patch stays visible to them.
from crm.core import session as session_mod

from .admin import (
    _admin_header_options,
    _admin_kwargs,
)
from .confirm import (
    _confirm_destructive,
    _destructive_option,
    _plaintext_secret_warning,
    prompt_secret,
    select_one,
)
from .errors import (
    _auth_error_hint,
    _handle_d365_error,
    d365_errors,
    usage_guard,
)
from .options import (
    _output_option,
)
from .parsing import (
    _CASCADE,
    _MENU,
    _REQUIRED,
    _check_expectations,
    _load_payload,
    _odata_literal,
    _parse_expect,
    _parse_value_labels,
    _read_file,
    _resolve_async_state,
    encode_function_params,
)
from .profiles import (
    default_profile_name,
    infer_auth_scheme,
)
from .rendering import (
    _apply_jq,
    _concise_record,
    _emit_expectation_failure,
    _emit_query_result,
    _emit_with_warning,
    _infer_columns,
    _normalize_odata_envelope,
    _project_fields,
    _project_table_columns,
    _prune_annotations,
    _sanitize,
    _short_repr,
    _strip_odata_keys,
)
from .session import (
    _journal,
    _no_retry_scope,
    _touch_session,
)
from .solutions import (
    _EXPORT_SETTING_KEYS,
    _active_profile,
    _optional_solution_option,
    _publish_option,
    _resolve_publish,
    _resolve_schema_name,
    _resolve_solution,
    _solution_option,
)

__all__ = [
    # rendering / output envelope
    "_sanitize",
    "_strip_odata_keys",
    "_concise_record",
    "_normalize_odata_envelope",
    "_short_repr",
    "_emit_with_warning",
    "_emit_query_result",
    "_infer_columns",
    "_prune_annotations",
    "_emit_expectation_failure",
    "_project_fields",
    "_project_table_columns",
    "_apply_jq",
    # generic option decorators
    "_output_option",
    # d365 errors
    "_handle_d365_error",
    "d365_errors",
    "usage_guard",
    "_auth_error_hint",
    # solution resolution
    "_resolve_solution",
    "_solution_option",
    "_optional_solution_option",
    "_publish_option",
    "_resolve_publish",
    "_active_profile",
    "_resolve_schema_name",
    "_EXPORT_SETTING_KEYS",
    # confirm / secret UX
    "_confirm_destructive",
    "_destructive_option",
    "_plaintext_secret_warning",
    "select_one",
    "prompt_secret",
    # admin headers
    "_admin_header_options",
    "_admin_kwargs",
    # input parsing / expectations
    "_load_payload",
    "_read_file",
    "_parse_expect",
    "_parse_value_labels",
    "_check_expectations",
    "_odata_literal",
    "encode_function_params",
    "_resolve_async_state",
    "_CASCADE",
    "_MENU",
    "_REQUIRED",
    # profile inference
    "infer_auth_scheme",
    "default_profile_name",
    # session / journal
    "_journal",
    "_touch_session",
    "_no_retry_scope",
]
