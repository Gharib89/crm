# Cloning a plug-in step onto a different entity

This CLI does not offer a `plugin register-step --from-existing <step-guid>` verb that reads an existing `sdkmessageprocessingstep` and re-creates it on another entity, changing only the `sdkmessagefilterid`. There is no clone-a-step orchestration, and there won't be one.

To be precise about scope: registering a step from explicit parameters stays in scope and is supported today — `crm plugin register-step --message --plugin-type --entity --stage --mode --rank --filtering-attributes` POSTs a step bound to its message, plug-in type, and (entity-given) filter. What is rejected is the *clone-from-an-existing-row* convenience: "copy that step, point it at this entity."

## Why this is out of scope

**The trigger is a one-time migration, not a recurring workflow.** The only stated use is duplicating an entity during a solution-clone campaign (EMAP), where the source entity's plug-in steps need re-registering on the clone. That is a transient job. A permanent CLI verb is the wrong shape for a migration you run once.

**A faithful clone is deceptively deep — and a partial clone fails silently.** The request frames it as "an exact copy with only `sdkmessagefilterid` changed." A step is not just its scalar columns. A correct clone has to carry, at minimum:

- the step row (stage, mode, rank, name, `filteringattributes`, `asyncautodelete`, description, supporteddeployment);
- its child **step images** (`sdkmessageprocessingstepimage` — the pre/post image rows holding `entityalias` and the attribute set the plug-in reads). A plug-in that reads `PreImage["name"]` misbehaves with no error if the clone has the step but not the image;
- its secure/unsecure configuration.

Miss any of these and the clone is not "an exact copy" — it is a subtly broken step that throws at runtime, inside the target plug-in, far from the registration call. That silent-corruption surface is precisely the failure class this CLI keeps out of its supported verbs (cf. `bpf-definition-via-webapi.md`). Building it *correctly* means a well-tested clone of every child row plus a live org to verify against — not the small convenience the issue implies.

**The migration shape is SolutionPackager territory.** Plug-in assemblies and their steps travel *inside a solution* as solution components. The canonical D365 path for "duplicate an entity and its registrations" is to export the solution, transform the unpacked customizations, and re-import — which round-trips images and configuration for free, because it is the platform's own serialization. crm already ships those primitives (`solution export` / `extract` / `pack` / `import`). A bespoke step-cloner reimplements a slice of that, worse.

**The supported primitives already suffice for a handful of steps.** The issue counts "15 commands for 5 steps." But that is 5 steps, once. The existing `entity get` / `query odata` / `entity create` verbs replicate it directly:

```bash
# 1. Read the source step's fields
crm --json entity get sdkmessageprocessingsteps <source-step-guid> \
  --select name,stage,mode,rank,filteringattributes,_sdkmessageid_value,_plugintypeid_value

# 2. Resolve the target entity's filter for the same message
crm --json query odata sdkmessagefilters \
  --filter "primaryobjecttypecode eq '<new-entity>' and _sdkmessageid_value eq <message-guid>" \
  --select sdkmessagefilterid

# 3. Re-create the step (and, for each image, its sdkmessageprocessingstepimage)
crm --json entity create sdkmessageprocessingsteps --data '{ ...copied fields, new sdkmessagefilterid@odata.bind... }'
```

For a one-off this is a short throwaway script, which is the right home for migration orchestration: the CLI ships supported primitives; the gluing-together stays a script.

## Supported alternative

Register steps explicitly with `crm plugin register-step`, or — for an entity-duplication migration — clone through the solution layer (`solution extract` → transform → `solution import`), which carries steps, images, and config as solution components. For a few ad-hoc steps, the `entity get`/`query odata`/`entity create` recipe above replicates the registration without a new verb.

## Prior requests

- #169 — "plugin register-step --from-existing — clone an existing step onto a different entity"
