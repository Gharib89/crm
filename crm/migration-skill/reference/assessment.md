# Assessment: sort findings into four classes

Step 3 of the workflow. `crm inventory assess <inventory> --target <target>
--out <file>` is offline. Run it once per target the plan passes through (the
on-premises 9.1 stop and the cloud), into separate files.

When `--in-use` or `--exclude-unknown` was passed, `counts.by_class` covers only
the rows left: quote it with the flag named.

## Map findings to the four classes

Read `counts.by_class` in the summary, not the exit code: a blocker exits 0.

| Finding class | Workflow class |
|---|---|
| `blocker` | Confirmed blocker |
| `deprecated` | Deprecated but working |
| `optional` | Optional modernization |
| `unknown` | Unknown |
| `clear` | None yet: judge it from the component's source or runtime evidence |

A classic dialog (`process-dialog`, a process of category 1) is `deprecated`:
enter it as "Deprecated but working" and name the replacement, a business
process flow or a canvas app. There is no automatic conversion.

A classic workflow (category 0) is read from its stored XAML. `workflow-wait-condition`
(a `Postpone` activity) and `workflow-custom-activity` (an `ActivityReference` to an
assembly other than `Microsoft.Crm.Workflow`) are `optional`: enter them as "Optional
modernization": a cloud flow has no equivalent for either, and Dataverse online still
runs classic workflows. A classic workflow with no readable XAML is `unknown`.

A custom Reporting Services report whose data provider is `sql` (`report-sql-datasource`,
online target only) is a `blocker`: enter it as "Confirmed blocker". Dataverse online runs
Fetch-based reports and the migration removes unconverted custom SQL-based reports, so the
report is lost, not the migration stopped. The fix is to rewrite its datasets in FetchXML;
Microsoft documents no converter, and the result must still pass the Dataverse RDL sandbox.
A custom report on `fetch` is `clear`. One on `unknown` carries the record's reason: a missing
RDL body, or a shared data source whose provider lives in SSRS. Settle it on the live org or in
SSRS: until then the provider is undecided. Stock reports and custom link, Excel or Power BI reports are
`clear` for this rule.

`clear` is not "compatible": the rules cover few kinds, and a component no rule
covers is `unknown`.
For a process, `clear` means only that the process rules did not fire. The inventory
does not record a process's mode, so a `clear` classic workflow says nothing about
real-time (synchronous) mode, and a business process flow or a modern flow has no
rule.

## Quote the evidence

Copy each finding's rule id and `evidence`, and the rule's `fix` and Microsoft
Learn `sources` (the findings file's `rules_hit`, keyed by rule id), into the
report beside its class; a class without them is an assertion.

## Turn every unknown into a question or a manual-review entry

Read each `unknown`'s `reason` and act on it:

- **`no rule`**: write a manual-review entry naming the component and the
  evidence that would settle it (its source, a runtime trace, the
  documentation).
- **`field ... not recorded`**: the inventory holds no value for the field the
  rule needs. Re-extract with the current crm; if the org left the field empty,
  ask the user what it should hold.
- **`extraction gap`** or **`kind coverage`**: the inventory never fully read it
  (`reference/inventory.md`). Close the gap, or carry the unknown forward with
  its reason.
- **`source version`**: extract could not read the org's version. Re-extract.
- **`artifact unreadable`**, **`artifact changed since extraction`** or **`no
  text artifact`**: re-extract; a changed artifact means the folder was edited
  after extraction.

`unattached_gaps` counts extraction gaps that name no component; they stay in
the report as unknowns too.

Classic workflows also have `crm workflow migration-assess`. It is live (it reads
the org through a profile), covers category 0 only, also judges real-time mode, and
writes no findings file, and no package holds its verdicts. The inventory's two
workflow rules give the same wait and custom-activity verdicts; only `migration-assess`
sees mode. Dataverse online
does not require the conversion, so enter its findings as "Optional modernization"
marked "needs redesign"; they become confirmed blockers only when the plan requires
the conversion.

## Read the registration findings

Five rules judge how a component calls a script, not the script itself:
`form-handler-no-execution-context` (kind `form`),
`form-library-no-odata-adapter` (kind `form`),
`web-resource-page-no-odata-adapter` (kind `web_resource`, HTML pages only),
`ribbon-action-no-odata-adapter` (kind `ribbon_diff`) and
`ribbon-action-no-primary-control` (kind `ribbon_diff`).
`form-library-no-odata-adapter` flags a form whose library list names a script
with a `web-resource-odata-v2` finding and has no OData v2 adapter listed ahead
of it (an adapter defines `global.CrmODataV2 =`); its evidence is the first such
`library`. `web-resource-page-no-odata-adapter` does the same for an HTML page: a
`<script src>` naming such a script, or an OData v2 URL in a string of an inline script,
with no adapter `<script src>` ahead of it; its evidence is the first such `library`, or
`inline` holding the page line of the first inline call. The URL in a comment or in markup is no call. A page whose scripts
cannot be placed (a tag in a comment, a script that writes another, an unclosed script)
or whose library cannot be read is `unknown`; a `src` matches only the web resource it
resolves to against the page's own name, never one that shares a file name.
`ribbon-action-no-odata-adapter` flags a ribbon command whose action calls such a script with no
action on an adapter ahead of it (evidence: `command`, `function`, `library` of the first such
action), and a `CustomRule` calling one (evidence: `rule`, `function`, `library`): a rule runs at
render, before any action can load the adapter. `form-handler-no-execution-context` and `ribbon-action-no-primary-control` are each a `deprecated`
finding when the library the registration calls carries an `Xrm.Page` finding
(`web-resource-xrm-page`) and the registration hands that library no context: an
enabled form handler without `passExecutionContext`, or a `JavaScriptFunction` on
a web resource library (`$webresource:` or a `/WebResources/` path) without `PrimaryControl` among its `CrmParameter`s.

- **Evidence** is `registrations`, one entry per flagged registration (form:
  `event`, `attribute`, `function`, `library`, the current
  `passExecutionContext`; ribbon: `command`, `function`, `library`,
  `crm_parameters`), with `match_count` for the total. Quote the entries.
- **`clear`** means every enabled handler or action in scope passes the context or
  calls a library with no `Xrm.Page` text. It says nothing about the library.
- **`unknown`** reasons: `library <name> is not in the inventory`, `library <name>
  is not a script web resource`, `library <name>: <artifact problem>`, `artifact is
  not well-formed XML`. Resolve them as the list above does; a missing library is
  often a platform or managed script, so name it in the entry.
- **Fix registration and function together.** The context becomes the handler's
  first argument, so setting the flag shifts every parameter the handler already
  configures; edit the function in the same change as the library's own finding.

## Read the plug-in project finding

`csproj-sdk-net462` judges the project, not the registered assembly, and only for an
assembly the source map (`reference/source-mapping.md`) matched to exactly one project,
with the map and the repository passed to `assess`. Anywhere else no project was read:
a `clear` there says nothing about the build.

- **`blocker`** means the project targets .NET Framework below 4.6.2 (`target_framework_version`
  null: none declared, which counts) or references the SDK 5.0 `Microsoft.Xrm.Sdk` or
  `Microsoft.Crm.Sdk.Proxy`. The evidence's `fired` says which: `framework`,
  `sdk_references` or both. Quote it with `target_framework_version`, `sdk_references` and
  the repository `path`. The floor's source is in `rules_hit`.
- **`unknown`** reasons start `project <path>` and say why the project could not be
  judged (unreadable, not an MSBuild project, or no single unconditional framework
  version). Fix the repository or the map, then re-run.
- The same project is what `convert --repo` retargets (`reference/remediation.md`), so a
  blocker here has a patch there; an SDK-style project is neither flagged nor patched.

## Read the plug-in source findings

`plugin-source-sql-client`, `plugin-source-filesystem` and `plugin-source-registry` read the
C# of the same mapped project, so they need the source map and the repository passed to
`assess`, and judge `--target online` only. Without both flags, or for an unmapped or
ambiguous assembly, no source was read: a `clear` there says nothing about the code.

- **`blocker`** is one finding per file and rule, for code that creates or accesses a type the
  online sandbox blocks: a SQL client (`plugin-source-sql-client`), the file system
  (`plugin-source-filesystem`) or the registry (`plugin-source-registry`). The rule's `title`
  and `sources` in `rules_hit` name the types and the Learn page. Quote the evidence's `path`
  and `match_count`. `locations` holds only the first ten uses (line and column), so re-read
  the file for the rest before calling the refactor done.
- **`unknown`** reasons start `source <path>` and say the file is unreadable, not UTF-8 or
  failed to parse. One `unknown` per file, with no rule; the project's other files still
  report. Open the file: fix a real syntax error or the map, or name the file in the entry
  when it is valid C# the parser rejects, then re-run.
- **Scope**: the project's own `.cs` files. A shared library reached by a `ProjectReference`
  and a file linked from outside the project folder are not scanned, so name them in the
  entry when the plug-in calls into one.
- **Refactor by hand.** `convert` never reads C#, so a blocker has no patch. Open each cited
  line, move the work to the Dataverse organization service (`IOrganizationService`) or an
  HTTPS service the plug-in calls, and prove it in the target (`reference/verification.md`).

Done when every component carries one of the four classes with its rule or
evidence, or an entry open on a named question, and no `clear` or `unknown` is
left unjudged.
