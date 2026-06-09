# In-org solution clone via export → GUID-regen → rename → import

This CLI does not support cloning a solution within the same org — taking an
existing solution, regenerating all org-unique component GUIDs, renaming the
entity logical names and the solution unique name, and re-importing the result
as an independent second solution. There is no `crm solution clone` verb, and
there won't be one.

The CLI deliberately stops at the **supported primitives**: `solution export`,
`solution import`, `solution extract`, `solution pack`, and `solution validate`.
What `clone` would add is the *transformation layer* in between — and that layer
is unsupported XML surgery against the platform's internal serialization, the
same class of work this project already rejected for BPF definition authoring
(see [`bpf-definition-via-webapi.md`](bpf-definition-via-webapi.md)).

## Why this is out of scope

**There is no supported "rename entity" or "clone solution as new" API.** A
table's schema/logical name is fixed at creation and immutable — Dataverse
offers no rename. The export→rewrite-`customizations.xml`→import dance only
"works" because import treats a rewritten logical name as a *brand-new* table.
That is a community XML-surgery technique, not a documented Microsoft path. The
supported clone verbs, `CloneAsPatch` and `CloneAsSolution`, **version** a
solution; they do not rename entities or regenerate component GUIDs.

**The transformation is a broad, brittle GUID-rewrite surface.** The originating
issue enumerates it precisely — every org-unique id must be regenerated and kept
internally consistent:

- `formid`, `savedqueryid`, workflow / SLA / template / webresource ids
- FormXml element ids: `id=`, `labelid=`, `uniqueid=`, `handlerUniqueId=`,
  `libraryUniqueId=`
- **BPF XAML `x:String` text-content GUIDs**
- `[Content_Types].xml` kept in sync with renamed webresource filenames
- `solution.xml` `RootComponents` rewritten

A single missed id doesn't fail loudly — it silently cross-links the clone back
to the source component, corrupting one or both. Generalizing a solution-specific
script (the issue's working ~400-line proof-of-concept) to *arbitrary* solution
shapes is materially harder than the tuned original, with a much larger failure
surface.

**It re-enters the BPF `clientdata` swamp.** Any cloned solution containing a
business process flow forces regeneration of its XAML GUIDs — touching the same
undocumented, unstable `Microsoft.Crm.Workflow.ObjectModel` serialization that
[`bpf-definition-via-webapi.md`](bpf-definition-via-webapi.md) rejected as not
hand-authorable. Cloning is not authoring, but it depends on the same fragile
format being stable across a rewrite.

**It cannot be verified the way this project requires.** Mocked tests would pass
while a real import failed or silently mis-linked components — the exact
false-green trap that bit the online-OAuth work (msal does network discovery at
construction; mocks miss it). A correct clone can only be proven by a live
export → transform → import → publish cycle against a real org, per solution
shape. That verification cost lands on a human every time, which makes this a
poor fit for the CLI's automatable, mock-testable command surface.

## Supported alternative

The primitives are all shipped. A one-off, solution-specific transform script
that drives `crm solution export` → local `customizations.xml` rewrite →
`crm solution pack` → `crm solution validate` → `crm solution import` is the
right tool for a known solution: it can be tuned to that solution's exact
component set and verified by hand against the org. Keeping that logic *outside*
the CLI is the deliberate boundary — the CLI ships the supported, testable
verbs; the unsupported per-solution surgery stays a script.

If Microsoft ever publishes a supported in-org clone-with-rename recipe, this
rejection can be revisited.

## Prior requests

- #166 — "feat: solution clone — full in-org solution clone via
  export/GUID-regen/rename/import"
