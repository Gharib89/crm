"""Connection management commands."""
# pyright: basic
from __future__ import annotations
import click
from crm.core import connection as conn_mod
from crm.core import keyring_store
from crm.core import session as session_mod
from crm.utils.d365_backend import ConnectionProfile, D365Error
from crm.cli import CLIContext, FAILURE_EXIT_CODE, pass_ctx, _stdin_is_tty
from crm.commands._helpers import _handle_d365_error, _plaintext_secret_warning


@click.group("connection")
def connection_group():
    """Manage server connection profiles and authentication."""


@connection_group.command("connect")
@click.option("--url", required=True, help="Server URL, e.g. https://crm.contoso.local/contoso")
@click.option("--username", required=True)
@click.option("--domain", default="", help="AD domain (optional for on-prem with UPN).")
@click.option("--password", "password_opt", help="Password (else read from D365_PASSWORD).")
@click.option("--profile-name", default="default", help="Save under this profile name.")
@click.option("--api-version", default=None,
              help="Web API version. Omit to auto-negotiate (tries v9.2, "
                   "downgrades to v9.1 when the server is on-prem and 501s).")
@click.option("--no-verify-ssl", is_flag=True, help="Skip SSL certificate verification.")
@click.option("--default-solution", default=None,
              help="Default solution uniquename for mutating metadata commands.")
@click.option("--publisher-prefix", default=None,
              help="Default schema-name prefix for create commands, e.g. 'new'.")
@click.option("--store-password", is_flag=True,
              help="Store the secret in the OS keyring (service 'crm', account = "
                   "profile name) so later commands need no password.")
@click.option("--store-password-plaintext", is_flag=True,
              help="Headless/CI fallback: write the secret into the profile file "
                   "(0600 on POSIX; perms unenforced on Windows). Emits a warning.")
@pass_ctx
def connection_connect(ctx: CLIContext, url, username, domain, password_opt,
                       profile_name, api_version, no_verify_ssl,
                       default_solution, publisher_prefix,
                       store_password, store_password_plaintext):
    """Save a connection profile and test the credentials with WhoAmI."""
    if store_password and store_password_plaintext:
        raise click.UsageError(
            "--store-password and --store-password-plaintext are mutually exclusive."
        )
    # An omitted --api-version is negotiated against the server (v9.2 → v9.1 on
    # on-prem); an explicit value is pinned and never auto-downgraded.
    negotiate = api_version is None
    profile = ConnectionProfile(
        name=profile_name,
        url=url,
        domain=domain,
        username=username,
        api_version=api_version or conn_mod.DEFAULT_API_VERSION,
        verify_ssl=not no_verify_ssl,
        auth_scheme=ctx.auth_scheme or "ntlm",
        default_solution=default_solution,
        publisher_prefix=publisher_prefix,
    )
    session_mod.save_profile(profile)
    ctx.profile_name = profile_name
    # Resolve the secret once (flag → env → keyring/plaintext → TTY prompt) so we
    # can both connect with it and store it without prompting twice.
    allow_prompt = _stdin_is_tty() and not ctx.json_mode
    try:
        resolved = conn_mod.resolve_credentials(
            profile_name=profile_name,
            password_override=password_opt,
            allow_prompt=allow_prompt,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.password = resolved.password
    ctx.invalidate_backend()
    try:
        info = conn_mod.test_connection(ctx.backend(), negotiate=negotiate)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return

    # Persist the negotiated version so every later command uses the working one.
    # (The trailing invalidate_backend() below picks up the re-saved profile.)
    if info["api_version"] != profile.api_version:
        profile.api_version = info["api_version"]
        session_mod.save_profile(profile)

    # Store (or clear) the secret BEFORE committing active_profile. A keyring
    # write can fail (no backend, locked); if it does the command exits as a
    # failure, and it must not also leave the session pointing at this profile
    # — that would silently change later commands' default profile selection.
    if store_password:
        try:
            keyring_store.set_secret(profile_name, resolved.password)
        except D365Error as exc:
            _handle_d365_error(ctx, exc)
            return
        # Keyring is now this profile's single store — drop any stale plaintext
        # secret so it can't shadow the keyring value (plaintext wins resolution).
        session_mod.clear_profile_secret(profile_name)
    elif store_password_plaintext:
        session_mod.save_profile_secret_plaintext(profile_name, resolved.password)
        # Plaintext is now this profile's single store — drop any stale keyring entry.
        keyring_store.delete_secret(profile_name)
        ctx.skin.warning(_plaintext_secret_warning())
    else:
        # No store flag on this connect: drop any stale plaintext _secret so it is
        # not silently retained (save_profile now PRESERVES _secret across
        # re-saves, so it would otherwise persist). Keyring entries are left
        # intact — that is the configure-once path (#130).
        session_mod.clear_profile_secret(profile_name)

    state = session_mod.load_session(ctx.session_name)
    state["active_profile"] = profile_name
    session_mod.save_session(state, ctx.session_name)
    ctx.invalidate_backend()
    ctx.emit(True, data=info, meta={"profile": profile_name})


@connection_group.command("status")
@pass_ctx
def connection_status(ctx: CLIContext):
    """Show the active session + profile (no network call)."""
    state = session_mod.load_session(ctx.session_name)
    profile_name = ctx.profile_name or state.get("active_profile")
    data = {
        "session": ctx.session_name,
        "active_profile": profile_name,
        "current_entity_set": state.get("current_entity_set"),
    }
    if profile_name:
        try:
            p = session_mod.load_profile(profile_name)
            data["profile"] = p.to_dict()
        except FileNotFoundError:
            data["profile_error"] = f"profile {profile_name!r} not found"
    ctx.emit(True, data=data)


@connection_group.command("whoami")
@pass_ctx
def connection_whoami(ctx: CLIContext):
    """Issue WhoAmI() against the server."""
    try:
        info = conn_mod.whoami(ctx.backend())
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


@connection_group.command("test")
@pass_ctx
def connection_test(ctx: CLIContext):
    """Reachability check: WhoAmI + report API base."""
    # An env-derived profile with no explicit D365_API_VERSION negotiates the
    # version for this run; a loaded profile is respected as saved. Load .env
    # first so a version pinned only in the .env file is seen (it would
    # otherwise be read later, inside ctx.backend(), and missed here).
    conn_mod.load_dotenv()
    negotiate = ctx.profile_name is None and not conn_mod.env_api_version()
    try:
        info = conn_mod.test_connection(ctx.backend(), negotiate=negotiate)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


def _credential_storage(name: str) -> str:
    """Where this profile's secret is stored: plaintext > keyring > none.

    Plaintext is checked first (a cheap file read, no keyring call) and reported
    even if a keyring entry also exists — the on-disk secret is the one to flag.
    """
    if session_mod.load_profile_secret(name) is not None:
        return "plaintext"
    if keyring_store.has_secret(name):
        return "keyring"
    return "none"


@connection_group.command("profiles")
@pass_ctx
def connection_profiles(ctx: CLIContext):
    """List saved profiles."""
    names = session_mod.list_profiles()
    if ctx.json_mode:
        # `data` stays the bare name list for back-compat with `--json` consumers
        # that iterate it; the per-profile default_solution / publisher_prefix (AC)
        # is surfaced under `meta` instead of changing the shape of `data`.
        profiles = []
        for n in names:
            storage = _credential_storage(n)
            try:
                p = session_mod.load_profile(n)
                profiles.append({
                    "name": n,
                    "default_solution": p.default_solution,
                    "publisher_prefix": p.publisher_prefix,
                    "credential_storage": storage,
                })
            except FileNotFoundError:
                profiles.append({"name": n, "credential_storage": storage})
        ctx.emit(True, data=names, meta={"profiles": profiles})
        return
    ctx.skin.section("Profiles")
    if not names:
        ctx.skin.hint("(none)")
    for n in names:
        storage = _credential_storage(n)
        try:
            p = session_mod.load_profile(n)
            ctx.skin.status(
                n,
                f"solution={p.default_solution} prefix={p.publisher_prefix} "
                f"cred={storage}",
            )
        except FileNotFoundError:
            ctx.skin.status(n, f"cred={storage}")


@connection_group.command("disconnect")
@pass_ctx
def connection_disconnect(ctx: CLIContext):
    """Clear the active profile from the session."""
    state = session_mod.load_session(ctx.session_name)
    state["active_profile"] = None
    session_mod.save_session(state, ctx.session_name)
    # Also clear in-memory state — sticky-options means these would otherwise
    # persist across REPL lines and defeat the disconnect.
    ctx.profile_name = None
    ctx.password = None
    ctx.invalidate_backend()
    ctx.emit(True, data={"disconnected": True})


@connection_group.command("delete-password")
@click.option("--profile", "profile_name", required=True,
              help="Profile whose stored secret should be removed.")
@pass_ctx
def connection_delete_password(ctx: CLIContext, profile_name):
    """Remove a stored secret for a profile (OS keyring AND plaintext)."""
    removed_keyring = keyring_store.delete_secret(profile_name)
    removed_plaintext = session_mod.clear_profile_secret(profile_name)
    removed = removed_keyring or removed_plaintext
    where = []
    if removed_keyring:
        where.append("keyring")
    if removed_plaintext:
        where.append("plaintext")
    ctx.emit(
        True,
        data={"profile": profile_name, "removed": removed, "from": where},
        meta=({"note": "no stored secret found"} if not removed else None),
    )


@connection_group.command("set-password")
@click.option("--profile", "profile_name", required=True,
              help="Profile to store the secret for (must already exist).")
@click.option("--password", "password_opt",
              help="Secret to store (else env D365_CLIENT_SECRET/D365_PASSWORD per the "
                   "profile's auth scheme, else a TTY prompt).")
@click.option("--store-password", is_flag=True,
              help="Store the secret in the OS keyring (default when neither flag is "
                   "given).")
@click.option("--store-password-plaintext", is_flag=True,
              help="Headless/CI fallback: write the secret into the profile file "
                   "(0600 on POSIX; perms unenforced on Windows). Emits a warning.")
@pass_ctx
def connection_set_password(ctx: CLIContext, profile_name, password_opt,
                            store_password, store_password_plaintext):
    """Store a secret (OAuth client secret or NTLM password) for an existing profile.

    Storage-side mirror of `connection delete-password`. Does not contact the server
    and does not rebuild the profile — it only writes the secret into the chosen store
    (OS keyring by default, or the profile file with --store-password-plaintext), keeping
    a profile's single-store invariant. The secret is read from --password, else the
    scheme's env var, else a TTY prompt; the existing on-disk store is never read.
    """
    if store_password and store_password_plaintext:
        raise click.UsageError(
            "--store-password and --store-password-plaintext are mutually exclusive."
        )
    try:
        profile = session_mod.load_profile(profile_name)
    except FileNotFoundError:
        _handle_d365_error(ctx, D365Error(f"Profile {profile_name!r} not found."))
        return
    allow_prompt = _stdin_is_tty() and not ctx.json_mode
    try:
        secret = conn_mod.resolve_secret_for_storage(
            profile, password_override=password_opt, allow_prompt=allow_prompt,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if store_password_plaintext:
        session_mod.save_profile_secret_plaintext(profile_name, secret)
        # Plaintext is now the single store — drop any stale keyring entry.
        keyring_store.delete_secret(profile_name)
        ctx.skin.warning(_plaintext_secret_warning())
        where = "plaintext"
    else:
        try:
            keyring_store.set_secret(profile_name, secret)
        except D365Error as exc:
            _handle_d365_error(ctx, exc)
            return
        session_mod.clear_profile_secret(profile_name)
        where = "keyring"
    ctx.emit(True, data={"profile": profile_name, "stored": True, "to": where})


@click.command("doctor")
@pass_ctx
def doctor_command(ctx: CLIContext):
    """Diagnose the connection: DNS/TCP, TLS, api_version, auth, rate-limit."""
    # Live diagnostic — issues raw GETs and never negotiates or mutates the
    # profile. Registered both under the `connection` group and as the
    # top-level `crm doctor` alias (the same command object).
    try:
        backend = ctx.backend()
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    result = conn_mod.connection_doctor(backend)
    if ctx.json_mode:
        ctx.emit(result["ok"], data={"checks": result["checks"]})
        return
    # Human mode: render the checklist explicitly — emit's human not-ok path
    # prints only the error line, dropping the per-check data we want to show.
    # The whole checklist (✓/✗ lines AND hints) goes to a SINGLE stream
    # (stdout, like skin.success/hint) so captured/piped output stays ordered;
    # skin.error writes to stderr and would orphan each failed line from its
    # hint. A ✗ line here is data, not an error message — render it on stdout
    # in the same visual format as skin.success (the exit code below is what
    # signals failure programmatically).
    ctx.skin.section("Connection doctor")
    for c in result["checks"]:
        line = f"{c['check']}: {c['detail']}"
        if c["ok"]:
            ctx.skin.success(line)
        else:
            ctx.skin.failure(line)
        if isinstance(c["hint"], str) and c["hint"]:
            ctx.skin.hint(f"    {c['hint']}")
    if not result["ok"]:
        raise click.exceptions.Exit(FAILURE_EXIT_CODE)


connection_group.add_command(doctor_command)
