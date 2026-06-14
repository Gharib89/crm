"""Admin-header CLI option stacking + kwargs resolution."""
# pyright: basic
from __future__ import annotations
from typing import Any
import click


def _admin_header_options(f):
    """Stack `--as-user`, `--as-user-object-id`, `--suppress-dup-detection`, `--bypass-plugins`."""
    f = click.option(
        "--bypass-plugins", is_flag=True, default=False,
        help="Send MSCRM.BypassCustomPluginExecution: true (requires prvBypassCustomPluginExecution).",
    )(f)
    f = click.option(
        "--suppress-dup-detection", is_flag=True, default=False,
        help="Send MSCRM.SuppressDuplicateDetection: true.",
    )(f)
    f = click.option(
        "--as-user-object-id", "as_user_object_id", metavar="GUID", default=None,
        help="Impersonate by Entra ID object id (cloud) via CallerObjectId header. "
             "Mutually exclusive with --as-user.",
    )(f)
    f = click.option(
        "--as-user", "as_user", metavar="GUID", default=None,
        help="Impersonate systemuser by GUID via MSCRMCallerID header. "
             "Mutually exclusive with --as-user-object-id.",
    )(f)
    return f


def _admin_kwargs(as_user: str | None, as_user_object_id: str | None,
                  suppress_dup_detection: bool,
                  bypass_plugins: bool) -> dict[str, Any]:
    """Resolve admin-header CLI flags into backend kwargs.

    `is_flag` defaults to False (flag absent). To preserve the backend's
    tri-state semantics (None = use env default like CRM_SUPPRESS_DUP /
    CRM_BYPASS_PLUGINS), we forward True only when the flag was actually
    set on the command line; otherwise None lets the backend env default
    take effect.

    `caller_id` (--as-user, MSCRMCallerID) and `caller_object_id`
    (--as-user-object-id, CallerObjectId) are forwarded as-is; the backend
    enforces that at most one resolves per request.
    """
    return {
        "caller_id": as_user,
        "caller_object_id": as_user_object_id,
        "suppress_duplicate_detection": True if suppress_dup_detection else None,
        "bypass_custom_plugin_execution": True if bypass_plugins else None,
    }
