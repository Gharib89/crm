# Composing workflow step XAML — the logic path of `workflow update`

`workflow update --xaml-file` takes a **whole** workflow XAML definition and PATCHes
it wholesale; it does not splice fragments. This reference is how you hand it a
finished, valid blob: the constraints that decide *whether* you can edit at all, the
routine the verb runs, and a snippet library for the common step shapes. Flags,
choices, and defaults live in `crm workflow update --help` / `crm describe workflow`
— this states only what those cannot.

## The provenance wall — when step-XAML editing is possible at all

The platform refuses any workflow definition whose step XAML it did not author
itself. This is **provenance-sensitive, not target-sensitive**: an unmodified
designer definition (clone / solution import) passes, but the moment you hand-edit
the `xaml` and write it back, the platform checks where that XAML came from.

- **On-prem only.** Editing step XAML works on on-premises orgs, against definitions
  the platform authored. On **Dataverse (cloud)** it is impossible — both direct
  PATCH and the sanctioned solution-import path are rejected identically — so the
  verb **refuses up front on a cloud (OAuth) profile, before any write**, rather than
  leaking the raw server fault. There is no override flag. Compose and import the
  workflow through the maker portal on cloud instead.
- **Even on-prem it is org-config-gated.** A deployment that does not permit
  hand-authored (non-UI) XAML refuses too.

The two server faults you will see, surfaced as a clean error that preserves
`status` / `code` / `response_body`:

| code | meaning |
|------|---------|
| `0x80045040` | XAML provenance is **not the web designer** — the definition was created or edited outside the D365 web application. The wall itself. |
| `0x80045041` | This **on-prem org does not permit non-UI (hand-authored) XAML** — a deployment-level gate, distinct from the provenance check. |

## The direct-PATCH routine

A workflow definition is locked while active, so the logic path runs
**deactivate-if-active → PATCH `xaml` → reactivate**. The verb drives this for you;
the conceptual shape matters because of where it can fail:

- **The server's reactivate step is the only compile authority.** The pre-write
  reference validation (below) is advisory — it catches dangling references, not
  compile errors. Whether the new XAML actually compiles is decided when the platform
  reactivates the definition.
- **Reactivation failure rolls back by default**: the prior XAML is restored and both
  errors reported. Suppressing rollback leaves the rejected definition as a draft for
  inspection.
- A `type=2` activation-record id resolves to its `type=1` parent first — edit the
  parent definition, never the activation copy.

## Composing the blob

Start from a **real exported definition** of the workflow you are editing
(`workflow export <id>`), not a skeleton built from scratch — it carries the correct
`x:Class`, the `InputEntities` member, and the namespace declarations the server
expects. Add your steps inside the `<mxswa:Workflow>` element. The root looks like:

```xml
<Activity x:Class="XrmWorkflow«32-hex-no-dashes»"
  xmlns="http://schemas.microsoft.com/netfx/2009/xaml/activities"
  xmlns:mxs="clr-namespace:Microsoft.Xrm.Sdk;assembly=Microsoft.Xrm.Sdk, Version=9.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35"
  xmlns:mxswa="clr-namespace:Microsoft.Xrm.Sdk.Workflow.Activities;assembly=Microsoft.Xrm.Sdk.Workflow, Version=9.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35"
  xmlns:scg="clr-namespace:System.Collections.Generic;assembly=mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"
  xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
  <x:Members>
    <x:Property Name="InputEntities" Type="InArgument(scg:IDictionary(x:String, mxs:Entity))" />
  </x:Members>
  <mxswa:Workflow>
    <!-- steps go here -->
  </mxswa:Workflow>
</Activity>
```

Two gotchas that produce a definition that parses but then fails the wall or the
server compile:

- **`xmlns:s` injection.** A literal typed value (a string, number, or boolean
  constant) is serialized with the `s:` (System / mscorlib) prefix — e.g.
  `s:String`. The exported skeleton does **not** declare that prefix. If a snippet
  you paste uses `s:`, inject the declaration on the root `<Activity>` element or the
  XAML carries an unbound prefix:
  ```
  xmlns:s="clr-namespace:System;assembly=mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"
  ```
- **Validate with ElementTree, not minidom.** Check well-formedness with
  `xml.etree.ElementTree` — it raises on an *unbound namespace prefix*, which is
  exactly the failure `xmlns:s` injection prevents. `minidom` is laxer about
  namespaces and will accept XAML that the server then rejects, so it gives false
  confidence.

The verb's reference validation will additionally warn (or, under strict mode, fail)
when a step names an attribute that does not exist on the workflow's primary entity,
or omits a required argument — fix those before reactivation rather than discovering
them as a rollback.

## Snippet library

Drop these inside `<mxswa:Workflow>`. `«slots»` are placeholders. Each is a starting
shape grounded in `Microsoft.Xrm.Sdk.Workflow.Activities` — the server reactivate is
the final authority, so for an unfamiliar step type the surest route is to build that
one step in the designer and `workflow export` it to see its exact serialization.

**Set a field to a static value.** `Entity` is the in-context record reference;
`Value` is the literal.

```xml
<mxswa:SetEntityProperty Entity="«record-ref»" EntityName="«entity-logical-name»"
                         Attribute="«attribute-logical-name»" Value="«literal»" />
```

**Set a field from another field.** Read the source attribute first, then bind it
into the target's `Value` via the property-element form (the designer carries the
read→write binding through a workflow variable; export a designer-built example when
the binding expression is unfamiliar):

```xml
<mxswa:GetEntityProperty Attribute="«source-attribute»" Entity="«source-record-ref»"
                         EntityName="«entity-logical-name»" />
<mxswa:SetEntityProperty Entity="«target-record-ref»" EntityName="«entity-logical-name»"
                         Attribute="«target-attribute»">
  <mxswa:SetEntityProperty.Value>
    <mxs:Entity />
  </mxswa:SetEntityProperty.Value>
</mxswa:SetEntityProperty>
```

**Create a record.** `Entity` is the in/out reference for the new record; populate its
fields with nested `SetEntityProperty` steps:

```xml
<mxswa:CreateEntity Entity="«new-record-ref»" EntityName="«entity-logical-name»" />
<mxswa:SetEntityProperty Entity="«new-record-ref»" EntityName="«entity-logical-name»"
                         Attribute="«attribute-logical-name»" Value="«literal»" />
```

**Change status.** `SetState` updates state/status on the target record; `EntityId`
(the record GUID) is required:

```xml
<mxswa:SetState EntityName="«entity-logical-name»" EntityId="«record-guid»"
                State="«state-code»" Status="«status-code»" />
```
