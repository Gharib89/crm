# Setup — install and connect

Install the `crm` binary, then create a connection profile. The skill ships
assuming the binary is already on PATH — this is here for re-install and for
connecting to a new org / host.

## Install

The prebuilt `crm` binary bundles CPython and every dependency — no Python install
needed. One line per host:

**Windows (PowerShell):**

```powershell
irm https://pub-bbeb86c46454443ca76521dd4d29818e.r2.dev/install.ps1 | iex
```

**Linux:**

```bash
curl -fsSL https://pub-bbeb86c46454443ca76521dd4d29818e.r2.dev/install.sh | sh
```

Open a new shell so PATH updates, then verify with `crm --version`.

## Configure

The CLI authenticates with **Windows Integrated auth** for on-prem (`ntlm` by
default; `kerberos` / `negotiate` also supported; `adfs` for IFD), or **OAuth 2.0
client-credentials** for Dataverse online. Run **`crm profile add`** once to create
a connection profile — it infers the scheme from the URL (any `.dynamics.` host →
OAuth — `*.dynamics.com` plus regional clouds like `.dynamics.cn` / `.dynamics.de`;
anything else → `ntlm`), prompts for what that scheme needs, and verifies with
WhoAmI **before saving anything**. Only on success does it store the secret and
activate the profile.

If that live test fails, `add` doesn't save blindly: a clearly malformed profile
(bad URL, missing tenant/client id, no secret) is rejected outright, nothing
saved. A structurally plausible profile whose test still failed — likely a
transient outage (VPN down, app user not yet provisioned) — prompts `Save the
profile anyway?` on a terminal; declining leaves nothing saved. Non-interactively
(`--json`/CI), pass `--save-on-test-failure` to save it anyway, otherwise the
command errors without saving. Saving despite a failed test prints a one-time
warning that the connection was never verified — nothing is flagged on the stored
profile itself.

```bash
crm profile add          # interactive wizard (on a terminal)
```

Or drive it non-interactively for scripting/CI:

```bash
# On-prem (NTLM)
crm profile add --url https://crm.contoso.local/contoso \
  --username alice --domain CONTOSO --password '...' --name onprem

# Dataverse online (OAuth) — app registration instead of user/pass/domain
crm profile add --url https://contoso.crm.dynamics.com \
  --tenant-id <aad-tenant> --client-id <app-id> --client-secret '<secret>' --name cloud
```

**IFD orgs behind AD FS.** NTLM fails there (the Web API answers a bare 500), and
the URL never infers this scheme, so pick `adfs` by hand. A failed `ntlm` test
against such an org says so in its hint. The username goes to the STS exactly as
typed, with no separate `--domain`; which form it accepts (`DOMAIN\user`, UPN, or
bare name) depends on the AD FS server, so try another form on a rejection, and
single-quote `'DOMAIN\user'` in the shell. An org on an internal CA needs
`--no-verify-ssl`, which covers the STS too.

The OAuth scope (`https://<host>/.default`) and authority
(`https://login.microsoftonline.com/<tenant>`) are derived automatically; public
cloud only. The bearer token is cached at `~/.crm/msal_token_cache.json` (`0600`;
root follows `CRM_HOME`).
The app registration needs an **application user** with a security role in Dynamics.

**No `.env`, no credential env vars.** The CLI reads credentials and connection
config ONLY from a saved profile (or a per-run `--password`). There is no `.env`
autoload and no `D365_*` / `CRM_*` environment-variable reading. The one retained
env knob is `CRM_HOME` (state-directory override; unset or empty means `~/.crm/`).

Switch or inspect profiles with `crm profile use [name]` (no name → interactive
picker; `--none` clears the active profile) and `crm profile list` (marks the
active one); edit, rename, or delete one with `crm profile edit` /
`crm profile rename OLD NEW` / `crm profile rm`. `rename` is the safe way to
relabel a profile in place (its secret and cached metadata move with it)
without re-running `add` — but like `rm`, it only repoints the *active*
session pointer, so a concurrent session still holding the old name breaks. On
a fresh machine, any connection command with no profile drops into
`crm profile add` automatically on a terminal (under `--json`/no-TTY it errors
cleanly telling you to run `crm profile add`).

**Read-only profiles (guardrail).** A profile can be marked read-only to block
accidental writes: the backend refuses every org mutation as an operational
failure (`ok:false`, exit 1, error naming the profile) while reads run normally.
Gotcha for JSON callers: a read-only refusal is **not** a `--dry-run` preview —
`--dry-run` is checked first, so a read-only *and* dry-run mutation still previews
(`ok:true`, `data._dry_run:true`), but a real mutation on a read-only profile
returns `ok:false` exit 1. If writes are being refused, that's the guardrail
working as intended — clearing it requires an interactive terminal, so don't try
to disable it non-interactively; it's a guardrail against accidents, not a
security boundary (the real boundary is a server-side read-only role).

## Legacy CRM 2011 orgs

A CRM 2011 org has no Web API. `crm profile add` detects one itself (Web API 404 on
an NTLM profile, then a `5.x` build answered over SOAP) and saves a **`crm2011`
profile**; its JSON output carries `platform: "crm2011"`, where a normal profile says
`v9`. Verified only on CRM 2011 UR15 (5.0.9690.3557), Linux: on another build or OS,
tell the user it is untested.

- **Only `crm legacy` and `crm inventory extract` run on it** (prove
  connectivity with `crm --json legacy version`; `crm legacy --help` lists the
  rest). Every other command exits 1 with an error naming `crm legacy`, so a
  `crm2011` profile never needs a fallback attempt on `entity` or `query`.
- **Writes are impossible, not just discouraged.** The profile is always read-only
  and `profile edit --no-read-only` refuses even on a terminal (unlike the guardrail
  above), and the SOAP transport sends only allowlisted reads. A refused request
  (for example a message outside the allowlist) is a CLI limit, not an org
  permission problem: report it to the user as unsupported and stop.
- **`crm legacy` on any other profile refuses**: pass `--profile` naming the
  `crm2011` one.
- **Reads are continue-and-report.** `legacy metadata ENTITY...` and `legacy
  components` return the rows they read plus an `errors` list, one entry per
  entity or kind that faulted, and exit 1 when it is non-empty: read `errors`
  before trusting a partial `data`. An auth or transport failure is not per
  item: it stops the read with the standard error envelope and no `data`. An
  assembly with `binary: "unavailable"` has no binary in the org, so do not
  retry it. Pass `--out` for any read that includes web resources, processes or
  forms, so the JSON stays small.
- **A dependency read takes an id from an earlier read**: the `metadata_id` of a
  `legacy metadata` row for an entity, attribute, relationship or option set, the
  `id` of a `legacy components` row for a kind it reads. Each row names both
  sides by component type code and object id (plus parent ids), never by name,
  so map them back through those reads.
- **When the user expects an unpublished edit in a `legacy export-solution`
  zip**, tell them it is not there: the CLI cannot publish on a CRM 2011 org.
- Success envelopes use the standard JSON contract, with `meta.url` set to the SOAP
  endpoint instead of a Web API base URL. Faults map to the usual `meta.code` and
  `meta.category`. A connect timeout retries inside the CLI (bounded), so treat a
  `transport_error` as the org unreachable: check the VPN before running it again.

### Migration inventory (`crm inventory extract`)

A migration inventory is a read-only, descriptive record of the whole org: a
`manifest.json` (schema v1: records keyed by component key, per-kind coverage,
extraction gaps) plus raw artifacts (XML, XAML, web resource content, assembly
binaries). It describes what is deployed; it is never a spec for `crm apply`.

- **A v9 or cloud profile is refused before anything is read**: tell the user
  only `crm2011` is built so far.
- **It runs for minutes on a real org**: give it a long timeout or run it in
  the background.
- **Resume by re-running the identical command on the same `--out` folder.** It
  reuses finished reads (`data.resumed`, `data.reused`) and continues paging
  after the last finished page. A folder holding a checkpoint for another org
  URL, or files and no checkpoint (a finished inventory included), is refused:
  pass a new folder.
- **A failed read is continue-and-report.** The command exits 1 with `ok:false`
  but the summary still in `data` (`data.kinds`, `data.errors`,
  `data.complete: false`), and the manifest is written. Re-run on the same
  folder to retry only what failed. When a re-run returns the same
  `data.errors`, the failure is permanent (a page with no paging cookie,
  content that is not base64, a fault the server repeats): stop and report
  those errors and the kinds' coverage to the user. An auth or transport
  failure stops the run with no `data`; re-running resumes it.
- **Judge completeness by each kind's coverage in the manifest before trusting
  record counts**, and report the manifest's `gaps` to the user as manual
  follow-up: they name what the inventory could not capture, both what SOAP
  cannot read (assemblies on disk or in the GAC, ISV pages, external
  integrations, SSRS data sources) and membership or dependency rows naming a
  component of a kind v1 does not record or whose read failed.
