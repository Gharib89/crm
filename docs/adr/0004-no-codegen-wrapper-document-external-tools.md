# No `crm codegen` wrapper — document the external tools instead

Early-bound class generation stays external (`CrmSvcUtil.exe` for on-prem,
`pac modelbuilder build` for Dataverse online); the skill documents the toolchain
rather than shipping a `crm codegen` verb. Wrapping `CrmSvcUtil.exe` would create a
verb that is Windows / .NET-Framework-only (this CLI also ships Linux binaries) and
would force a credential handoff that either leaks the secret onto a subprocess
command line or degrades to interactive login — adding nothing over a documented
command. The SolutionPackager bridge this once paralleled has since migrated to
the cross-platform `pac solution` verbs (#500); `CrmSvcUtil.exe`, by contrast,
has no cross-platform equivalent for on-prem codegen, so a `crm codegen` verb
would still be Windows-only without payoff. See issue #194.

## Reconsidered 2026-06-24 — decision unchanged

Reopened to weigh a *native* renderer (pure Python over the metadata the CLI
already reads cross-platform, no subprocess), which escapes this ADR's original
objections (Windows-only, credential handoff). It splits in two:

- **C# early-bound** — reimplementing `CrmSvcUtil` / `pac modelbuilder` output
  (`Entity`-derived classes, `OrganizationServiceContext`, LINQ). A large surface
  that would permanently lag the first-party tools. Stays out.
- **TypeScript / Python Web-API typings** — no first-party tool emits these, but a
  mature community ecosystem already owns the niche: `XrmDefinitelyTyped` (the
  on-prem form-script favourite) and `dataverse-gen` / `dataverse-ify` (Web-API
  early-bound). A fourth entrant carries the same "will lag" risk, and the CLI
  already emits the schema as JSON (`metadata describe` / `export-spec`) for any
  generator or agent to consume.

Decision unchanged: **no `crm codegen` verb.** The skill and docs should
additionally point TypeScript users at `XrmDefinitelyTyped` and `dataverse-gen`
alongside the existing `CrmSvcUtil` / `pac modelbuilder` guidance.
