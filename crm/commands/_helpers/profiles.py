"""Profile-UX inference helpers (credential revamp)."""
# pyright: basic
from __future__ import annotations
import urllib.parse


# Dataverse online hosts always end in this suffix (crm.dynamics.com,
# crm4.dynamics.com, crm.dynamics.cn, ...). Anything else is treated as on-prem.
_CLOUD_HOST_MARKER = ".dynamics."


def infer_auth_scheme(url: str) -> str:
    """Guess the auth scheme from the server URL: oauth for Dataverse online
    (`*.dynamics.*`), else ntlm. The wizard shows this as an overridable default."""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return "oauth" if _CLOUD_HOST_MARKER in host else "ntlm"


def default_profile_name(url: str) -> str:
    """Default profile name = the first label of the URL host (`crm.contoso.local`
    -> `crm`, `orgd080.crm.dynamics.com` -> `orgd080`). Falls back to 'default'
    when the URL has no parseable host."""
    host = urllib.parse.urlparse(url).hostname or ""
    label = host.split(".")[0] if host else ""
    return label or "default"
