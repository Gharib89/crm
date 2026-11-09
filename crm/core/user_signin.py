"""Delegated sign-in (``oauth-user``): interactive login and silent token reuse (ADR 0035).

``login`` is the only interactive entry (``crm profile login``). Every other
command goes through ``UserBearerAuth``, which takes a token silently from the
encrypted cache (``token_store``) and, when it cannot, raises a stable
``reauth_required`` error. It never opens a browser and never prints a token.
"""

from __future__ import annotations

import urllib.parse
import webbrowser
from collections.abc import Callable
from typing import Any

from crm.core import token_store
from crm.utils.d365_backend import ConnectionProfile, D365Error

REAUTH_CODE = "reauth_required"
"""``D365Error.code`` of every silent-acquisition failure; stable for scripts."""

_AUTHORITY = "https://login.microsoftonline.com/{}"
# Consent errors and the Conditional Access device-code block get a message
# naming the fix; matched on the AADSTS code MSAL passes through verbatim.
_CONSENT_CODES = ("AADSTS65001", "AADSTS90094")
_CA_CODES = ("AADSTS53003", "AADSTS530036")

BROWSER_SIGNIN_TIMEOUT_S = 300
"""How long the browser flow waits for the loopback redirect. MSAL defaults to
forever, and an Entra page that never redirects back (the admin approval page)
would hang the command silently; 300 s leaves room for MFA and a password manager."""


def scope_for(profile: ConnectionProfile) -> str:
    """The org's delegated Dataverse scope (``user_impersonation``)."""
    host = urllib.parse.urlparse(profile.url).netloc
    return f"https://{host}/user_impersonation"


def _reauth(profile: ConnectionProfile, why: str) -> D365Error:
    return D365Error(
        f"Delegated sign-in for profile {profile.name!r} is not usable: {why}. "
        f"Run crm profile login {profile.name}",
        status=401,
        code=REAUTH_CODE,
    )


def _build_app(profile: ConnectionProfile, cache: Any) -> Any:
    """A public-client app on the profile's tenant (MSAL checks the authority online)."""
    import msal  # type: ignore[import-untyped]

    if not profile.tenant_id or not profile.client_id:
        raise D365Error(
            f"Profile {profile.name!r} needs tenant_id and client_id for delegated sign-in "
            "(set them with crm profile edit)."
        )
    msal_any: Any = msal
    return msal_any.PublicClientApplication(
        profile.client_id,
        authority=_AUTHORITY.format(profile.tenant_id),
        token_cache=cache,
    )


def _load_cache(profile: ConnectionProfile) -> Any:
    import msal  # type: ignore[import-untyped]

    cache: Any = msal.SerializableTokenCache()
    blob = token_store.load(profile.name)
    if blob:
        cache.deserialize(blob)
    return cache


def _save_cache(profile: ConnectionProfile, cache: Any) -> str | None:
    if not getattr(cache, "has_state_changed", False):
        return None
    return token_store.save(profile.name, cache.serialize())


class UserBearerAuth:
    """``requests`` auth hook: a silently acquired delegated bearer token per request.

    The cache and MSAL app are built once, on the first request: building the app
    checks the authority online, and MSAL serves a valid token from memory after.
    """

    def __init__(self, profile: ConnectionProfile) -> None:
        self._profile = profile
        self._cache: Any = None
        self._app: Any = None

    def token(self) -> str:
        """The bound account's access token, refreshed from the cache when due."""
        profile = self._profile
        if not profile.home_account_id:
            raise _reauth(profile, "no account is signed in")
        try:
            if self._app is None:
                self._cache = _load_cache(profile)
                self._app = _build_app(profile, self._cache)
            account = next(
                (
                    a
                    for a in self._app.get_accounts()
                    if a.get("home_account_id") == profile.home_account_id
                ),
                None,
            )
            if account is None:
                raise _reauth(profile, "the signed-in account is not in the token cache")
            result = self._app.acquire_token_silent_with_error(
                [scope_for(profile)], account=account
            )
        except D365Error:
            raise
        except Exception as exc:
            raise _reauth(profile, f"token refresh failed ({type(exc).__name__})") from exc
        res: dict[str, Any] = result or {}
        token = res.get("access_token")
        if not token:
            err = res.get("error", "no token returned")
            raise _reauth(profile, f"the cached sign-in was rejected ({err})")
        try:
            _save_cache(profile, self._cache)
        except OSError:
            pass  # the token is in hand; an unwritable cache only costs the next run a refresh
        return str(token)

    def __call__(self, request: Any) -> Any:
        request.headers["Authorization"] = f"Bearer {self.token()}"
        return request


def acquire_silent(profile: ConnectionProfile) -> str:
    """One silent acquisition on a fresh app (a convenience for one-shot callers)."""
    return UserBearerAuth(profile).token()


def _fix_for(result: dict[str, Any], *, device_code: bool) -> str | None:
    text = f"{result.get('error', '')} {result.get('error_description', '')}"
    if any(code in text for code in _CONSENT_CODES):
        return (
            "the app registration lacks consent for Dataverse: have an administrator grant "
            "admin consent to its user_impersonation permission (see "
            "the delegated sign-in how-to in the crm docs)"
        )
    if device_code and any(code in text for code in _CA_CODES):
        return (
            "Conditional Access blocks device code sign-in for this tenant: "
            "run crm profile login without --device-code on a machine with a browser"
        )
    return None


def _no_browser() -> bool:
    try:
        webbrowser.get()
    except webbrowser.Error:
        return True
    return False


def _interactive(
    app: Any, scopes: list[str], login_hint: str | None, announce: Callable[[str], None]
) -> dict[str, Any]:
    """The bounded browser flow.

    MSAL raises, rather than returns an error, when the wait runs out: a
    ``RuntimeError`` subclass (``BrowserInteractionTimeoutError``) from msal 1.30, a plain
    ``RuntimeError`` before it, and setup.py allows ``msal>=1.20``. Other rare msal
    ``RuntimeError``s on this path (ID token checks) land here too, so the message carries
    msal's own text.
    """

    def before_ui(**_kw: Any) -> None:
        announce(
            "A browser window opened for sign-in; crm is waiting for you to finish "
            f"(gives up after {BROWSER_SIGNIN_TIMEOUT_S} s)."
        )

    try:
        result: dict[str, Any] = app.acquire_token_interactive(
            scopes,
            login_hint=login_hint,
            timeout=BROWSER_SIGNIN_TIMEOUT_S,
            on_before_launching_ui=before_ui,
        )
    except RuntimeError as exc:
        raise D365Error(
            f"Sign-in failed: the browser sign-in was not finished within "
            f"{BROWSER_SIGNIN_TIMEOUT_S} s. Either it was not completed in the browser, or "
            "Entra stopped on a page that never returns to crm, such as 'Approval required': "
            "an administrator must grant admin consent to the app's Dataverse "
            "user_impersonation permission (see the delegated sign-in how-to in the crm "
            f"docs). Finish the sign-in or get consent, then run the login again. (msal: {exc})",
            status=401,
            code="login_failed",
        ) from exc
    return result


def login(
    profile: ConnectionProfile,
    *,
    device_code: bool,
    announce: Callable[[str], None] = lambda _msg: None,
) -> dict[str, Any]:
    """Sign in and return ``{account, username, tid, storage, previous}``.

    Refuses a token from a tenant other than the profile's ``tenant_id``, which
    MSAL itself accepts; nothing is saved then. ``previous`` is the home account id
    this sign-in replaces, or ``None``.
    """
    if not device_code and _no_browser():
        raise D365Error(
            "No web browser is available for sign-in. Re-run with --device-code to sign "
            "in from another device.",
            code="no_browser",
        )
    cache = _load_cache(profile)
    app = _build_app(profile, cache)
    scopes = [scope_for(profile)]
    if device_code:
        flow = app.initiate_device_flow(scopes=scopes)
        if "user_code" not in flow:
            result: dict[str, Any] = flow
        else:
            announce(str(flow["message"]))
            result = app.acquire_token_by_device_flow(flow)
    else:
        result = _interactive(app, scopes, profile.username or None, announce)
    if "access_token" not in result:
        fix = _fix_for(result, device_code=device_code)
        detail = (
            fix or f"{result.get('error', 'unknown_error')}: {result.get('error_description', '')}"
        )
        raise D365Error(f"Sign-in failed: {detail}".strip(), status=401, code="login_failed")
    claims: dict[str, Any] = result.get("id_token_claims") or {}
    tid = str(claims.get("tid", ""))
    if tid.lower() != (profile.tenant_id or "").lower():
        raise D365Error(
            f"Signed in to tenant {tid or '(unknown)'}, but profile {profile.name!r} has "
            f"tenant_id {profile.tenant_id}. Nothing was saved; set the profile's tenant_id "
            "to the tenant's GUID (crm profile edit) or sign in with an account of that tenant.",
            status=401,
            code="wrong_tenant",
        )
    accounts = app.get_accounts(username=claims.get("preferred_username"))
    account: dict[str, Any] = accounts[0] if accounts else {}
    home_id = str(account.get("home_account_id") or "")
    if not home_id:
        raise D365Error("Sign-in returned no account to bind.", status=401, code="login_failed")
    previous = profile.home_account_id if profile.home_account_id != home_id else None
    if previous:
        for stale in app.get_accounts():
            if stale.get("home_account_id") == previous:
                app.remove_account(stale)
    try:
        storage = _save_cache(profile, cache)
    except OSError as exc:
        raise D365Error(
            f"Signed in, but the token cache could not be written ({exc.strerror or exc}). "
            "Fix the state home's permissions or free space, then run the login again.",
            code="cache_write_failed",
        ) from exc
    return {
        "account": home_id,
        "username": str(account.get("username") or claims.get("preferred_username") or ""),
        "tid": tid,
        "storage": storage,
        "previous": previous,
    }
