# How-to: delegated sign-in (`oauth-user`)

Sign in to a Dataverse online org **as yourself** instead of as an application
user. The org applies your own security roles, and no client secret is stored
anywhere. Use it when your tenant has no application user for you, or when the
work should run under your identity. For unattended or CI use, keep the
`oauth` (app sign-in) scheme; see [How-to: profile](profile.md).

Only an explicit `crm profile login` is interactive. Every other command reuses
the cached sign-in silently, or fails with a stable `reauth_required` error that
tells you to log in again.

## 1. Register an Entra app (once per tenant)

Each tenant registers its **own** public-client app. There is no shared app.

1. In the Entra admin center, create an **App registration**.
2. Under **Authentication**, add the **Mobile and desktop applications**
   platform with the redirect URI `http://localhost`.
3. Still under **Authentication**, turn **Allow public client flows** on.
4. Under **API permissions**, add the **Dynamics CRM** delegated permission
   `user_impersonation`.
5. Have an administrator **grant admin consent** for that permission, once for
   the tenant.
6. Note the **Directory (tenant) ID** (the GUID, not a domain name) and the
   **Application (client) ID**.

## 2. Create the profile

```bash
crm profile add \
    --url https://contoso.crm.dynamics.com \
    --auth-scheme oauth-user \
    --tenant-id <tenant-guid> --client-id <app-client-id> \
    --name me
```

This saves and activates the profile with no secret and no live test (there is
no credential until you log in). On a terminal, the wizard for a cloud URL asks
whether to use app sign-in or delegated sign-in. Scripted adds are unchanged: a
cloud URL without `--auth-scheme` still infers `oauth`. `--password` /
`--client-secret` are a usage error with this scheme.

## 3. Log in

```bash
crm profile login          # the active profile
crm profile login me
```

A browser opens for the sign-in (authorization code with PKCE, on a loopback
redirect). On success the profile records your non-secret `username` and
`home_account_id`, and the tokens go into an encrypted cache.

crm prints a line to stderr when the browser opens, then waits up to 300 seconds
for the sign-in to finish. If it does not, `login` fails with `login_failed`
instead of hanging (see Troubleshooting).

- **Terminal only.** `login` is refused under `--json` or with no TTY (error
  code `login_requires_tty`).
- **No browser available.** It fails at once and names `--device-code`.
- **Wrong tenant.** A token from a tenant other than the profile's `tenant_id`
  is refused and nothing is saved (`wrong_tenant`).
- **Different account.** Logging in as another account rebinds the profile and
  says so in a warning.

### Device code (only on request)

```bash
crm profile login me --device-code
```

Prints a code and URL to complete on another device. Microsoft discourages
device code (it is a common phishing vector) and many tenants block it with
Conditional Access, so use it only when no browser is available. A session
started this way stays protocol-tracked, so a later silent refresh can still
fail under Conditional Access; log in again with the browser flow then.

## Day-to-day behavior

- Other commands take a token from the cache silently. They never open a
  browser and never print a token.
- The cache is an AES-256-GCM encrypted file in the state home (`CRM_HOME`,
  default `~/.crm/`), with the key in the OS keyring. Where no keyring exists
  (typical WSL or headless), the key falls back to a `0600` plaintext file and
  `login` warns.
- With no signed-in account, or an expired or revoked sign-in, a command fails
  with `reauth_required` (HTTP status 401, category `auth_failed`) and the hint
  `Run crm profile login <name>`.
- `profile set-password` and `profile delete-password` error on this scheme:
  there is no secret. Use `profile login`.
- `profile rm` also deletes the cached tokens.
- `profile rename` drops the cache (it is keyed by profile name): run
  `profile login` again under the new name.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `reauth_required` on any command | No usable cached sign-in. Run `crm profile login <name>` from a terminal. |
| `login_requires_tty` | `login` ran under `--json` or without a TTY. Run it from an interactive shell. |
| `wrong_tenant` | The signed-in account belongs to another tenant than the profile's `tenant_id`. Check that `tenant_id` is the tenant GUID (`crm profile edit`), or use an account of that tenant. |
| `AADSTS65001` or `AADSTS90094` | The app has no consent for Dataverse. An administrator grants admin consent to `user_impersonation` (step 1.5). |
| `login` waits, then fails with `login_failed` ("not finished within 300 s") | The browser sign-in never returned to crm: it was not finished, or Entra stopped on an "Approval required" page (admin consent missing for `user_impersonation`, step 1.5). Finish the sign-in, or have an administrator grant consent, then log in again. |
| Device code blocked (Conditional Access) | The tenant forbids device code. Run `crm profile login` without `--device-code` on a machine with a browser. |
| "No web browser is available" | Headless host. Use `--device-code`, or log in on a machine with a browser. |
| Works, then fails after a rename | Run `crm profile login <new-name>`. |
