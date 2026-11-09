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

`clear` is not "compatible": the rules cover few kinds, and a component no rule
covers is `unknown`.

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

Classic workflows have their own check, `crm workflow migration-assess`. It reads
the org through a profile and judges only their conversion to cloud flows, which
Dataverse online does not require. Enter its findings as "deprecated but
working" conversion candidates marked "needs redesign"; they become confirmed
blockers only when the plan requires the conversion.

## Read the registration findings

Two rules judge how a component calls a script, not the script itself:
`form-handler-no-execution-context` (kind `form`) and
`ribbon-action-no-primary-control` (kind `ribbon_diff`). Each is a `deprecated`
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

`csproj-sdk-net462` judges the project, not the registered assembly. It runs only when
`assess` is given the source map and the repository (`reference/source-mapping.md`), and
only for an assembly the map matched to exactly one project. Without them, or for an
unmapped assembly, no project was read: a `clear` there says nothing about the build.

- **`blocker`** means the project targets .NET Framework below 4.6.2 (a project that
  declares no version counts) or references the SDK 5.0 `Microsoft.Xrm.Sdk` or
  `Microsoft.Crm.Sdk.Proxy`. The evidence's `fired` says which: `framework`,
  `sdk_references` or both. Quote it with `target_framework_version`, `sdk_references` and
  the repository `path`. The floor's source is in `rules_hit`.
- **`unknown`** reasons start `project <path>`: the file is missing, not UTF-8 or not
  well-formed XML, or declares its framework under a condition. Fix the repository or the
  map, then re-run.
- The same project is what `convert --repo` retargets (`reference/remediation.md`), so a
  blocker here has a patch there; an SDK-style project is neither flagged nor patched.

Done when every component carries one of the four classes with its rule or
evidence, or an entry open on a named question, and no `clear` or `unknown` is
left unjudged.
