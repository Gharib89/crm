# Automation — plug-ins and workflows

Register plug-in assemblies and processing steps; manage classic workflows and
business rules. Groups: `plugin`, `workflow`. Flags/choices: `crm <group> --help`.

## Plug-ins — `plugin` (assembly + step lifecycle)

The full workflow is **upload assembly → verify the platform-generated types →
register a step** against one of those types:

```bash
# register-assembly: .dll bytes are base64'd into `content`. --solution sends
# MSCRM.SolutionUniqueName.
crm --json plugin register-assembly ./bin/Contoso.Plugins.dll --solution cwx_contoso

# --update: re-uploads content of an existing assembly (resolved by name); the
# identity flags --version/--culture/--public-key-token/--description/--isolation-mode
# are IGNORED under --update and produce a warning.
crm --json plugin register-assembly ./bin/Contoso.Plugins.dll --update

# list-types: platform-generated rows in plugintypes (one per IPlugin class).
crm --json plugin list-types --assembly Contoso.Plugins

# register-step: --message and --plugin-type are required. async forces postoperation.
# --entity sets primaryobjecttypecode (omit = all entities); --filtering-attributes
# (comma-separated) restricts an Update step. The step name is auto-derived as
# '<typename>: <message> of <entity>'; pass --name when that would exceed the
# 256-char platform limit.
crm --json plugin register-step \
    --message Update \
    --plugin-type Contoso.Plugins.AccountPostUpdate \
    --entity account --stage postoperation --mode sync \
    --filtering-attributes name,telephone1
```

```bash
# unregister-step: by name or GUID; an ambiguous name errors (use the GUID).
crm --json plugin unregister-step "Contoso.Plugins.AccountPostUpdate: Update of account" --yes

# unregister-assembly: cascades — deletes dependent steps first, then the assembly.
crm --json plugin unregister-assembly Contoso.Plugins --yes
```

`--dry-run` skips all writes (resolution GETs still fire); the `--json` envelope
carries `meta.dry_run: true`.

## Workflows — `workflow`

```bash
crm --json workflow list --entity cwx_ticket --category 0   # definitions on an entity

crm --json workflow activate <workflow-guid>
crm --json workflow deactivate <workflow-guid>
# If the GUID is a type=2 activation record the command returns ok:false with a hint naming the parent draft GUID to use instead.

crm --json workflow run <workflow-guid> --target <record-guid>   # trigger on-demand

# Clone a classic workflow onto another entity (xaml-retargeted; activates by default)
crm --json workflow clone <workflow-guid> --to-entity cwx_ticketclone
crm --json workflow clone <workflow-guid> --to-entity cwx_ticketclone \
    --name "My Clone" --solution my_solution --no-activate

# Export / import a workflow definition (incl. xaml) as JSON
crm --json workflow export <workflow-guid> --out ./wf.json
crm --json workflow import --file ./wf.json --activate
```

Category values: `0`=Workflow, `1`=Dialog, `2`=BusinessRule, `3`=Action, `4`=BPF,
`5`=ModernFlow. **Clone supports only `0` and `2`** — action/BPF/dialog/modern-flow
fail loudly. (This is the same constraint the entity-clone `--with-workflows` flag
hits; see `reference/metadata.md`.)
