"""Shared helpers used across crm.commands.* (#271).

Formerly a single ~630-line module, now a package of cohesive submodules — one
per concern. This ``__init__`` re-exports them as one flat namespace, so callers
write ``from crm.commands._helpers import <name>``. A helper with a single
consuming command lives in that command module instead (#996).

Cross-submodule callers import from their sibling submodule directly (not back
through this ``__init__``) to keep the package import-cycle-free.
"""

# pyright: basic
from __future__ import annotations

from .admin import (
    _admin_header_options,
    _admin_kwargs,
)
from .confirm import (
    _confirm_destructive,
    _destructive_option,
    select_one,
)
from .errors import (
    _handle_d365_error,
    d365_errors,
    usage_guard,
)
from .options import (
    _output_option,
    in_use_options,
)
from .parsing import (
    EmptyAllowedOption,
    _check_expectations,
    _load_payload,
    _parse_expect,
    _parse_value_labels,
    _read_file,
    reject_empty_values,
    require_exactly_one,
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
    _active_profile,
    _publish_option,
    _resolve_publish,
    _solution_option,
)

__all__ = [
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
    "_output_option",
    "in_use_options",
    "_handle_d365_error",
    "d365_errors",
    "usage_guard",
    "_solution_option",
    "_publish_option",
    "_resolve_publish",
    "_active_profile",
    "_confirm_destructive",
    "_destructive_option",
    "select_one",
    "_admin_header_options",
    "_admin_kwargs",
    "_load_payload",
    "_read_file",
    "require_exactly_one",
    "EmptyAllowedOption",
    "reject_empty_values",
    "_parse_expect",
    "_parse_value_labels",
    "_check_expectations",
    "_journal",
    "_touch_session",
    "_no_retry_scope",
]
