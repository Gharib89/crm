# Dedicated CS-provisioned Dataverse sandbox as the cloud e2e target

To convert the org-stateful verbs that were `E2E_SKIP`'d for lack of a safe live
target (issue #498), the cloud e2e target moves from the general `agent-cloud`
org to a **long-lived, pollution-tolerant Dataverse sandbox with Customer Service
provisioned**. This sandbox *replaces* `agent-cloud` as the single cloud target —
it is not a third target — so the existing capability buckets
(`any`/`requires_cloud`/`requires_onprem`/divergent) keep working unchanged and no
new pytest marker is introduced.

## Why

Several D365-touching verbs had no live e2e test because exercising them mutates
shared org state with no clean teardown, or needs a capability the general test
orgs lack:

- `sla create`/`add-kpi` need a **Customer-Service-provisioned** org (the general
  cloud org has none) and flip `IsSLAEnabled` metadata with no inverse verb.
- `theme publish`, `fieldsec add-permission`, `solution stage-and-upgrade`/
  `apply-upgrade` are reversible but org-stateful — risky to run repeatably on a
  shared org other sessions rely on.
- `audit detail` needs auditing enabled and an audit row to decode; the general
  cloud org has org-level auditing disabled.
- `workflow run` needs a pre-existing on-demand workflow to dispatch (Web API
  cannot create a workflow definition — a platform block, see below).

A target that is **ours to pollute** turns all of these from "unsafe on a shared
org" into ordinary lifecycle tests. A Dataverse sandbox is the cheap way to get
one: Customer Service is a click-to-install first-party app, and the env carries
no VPN requirement. Provisioning a *new on-prem* org for the same purpose was
rejected as infrastructure-heavy (a CRM server: IIS + SQL + deployment); the
existing `agent-on-prem` org stays as-is for `requires_onprem` coverage.

## How it works

The sandbox is provisioned once and carries a fixed setup baseline (the
"dedicated-org provisioning checklist"), seeded by a maintainer:

1. Customer Service installed (unblocks `sla create`/`add-kpi`).
2. Org-level auditing on, with auditing enabled on one entity (unblocks
   `audit detail` — the test generates and decodes a row inline).
3. One **no-op on-demand workflow** authored via the web app (unblocks
   `workflow run` dispatch-only; the test skips if absent).
4. The `agent-cloud` profile and the CI cloud credential set are pointed at the
   sandbox; `D365_E2E_ALLOW_HOST` names its `*.dynamics.com` host to satisfy the
   production-host guard.

Tests follow the suite's existing self-cleaning lifecycle discipline
(create→assert→delete/restore). The dedicated org is the safety net for leaked
state, **not** a licence to leak — CI reruns must stay repeatable.

The plugin lifecycle (`register-assembly`→`unregister-assembly`/`-step`) needs a
signed `.dll`, built from committed C# source by a pytest session fixture that
shells `dotnet build`; if `dotnet` is absent the fixture **skips with
instructions** rather than erroring (matching the suite's "skip-with-instructions"
convention for missing prerequisites). This adds the .NET SDK to the e2e CI job
but does **not** unblock `solution extract`/`pack`: those wrap the legacy
Windows-only `SolutionPackager.exe`, a separate toolchain with no supported Linux
runtime — migration to the cross-platform `pac solution` is tracked in #500.

## What stays skipped

- `solution extract`/`pack` — wrap `SolutionPackager.exe`; tracked by #500.
- `workflow clone`/`delete`/`import` — the Web API rejects workflow-definition
  upsert with *"…created outside the … Web application"*. This is a **platform
  block, not org-specific policy**, so the dedicated org does not unblock it; the
  `E2E_SKIP` reason is corrected to say so.

## Considered options

- **Run org-stateful tests on the existing shared `agent-cloud` org.** Rejected:
  no Customer Service (SLA impossible regardless of teardown), and pollution risk
  for concurrent sessions that depend on that org's state.
- **A separate third target behind a new `requires_dedicated` marker.** Rejected:
  adds a marker, a third CI credential set, and target-selection logic to the
  harness for no isolation benefit over simply making the sandbox the cloud target.
- **Per-CI-run ephemeral org** (provision + install CS + tear down each run via
  PAC CLI / admin API). Rejected: minutes of provisioning latency per run and
  brittle automation; a long-lived dirty-allowed sandbox is far cheaper and
  repeatable tests keep it clean enough.

## Consequences

- Provisioning, CS install, the seed baseline, and the CI credential swap are a
  one-time **maintainer** task that gates the test work — this is a
  `ready-for-human` prerequisite, not agent-automatable.
- `agent-cloud` no longer points at the prior general cloud org; CLAUDE.md's
  "Project live targets" section and the `onprem_cloud_profiles` memory must be
  updated to describe the sandbox.
- The e2e CI job gains a .NET SDK + `dotnet build` step (plugin fixture only).
- `TEST.md` documents the provisioning checklist and the new `requires_cloud`
  conversions; `coverage.py` shrinks to 5 `E2E_SKIP` entries (extract/pack +
  workflow clone/delete/import), each with a corrected reason.

## Addendum (2026-06-22): trial reality — ephemeral target, no CI swap

The "long-lived sandbox" this ADR assumed turned out **not to be licensable** on the
available tenant: it carries only trial licenses (Trial-type environments only) and was
at storage capacity, and a generic Trial environment **cannot install Customer Service**
(a Dynamics 365 license is required, which wasn't available). Standing up the intended
durable CS sandbox needs a paid or partner Dynamics 365 license — a procurement decision
that wasn't taken now.

Interim resolution, which **revises three points of the plan above**:

- **A self-service Customer Service trial, not a long-lived sandbox.** A free
  Customer-Service trial ships CS preinstalled and brings its own capacity (clearing both
  walls at $0), but it is **ephemeral** — 30 days, extendable once to ~60, and auto-expires
  after 14 days idle. So it is a *transient* CS target, not the permanent one this ADR
  envisioned.
- **Duplicate, not replace.** Because the target is ephemeral, `agent-cloud` is **not**
  re-pointed at it (that would break the durable cloud target when the trial dies).
  Instead a **separate `agent-cs-trial`** profile duplicates `agent-cloud` (same tenant →
  same Entra app registration; only the URL differs). `agent-cloud` remains *the* cloud
  e2e target. This is still **not a third pytest target** — `agent-cs-trial` is OAuth, so
  it resolves to the existing `cloud` bucket with no new marker; it is simply the profile a
  maintainer points at locally when exercising the CS-dependent verbs.
- **CI stays on `agent-cloud`; the CI cloud secret is *not* swapped.** An org that expires
  in ≤60 days cannot be a stable CI target. The CS-dependent conversions therefore
  **skip-with-instructions** in CI (preconditions absent on `agent-cloud`) and are proven
  **locally and opportunistically** against `agent-cs-trial` while the trial lives. The
  guarantee weakens from "proven in CI" to "proven locally on demand" for those verbs —
  the honest cost of not having a licensed durable org. The `.NET SDK` CI step still lands
  (the plugin lifecycle needs no CS, so it can run against `agent-cloud`).

Provisioning was completed and verified against the trial org: org auditing on with Account
audited; a background, on-demand, activated **no-op on-demand workflow** seeded (dispatch
confirmed — `ExecuteWorkflow` created an async operation); an existing cloud entity-CRUD
e2e passing **3/3** green via the harness. The "what stays skipped" set is unchanged. One
defect surfaced during the live dispatch — `crm workflow run` reports a null async-operation
id because it reads `Id` while the platform returns the `asyncoperation` entity keyed
`asyncoperationid` — filed as **#514** (out of scope here; the `workflow run` conversion in
#502 depends on its fix).

When a paid/partner Dynamics 365 license becomes available, the durable-sandbox plan in the
body of this ADR can be revisited (re-point `agent-cloud` + the CI secret, drop the
ephemeral-trial caveats).
