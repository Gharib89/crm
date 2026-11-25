# Automation — plug-in package deploy and early-bound classes

Build, push and verify a NuGet plug-in package, and generate early-bound .NET
classes. Group: `plugin`, plus the external `pac` CLI. Flags/choices: `crm plugin
--help`. Registration verbs and trace logs: `reference/automation.md`.

## Deploy a plug-in package from a local build and verify it

For a NuGet plug-in package (a plug-in plus its dependencies) the Power Platform
CLI (`pac`) owns build scaffolding and push; `crm` owns the target check, the
registration check, the step and the trace read. Proven on Ubuntu with PAC 2.14.1
on .NET 10 against a Dataverse online dev org, a synthetic package
`new_TEST1130Probe` with one dependency (Newtonsoft.Json). Use a clear synthetic
prefix like that for every test artifact.

```bash
# 0. Target. PAC takes the same application credentials as the crm profile.
# Point PAC at the same org, then run the PAC target check in
# reference/setup.md (pass the same --profile on every crm command below) and
# push only on a confirmed match.
pac auth create --name <n> --applicationId <app-id> --clientSecret <secret> \
    --tenant <tenant-id> --environment <org-url>

# 1. Scaffold and build. Add the dependency as a PackageReference and set
# <PackageId> in the .csproj; the .nupkg lands in bin/Release/.
pac plugin init --skip-signing --author test
dotnet build -c Release
unzip -l bin/Release/*.nupkg        # the plug-in and dependency DLLs under lib/net48/

# 2. Seed the package row ONCE (first registration only). `pac plugin push` needs
# an existing --pluginId and crm has no pluginpackages verb, so use entity create;
# pkg.json holds name, uniquename, version and content (base64 of the .nupkg).
crm --json entity create pluginpackages --data-file pkg.json --no-return

# 3. Push. Same command for every later push of a version-bumped rebuild.
pac plugin push --pluginId <pluginpackageid> \
    --pluginFile bin/Release/<PackageId>.<ver>.nupkg --type Nuget

# 4. Verify the registration.
crm --json query odata pluginpackages --select name,version,uniquename
crm --json query odata pluginassemblies --filter "name eq '<assembly>'" \
    --select name,version,_packageid_value
crm --json plugin list-types --assembly <assembly>

# 5. Register a step (solution-scoped), then trigger it.
crm --json solution create --name TEST1130Sol --publisher <publisher-uniquename>
crm --json plugin register-step --plugin-type <Namespace.Class> --message Create \
    --entity account --stage preoperation --mode sync --solution TEST1130Sol
crm --json entity create accounts --data '{"name":"TEST1130-OK"}'
```

Read the traces with the loop in `reference/automation.md` (set `plugintracelogsetting` to `2`, rerun,
read, set back to `0`), then check the business outcome (here, a `description`
the plug-in stamps on the created account).

What the push does and what to expect:

- **Push creates the `pluginassembly` (linked by `_packageid_value`) and the
  `plugintype` rows.** No `register-assembly` or `register-type` on this path.
  The dependency gets no row of its own. A dependency loading in the sandbox is
  proven by the plug-in using it (the trace showed its JSON output).
- **`pluginpackages.version` can lag.** It still read `1.0.0` after pushing
  `1.0.1`; the assembly row read `1.0.1.0`. Check the assembly row for the
  deployed version.
- **Trace `typename` is assembly-qualified** (`<Class>, <assembly>, Version=...`),
  so filter with `startswith`, as in the trace loop in `reference/automation.md`.
- **Traces can lag.** A read 5 s after the run returned nothing and one about
  25 s later returned both rows (a single run, so not a bound): retry.

A deliberate failure (the plug-in throws `InvalidPluginExecutionException`) gives
a useful diagnostic. `entity create` exits 1 with `ok: false`, the plug-in's own
message as `error`, `status` 400, `code` `0x80040265`, `category` `validation`,
`retryable` false, and no row is created. The trace row carries the plug-in's
trace lines in `messageblock` and the fault text plus stack in `exceptiondetails`
(add it to `--select`).

Clean up a test run in this order: the test records, the step, the package
(its assembly and type rows go with it), the test solution, then
`plugintracelogsetting` back to `0`. Never put credentials, org URLs, ids or
business data in saved evidence.

## Generating early-bound classes

There is no `crm codegen` verb — early-bound .NET class generation is an external
Microsoft toolchain, and which tool you use depends on the target:

- **On-prem v9.x → `CrmSvcUtil.exe`** is the only Microsoft-supported path; the
  Power Platform CLI is not available for Dynamics 365 CE (on-premises). It ships in
  the [`Microsoft.CrmSdk.CoreTools`](https://www.nuget.org/packages/Microsoft.CrmSdk.CoreTools)
  NuGet package — the same one that provides SolutionPackager.
- **Dataverse online → `pac modelbuilder build`** (Power Platform CLI) is the
  recommended tool. It is **online-only** — it cannot target on-prem. `CrmSvcUtil.exe`
  still works against online too, but Microsoft recommends `pac modelbuilder` there.
- **XrmToolBox Early Bound Generator V2** is a UI that writes a `builderSettings.json`
  and *calls `pac modelbuilder build`* under the hood — so it inherits the
  **online-only** constraint. It is not an on-prem path (only the older SDK-based EBG was).

Credential boundary: never put a stored profile secret on a codegen command line
(process-list leak; secrets live in the keyring / 0600 file, never on the CLI). On
on-prem, use `CrmSvcUtil.exe /interactivelogin`, which collects the server URL and
credentials in a dialog — every other connection parameter on the command line is
ignored. Look up the org URL to enter from the active profile:

```bash
# org URL to type into the interactive-login dialog:
crm profile list --json | jq -r '.data[] | select(.active).url'

# on-prem early-bound classes (server + credentials entered in the dialog):
CrmSvcUtil.exe /interactivelogin /out:EarlyBound.cs /namespace:Xrm
```

See MS Learn "Create early-bound entity classes with the Code Generation tool" and the
`pac modelbuilder build` reference for the full parameter set.
