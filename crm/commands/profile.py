"""`crm profile` — create, switch, and manage connection profiles."""
# pyright: basic
from __future__ import annotations

import click

from crm.cli import CLIContext, pass_ctx
from crm.commands._tty import _stdin_is_tty
from crm.core import connection as conn_mod
from crm.core import keyring_store
from crm.core import session as session_mod
from crm.commands._helpers import (
    _handle_d365_error,
    d365_errors,
    _plaintext_secret_warning,
    _confirm_destructive,
    infer_auth_scheme,
    default_profile_name,
    select_one,
)
from crm.utils.d365_backend import ConnectionProfile, D365Error, validate_profile_name

# A profile file can be missing (FileNotFoundError), unreadable (OSError),
# non-JSON (ValueError → json.JSONDecodeError), or JSON-valid but malformed —
# from_dict does d["name"]/d["url"]/d["username"] (KeyError), .rstrip on a
# non-str (AttributeError/TypeError), and __post_init__ validates the auth
# scheme / name (D365Error). Treat all of these as "this one profile is
# unusable" so a single bad file never crashes list/use.
_PROFILE_LOAD_ERRORS = (
    FileNotFoundError, OSError, ValueError, KeyError, TypeError,
    AttributeError, D365Error,
)


@click.group("profile")
def profile_group():
    """Create, switch, and manage connection profiles."""


def _resolve_secret_flag(password_opt, client_secret_opt):
    """Collapse the --password / --client-secret aliases into one secret.

    The two name the same field (NTLM password vs OAuth client secret); passing
    both is a usage error (exit 2 per the house rule for mutually-exclusive flags)
    rather than a silent last-wins."""
    if password_opt is not None and client_secret_opt is not None:
        raise click.UsageError("--password and --client-secret are mutually exclusive.")
    return password_opt if password_opt is not None else client_secret_opt


def _validate_prefix_opt(_ctx, _param, value):
    """Validate --publisher-prefix at parse time so a bad value fails as a usage
    error (exit 2). An empty/omitted prefix is allowed (skip = no default prefix);
    the wizard re-prompts on invalid instead of erroring."""
    if not value:
        return value
    from crm.core.solution import validate_customization_prefix
    try:
        validate_customization_prefix(value)
    except D365Error as exc:
        raise click.BadParameter(str(exc))
    return value


@profile_group.command("add")
@click.option("--url", default=None, help="Server URL, e.g. https://crm.contoso.local/org "
              "or https://org.crm.dynamics.com")
@click.option("--name", "name_opt", default=None, help="Profile name (default: URL host label).")
@click.option("--auth-scheme", "auth_opt",
              type=click.Choice(["ntlm", "kerberos", "negotiate", "oauth"]),
              default=None, help="Override the auth scheme inferred from the URL.")
@click.option("--username", default=None, help="NTLM: username.")
@click.option("--domain", default=None, help="NTLM: AD domain (blank for UPN).")
@click.option("--tenant-id", default=None, help="OAuth: Azure AD tenant id.")
@click.option("--client-id", default=None, help="OAuth: application (client) id.")
@click.option("--password", "password_opt", default=None,
              help="NTLM password (or OAuth client secret). Prompted if omitted on a TTY.")
@click.option("--client-secret", "client_secret_opt", default=None,
              help="OAuth client secret — alias for --password (mutually exclusive).")
@click.option("--api-version", default=None,
              help="Web API version. Omit to auto-negotiate (v9.2 → v9.1 on on-prem).")
@click.option("--no-verify-ssl", is_flag=True, help="Skip SSL certificate verification.")
@click.option("--publisher-prefix", default=None, callback=_validate_prefix_opt,
              help="Default schema-name prefix, e.g. 'new'.")
@click.option("--store-password-plaintext", is_flag=True,
              help="Force plaintext storage (skip the OS keyring).")
# Deliberate inline option (not _destructive_option): the profile setup verbs
# keep a `-y` short alias the shared helper omits by design (#294).
@click.option("--yes", "-y", is_flag=True, help="Skip the overwrite-confirm prompt.")
@pass_ctx
def profile_add(ctx: CLIContext, url, name_opt, auth_opt, username, domain,
                tenant_id, client_id, password_opt, client_secret_opt, api_version,
                no_verify_ssl, publisher_prefix,
                store_password_plaintext, yes):
    """Create a profile, save its secret, test the connection, and activate it.

    Run with no flags for an interactive wizard; pass flags for scripting/CI.
    """
    password_opt = _resolve_secret_flag(password_opt, client_secret_opt)
    interactive = _stdin_is_tty() and not ctx.json_mode
    if not url:
        if not interactive:
            raise click.UsageError("--url is required (no TTY for the wizard).")
        url = click.prompt("Server URL (e.g. https://crm.corp/org or https://org.crm.dynamics.com)")
    auth_scheme = auth_opt or infer_auth_scheme(url)
    if interactive and auth_opt is None:
        schemes = ["ntlm", "kerberos", "negotiate", "oauth"]
        chosen = select_one("Auth scheme", [(s, s) for s in schemes],
                            default=auth_scheme)
        if chosen is None:
            ctx.emit(False, error="aborted by user")
            return
        auth_scheme = chosen

    if auth_scheme == "oauth":
        if not tenant_id:
            if not interactive:
                raise click.UsageError("--tenant-id is required for an OAuth profile.")
            tenant_id = click.prompt("Azure AD tenant id")
        if not client_id:
            if not interactive:
                raise click.UsageError("--client-id is required for an OAuth profile.")
            client_id = click.prompt("Application (client) id")
        domain = ""
        username = ""
    else:
        if not username:
            if not interactive:
                raise click.UsageError("--username is required for an on-prem profile.")
            username = click.prompt("Username")
        if domain is None:
            domain = click.prompt("AD domain (blank for UPN)", default="", show_default=False) \
                if interactive else ""

    name = name_opt or (
        click.prompt("Profile name", default=default_profile_name(url))
        if interactive else default_profile_name(url))
    # Optional publisher prefix. The --publisher-prefix flag path is validated by
    # _validate_prefix_opt at parse time; here the wizard prompts (blank = skip)
    # and re-prompts on an invalid entry rather than aborting.
    if publisher_prefix is None and interactive:
        from crm.core.solution import validate_customization_prefix
        while True:
            entered = click.prompt("Publisher prefix (blank to skip)",
                                   default="", show_default=False).strip()
            if not entered:
                break
            try:
                validate_customization_prefix(entered)
            except D365Error as exc:
                click.echo(str(exc), err=True)
                continue
            publisher_prefix = entered
            break
    secret = password_opt
    if not secret and interactive:
        label = "Client secret" if auth_scheme == "oauth" else "Password"
        secret = click.prompt(label, hide_input=True, default="", show_default=False) or None
    if not secret:
        raise click.UsageError(
            "--password (or --client-secret) is required (no TTY to prompt for it).")

    if name in session_mod.list_profiles() and not yes:
        _confirm_destructive(ctx, "profile", name, yes,
                             message=f"Profile {name!r} exists. Overwrite?")

    negotiate = api_version is None
    try:
        profile = ConnectionProfile(
            name=name, url=url, domain=domain or "", username=username or "",
            api_version=api_version or conn_mod.DEFAULT_API_VERSION,
            verify_ssl=not no_verify_ssl, auth_scheme=auth_scheme,
            tenant_id=tenant_id, client_id=client_id,
            publisher_prefix=publisher_prefix,
        )
        session_mod.save_profile(profile)
    except D365Error as exc:
        # Invalid name / auth scheme — emit the clean envelope, not a traceback.
        _handle_d365_error(ctx, exc)
        return
    except OSError as exc:
        ctx.emit(False, error=f"Could not write profile {name!r}: {exc}")
        return

    with d365_errors(ctx):
        where = conn_mod.save_secret(name, secret, force_plaintext=store_password_plaintext)
    warnings = []
    if where == "plaintext" and not store_password_plaintext:
        warnings.append("OS keyring unavailable — " + _plaintext_secret_warning())
    elif where == "plaintext":
        warnings.append(_plaintext_secret_warning())

    ctx.profile_name = name
    ctx.password = secret
    ctx.invalidate_backend()
    try:
        info = conn_mod.test_connection(ctx.backend(), negotiate=negotiate)
    except D365Error as exc:
        # The secret is already saved; don't leave it lingering in-memory as a
        # sticky REPL root option after a failed connection.
        ctx.password = None
        ctx.invalidate_backend()
        _handle_d365_error(ctx, exc, hint="profile saved; fix creds then re-run `crm profile add`")
        return
    if info["api_version"] != profile.api_version:
        profile.api_version = info["api_version"]
        session_mod.save_profile(profile)

    state = session_mod.load_session(ctx.session_name)
    state["active_profile"] = name
    session_mod.save_session(state, ctx.session_name)
    # Clear the in-memory secret now that it's persisted: later commands resolve
    # from the saved store, and in the REPL (sticky root options) a lingering
    # ctx.password would otherwise override the stored secret on every command.
    ctx.password = None
    ctx.invalidate_backend()
    data = {
        "profile": name, "auth_scheme": auth_scheme,
        "credential_storage": where, "active": True,
        "user_id": info.get("user_id"), "api_version": info["api_version"],
    }
    ctx.emit(True, data=data, meta={"profile": name}, warnings=warnings or None)


@profile_group.command("use")
@click.argument("name", required=False)
@click.option("--none", "clear", is_flag=True, help="Clear the active profile.")
@pass_ctx
def profile_use(ctx: CLIContext, name, clear):
    """Switch the active profile. No argument shows an interactive picker."""
    state = session_mod.load_session(ctx.session_name)
    if clear:
        state["active_profile"] = None
        session_mod.save_session(state, ctx.session_name)
        ctx.profile_name = None
        ctx.password = None
        ctx.invalidate_backend()
        ctx.emit(True, data={"active_profile": None})
        return

    names = session_mod.list_profiles()
    if not name:
        if not names:
            ctx.emit(False, error="No profiles. Run `crm profile add`.")
            return
        try:
            active = state.get("active_profile")
            items = [(n, _use_label(n, active)) for n in names]
            name = select_one("Select profile to activate", items)
        except RuntimeError:
            ctx.emit(False, error="profile name required (no TTY for the picker); "
                     "see `crm profile list`.")
            return
        if not name:
            ctx.emit(False, error="no profile selected")
            return

    if name not in names:
        _handle_d365_error(ctx, D365Error(f"Profile {name!r} not found."))
        return
    state["active_profile"] = name
    session_mod.save_session(state, ctx.session_name)
    ctx.profile_name = name
    ctx.password = None
    ctx.invalidate_backend()
    ctx.emit(True, data={"active_profile": name})


def _use_label(name: str, active: str | None) -> str:
    try:
        p = session_mod.load_profile(name)
        target = "cloud" if p.auth_scheme == "oauth" else "on-prem"
        flag = "  (active)" if name == active else ""
        return f"{name}  {target}  {p.url}{flag}"
    except _PROFILE_LOAD_ERRORS:
        # A corrupt/malformed profile still gets a pickable bare-name label.
        return name


def _credential_storage(name: str) -> str:
    """Report where a profile's secret lives ('plaintext'|'keyring'|'none').

    Resilient to an unreadable/corrupt profile file — returns 'unknown' rather
    than letting a single bad file crash `crm profile list`.
    """
    try:
        if session_mod.load_profile_secret(name) is not None:
            return "plaintext"
        if keyring_store.has_secret(name):
            return "keyring"
        return "none"
    except _PROFILE_LOAD_ERRORS:
        return "unknown"


@profile_group.command("list")
@pass_ctx
def profile_list(ctx: CLIContext):
    """List saved profiles; the active one is marked."""
    names = session_mod.list_profiles()
    active = session_mod.load_session(ctx.session_name).get("active_profile")
    rows = []
    for n in names:
        try:
            p = session_mod.load_profile(n)
            rows.append({
                "name": n, "active": n == active,
                "target": "cloud" if p.auth_scheme == "oauth" else "on-prem",
                "url": p.url, "credential_storage": _credential_storage(n),
                "publisher_prefix": p.publisher_prefix,
            })
        except _PROFILE_LOAD_ERRORS:
            rows.append({"name": n, "active": n == active,
                        "credential_storage": _credential_storage(n)})
    if ctx.json_mode:
        ctx.emit(True, data=rows)
        return
    ctx.skin.section("Profiles")
    if not rows:
        ctx.skin.hint("(none) — run `crm profile add`")
    for r in rows:
        mark = "● " if r.get("active") else "○ "
        ctx.skin.status(mark + r["name"],
                        f"{r.get('target','?')}  {r.get('url','?')}  "
                        f"cred={r['credential_storage']}")


@profile_group.command("edit")
@click.argument("name")
@click.option("--url", default=None)
@click.option("--username", default=None)
@click.option("--domain", default=None)
@click.option("--tenant-id", default=None)
@click.option("--client-id", default=None)
@click.option("--api-version", default=None)
@click.option("--publisher-prefix", default=None, callback=_validate_prefix_opt)
@pass_ctx
def profile_edit(ctx: CLIContext, name, url, username, domain, tenant_id,
                 client_id, api_version, publisher_prefix):
    """Change a profile's fields (not its secret — use set-password)."""
    try:
        p = session_mod.load_profile(name)
    except FileNotFoundError:
        _handle_d365_error(ctx, D365Error(f"Profile {name!r} not found."))
        return
    except _PROFILE_LOAD_ERRORS as exc:
        ctx.emit(False, error=f"Profile {name!r} is unreadable: {exc}")
        return
    if url is not None: p.url = url.rstrip("/")
    if username is not None: p.username = username
    if domain is not None: p.domain = domain
    if tenant_id is not None: p.tenant_id = tenant_id
    if client_id is not None: p.client_id = client_id
    if api_version is not None: p.api_version = api_version
    if publisher_prefix is not None: p.publisher_prefix = publisher_prefix
    # Fail fast on an edit that leaves the profile unusable, rather than letting
    # it surface later as a confusing backend-build error.
    if not p.url:
        raise click.UsageError("--url cannot be empty.")
    if p.auth_scheme == "oauth":
        if not p.tenant_id or not p.client_id:
            raise click.UsageError(
                "an OAuth profile needs tenant_id and client_id.")
    elif not p.username:
        raise click.UsageError("an on-prem profile needs a username.")
    session_mod.save_profile(p)
    ctx.invalidate_backend()
    ctx.emit(True, data={"profile": name, "updated": True})


@profile_group.command("rm")
@click.argument("name")
# Deliberate inline option (not _destructive_option): keeps a `-y` short alias the
# shared helper omits by design (#294).
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation.")
@pass_ctx
def profile_rm(ctx: CLIContext, name, yes):
    """Delete a profile and its stored secret."""
    if name not in session_mod.list_profiles():
        _handle_d365_error(ctx, D365Error(f"Profile {name!r} not found."))
        return
    _confirm_destructive(ctx, "profile", name, yes)
    keyring_store.delete_secret(name)
    session_mod.clear_profile_secret(name)
    session_mod.delete_profile(name)
    state = session_mod.load_session(ctx.session_name)
    if state.get("active_profile") == name:
        state["active_profile"] = None
        session_mod.save_session(state, ctx.session_name)
        ctx.profile_name = None
        ctx.invalidate_backend()
    ctx.emit(True, data={"profile": name, "removed": True})


@profile_group.command("rename")
@click.argument("old")
@click.argument("new")
@pass_ctx
def profile_rename(ctx: CLIContext, old, new):
    """Rename profile OLD to NEW, cascading its secret, active pointer, and cache.

    All validation runs before any mutation; a keyring failure warns (with a
    `profile set-password NEW` recovery hint) rather than rolling back.
    """
    # Validate everything before touching disk.
    try:
        validate_profile_name(new)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if old not in session_mod.list_profiles():
        _handle_d365_error(ctx, D365Error(f"Profile {old!r} not found."))
        return
    if old == new:
        raise click.UsageError("OLD and NEW must differ.")
    if new in session_mod.list_profiles():
        _handle_d365_error(
            ctx, D365Error(f"Profile {new!r} already exists; refusing to clobber it."))
        return

    # Capture any keyring-stored secret before the file move (inline plaintext
    # `_secret` rides along inside the file itself).
    kr_secret = keyring_store.get_secret(old)
    session_mod.rename_profile(old, new)

    warnings = []
    if kr_secret is not None:
        try:
            keyring_store.set_secret(new, kr_secret)
            keyring_store.delete_secret(old)
        except D365Error as exc:
            warnings.append(
                f"Could not move the keyring secret to {new!r}: {exc} "
                f"Run `crm profile set-password {new}` to re-store it.")
    else:
        keyring_store.delete_secret(old)  # best-effort cleanup; no-op if absent

    # Repoint the active session only (concurrent sessions still on OLD break,
    # matching `profile rm`).
    state = session_mod.load_session(ctx.session_name)
    if state.get("active_profile") == old:
        state["active_profile"] = new
        session_mod.save_session(state, ctx.session_name)
        ctx.profile_name = new
        ctx.invalidate_backend()

    # Move the per-profile metadata cache dir (best-effort).
    try:
        from crm.core import metadata_cache
        metadata_cache.move_cache(old, new)
    except OSError:
        pass

    ctx.emit(True, data={"profile": new, "renamed": True, "from": old, "to": new},
             warnings=warnings or None)


@profile_group.command("set-password")
@click.option("--profile", "profile_name", required=True, help="Profile to store the secret for.")
@click.option("--password", "password_opt", default=None, help="Secret to store (else prompted on a TTY).")
@click.option("--client-secret", "client_secret_opt", default=None,
              help="OAuth client secret — alias for --password (mutually exclusive).")
@click.option("--store-password-plaintext", is_flag=True, help="Force plaintext storage.")
@pass_ctx
def profile_set_password(ctx: CLIContext, profile_name, password_opt,
                         client_secret_opt, store_password_plaintext):
    """Store/replace the secret for an existing profile."""
    password_opt = _resolve_secret_flag(password_opt, client_secret_opt)
    try:
        profile = session_mod.load_profile(profile_name)
    except FileNotFoundError:
        _handle_d365_error(ctx, D365Error(f"Profile {profile_name!r} not found."))
        return
    secret = password_opt
    if not secret and _stdin_is_tty() and not ctx.json_mode:
        import getpass
        label = "client secret" if profile.auth_scheme == "oauth" else "password"
        secret = getpass.getpass(f"D365 {label} for profile {profile_name!r}: ") or None
    if not secret:
        ctx.emit(False, error="No secret supplied. Pass --password (or --client-secret).")
        return
    with d365_errors(ctx):
        where = conn_mod.save_secret(profile_name, secret, force_plaintext=store_password_plaintext)
    warnings = [_plaintext_secret_warning()] if where == "plaintext" else None
    ctx.emit(True, data={"profile": profile_name, "stored": True, "to": where},
             warnings=warnings)


@profile_group.command("delete-password")
@click.option("--profile", "profile_name", required=True, help="Profile whose secret to remove.")
@pass_ctx
def profile_delete_password(ctx: CLIContext, profile_name):
    """Remove a stored secret (OS keyring AND plaintext)."""
    removed_keyring = keyring_store.delete_secret(profile_name)
    removed_plaintext = session_mod.clear_profile_secret(profile_name)
    removed = removed_keyring or removed_plaintext
    where = []
    if removed_keyring: where.append("keyring")
    if removed_plaintext: where.append("plaintext")
    ctx.emit(True, data={"profile": profile_name, "removed": removed, "from": where},
             meta=({"note": "no stored secret found"} if not removed else None))
