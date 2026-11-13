# Migration inventory: assess compatibility, roll up readiness

Both verbs read files only and need no profile. Convert and its check are in
`reference/inventory-convert.md`; the `crm-migration` skill, when installed,
says how a migration plan sorts these answers.

## Assessing compatibility (`crm inventory assess`)

`crm inventory assess` runs the compatibility rules shipped with crm over an
inventory, for one target, and writes a findings file. Run it once per target, into
separate files.

- **Every component gets a finding**, keyed by component key: `blocker`,
  `deprecated` or `optional` from a rule hit, `unknown` with a `reason`, or `clear`
  (the rules that apply found nothing). `clear` means only that: judge a `clear`
  component from evidence.
- **The rule families**, each hit's rule id in its finding:
  - web resource scripts: client API calls, the 2011 SOAP and OData v2 endpoints,
    `crmForm`, a static jQuery (`web-resource-*`);
  - plug-in assembly registration: storage in the database and isolation mode
    (`assembly-*`);
  - plug-in steps on `systemuser` (`step-on-systemuser`, `online` only, a blocker:
    the move to online needs no step on that entity); every other step is `clear`,
    which says nothing about its stage, mode or configuration;
  - how forms, HTML pages and ribbon diffs call scripts (Script registrations below);
  - plug-in projects and their C# (Plug-in projects and source below);
  - processes: a classic dialog (`process-dialog`, category 1) is `deprecated` with
    no automatic conversion; a classic workflow (category 0) is read from its stored
    XAML, where a `Postpone` activity (`workflow-wait-condition`) and an
    `ActivityReference` to an assembly other than `Microsoft.Crm.Workflow`
    (`workflow-custom-activity`) are `optional`, and no readable XAML is `unknown`;
  - reports: a Reporting Services report of origin `custom` whose `data_provider` is `sql`
    (`report-sql-datasource`, `online` only) is a `blocker`, its datasets to be
    rewritten in FetchXML; one on `fetch` is `clear`, one on `unknown` carries the
    record's reason; a `sql` report of origin `unknown` is `unknown`; stock (origin
    `system`), link, Excel and Power BI reports are `clear`;
  - stored text naming the source org's host (Source hosts below);
  - Solution Checker results you merge (Solution Checker below).
- **A `clear` process says nothing about its mode.** The inventory records no process
  mode, so a `clear` classic workflow says nothing about real-time (synchronous) mode,
  and a business process flow or a modern flow has no rule.
- **Every `unknown` carries a `reason`**; the per-family ones are in the sections below:
  - `no rule`: no rule judges the component (most kinds).
  - `extraction gap` or `kind coverage`: the inventory never fully read it.
  - `source version`: extract could not read the org's version, so every ruled
    component is unknown: re-extract.
  - `field ... not recorded`: the inventory holds no value the rule needs: re-extract
    with the current crm (`field isolation_mode not recorded` marks an older extract).
  - `artifact unreadable`, `artifact changed since extraction`, `no text artifact`:
    re-extract; a changed artifact means the folder was edited after extraction.

  `unattached_gaps` counts extraction gaps that name no component.
- **Quote the evidence**: a script hit carries the artifact `path`, `sha256` and match
  `locations` (line, column); a registration hit carries the `field` and `value`; a form
  or ribbon hit carries the flagged `registrations`. The fix and the Microsoft Learn
  sources of each rule hit are under `rules_hit`.
- **Read `counts.by_class`, not the exit code**: a blocker exits 0. With `--in-use` or
  `--exclude-unknown`, the counts cover only the rows left.

### Script registrations

Five rules judge how a component calls a script, not the script itself:
`form-handler-no-execution-context` (kind `form`),
`form-library-no-odata-adapter` (kind `form`),
`web-resource-page-no-odata-adapter` (kind `web_resource`, HTML pages only),
`ribbon-action-no-odata-adapter` (kind `ribbon_diff`) and
`ribbon-action-no-primary-control` (kind `ribbon_diff`).

- **The OData adapter rules.** `form-library-no-odata-adapter` flags a form whose
  library list names a script with a `web-resource-odata-v2` finding and no OData v2
  adapter listed ahead of it (an adapter defines `global.CrmODataV2 =`); its evidence
  is the first such `library`. `web-resource-page-no-odata-adapter` does the same for
  an HTML page: a `<script src>` naming such a script, or an OData v2 URL in a string
  of an inline script, with no adapter `<script src>` ahead of it; its evidence is the
  first such `library`, or `inline` holding the page line of the first inline call. The
  URL in a comment or in markup is no call. A page whose scripts cannot be placed (a tag
  in a comment, a script that writes another, an unclosed script) or whose library
  cannot be read is `unknown`; a `src` matches only the web resource it resolves to
  against the page's own name, never one that shares a file name.
  `ribbon-action-no-odata-adapter` flags a ribbon command whose action calls such a
  script with no action on an adapter ahead of it (evidence: `command`, `function`,
  `library` of the first such action), and a `CustomRule` calling one (evidence:
  `rule`, `function`, `library`): a rule runs at render, before any action can load
  the adapter.
- **The context rules.** `form-handler-no-execution-context` and
  `ribbon-action-no-primary-control` are each `deprecated` when the library the
  registration calls carries an `Xrm.Page` finding (`web-resource-xrm-page`) and the
  registration hands that library no context: an enabled form handler without
  `passExecutionContext`, or a `JavaScriptFunction` on a web resource library
  (`$webresource:` or a `/WebResources/` path) without `PrimaryControl` among its
  `CrmParameter`s.
- **Evidence** is `registrations`, one entry per flagged registration (form: `event`,
  `attribute`, `function`, `library`, the current `passExecutionContext`; ribbon:
  `command`, `function`, `library`, `crm_parameters`), with `match_count` for the total.
- **`clear`** means every enabled handler or action in scope passes the context or
  calls a library with no `Xrm.Page` text. It says nothing about the library.
- **`unknown`** reasons: `library <name> is not in the inventory`, `library <name> names
  more than one web resource`, `library <name> is not a script web resource`, `library <name>: <artifact problem>`, `artifact is not
  well-formed XML`. A missing library is often a platform or managed script.
- **Fix registration and function together.** The context becomes the handler's first
  argument, so setting the flag shifts every parameter the handler already configures.

### Plug-in projects and source

Pass `--source-map` (the `map-source --out` file of the same inventory) with `--repo`
for these rules. They read only the project of an assembly the map matched to exactly
one project;
anywhere else no project was read and the assembly reads `unknown`, once per rule that
applies, with a reason naming `--source-map` and `--repo`: never `clear`.

- **`csproj-sdk-net462`** judges the project, not the registered assembly. A `blocker`
  means it targets .NET Framework below 4.6.2 (`target_framework_version` null: none
  declared, which counts) or references the SDK 5.0 `Microsoft.Xrm.Sdk` or
  `Microsoft.Crm.Sdk.Proxy`; `fired` says which (`framework`, `sdk_references` or both),
  beside `sdk_references` and the repository `path`. `convert --repo` retargets the same
  project; an SDK-style project is neither flagged nor patched.
- **`plugin-source-sql-client`, `plugin-source-filesystem` and
  `plugin-source-registry`** (`--target online` only) read the project's own `.cs`
  files: one `blocker` per file and rule, for code that creates or accesses a type the
  online sandbox blocks. Evidence: `path` and `match_count`; `locations` holds only the
  first ten uses, so re-read the file for the rest. A shared library reached by a
  `ProjectReference`, or a file linked from outside the project folder, is not scanned.
  `convert` never reads C#: refactor by hand, moving the work to the organization
  service (`IOrganizationService`) or an HTTPS service the plug-in calls.
- **`unknown`** reasons start `project <path>` (unreadable, not an MSBuild project, or
  no single unconditional framework version) or `source <path>` (unreadable, not UTF-8,
  or syntax errors; one per file, the project's other files still report). Fix the
  repository or the map, then re-run.

### Source hosts

After a database upgrade, stored text that names the old server points at a host that
will be switched off. Pass each host name of the source org with `--source-host`
(repeat it for a short name and a fully qualified one). Without it the five
`source-host-*` rules do not run, and a `clear` says nothing about addresses.

- **`blocker`** is one finding per component whose text names a host: a script or HTML
  web resource, the site map, a ribbon or a process definition. Evidence: `path` and
  `match_count`; `locations` holds the first ten. A match in a comment counts.
- **Whole host, any case.** `crm01` finds `crm01.contoso.com` but not `crm010`; a host
  with a scheme, port or path is refused. The findings file's `source_hosts` records the
  hosts the run used.
- **Not covered**: a plug-in step's configuration is not in the inventory, so nothing
  reads it: ask the plug-in owner.
- **Repair each cited line by hand**: `convert` rewrites no address. Use a relative URL,
  or read the org URL at run time.

### Solution Checker

`--checker-report <file>` merges a Power Apps Solution Checker run you already have (the
zip `pac solution check` wrote, or the SARIF file inside); crm never runs the checker. It
complements the packaged rules and replaces none, and the checker does not flag
everything they do.

- **A matched result is a finding** on that web resource, `rule` the checker's own id
  (`web-avoid-2011-api`). Class: Critical and High `blocker`, Medium `deprecated`, Low
  and Informational `optional`, except `web-use-async` and `web-avoid-eval`, which are
  `deprecated`.
- **Quote `checker_report` and `checker_unmatched`.** `skipped_duplicates` counts results
  a packaged finding already covers; `checker_unmatched` lists what matched no web
  resource (a file outside the inventory, forms, plug-ins, a non-`web-` family): each is
  an open question, never a pass. `convert` rewrites none of them.

## Rolling findings up for online (`crm inventory readiness`)

`crm inventory readiness <findings>` reads a file `assess --target online` wrote (one for
`onprem-9.1` is a usage error) and prints one row per gate: `pass`, `fail`, `warn` or
`unknown`, with the components behind it and a basis. It re-judges nothing.

- **This is a checklist.** It replaces neither the on-premises-to-online (OP2OL) service,
  Lifecycle Services nor Microsoft's nomination, and it performs no upgrade hop.
- **Quote the basis.** `OP2OL whitepaper` marks a documented OP2OL gate; `Dataverse online
  requirement` marks a rule that blocks online but is not one. `warn` is isolation mode
  none: the migration converts it to sandbox mode and the code may still fail there.
- **`unknown` stays open.** A kind with an extraction gap (the SSRS data sources for the
  report row), a rule whose input `assess` was not given (`--source-host`, or
  `--source-map` with `--repo`) and a version that is not dotted read `unknown`.
- **Manual rows carry no status.** Collation, database size, TDE, integrity checks and
  the target users and licences are database-side or tenant-side: ask the owner of each.
- Exit 0 means the checklist was produced, whatever its rows say.
