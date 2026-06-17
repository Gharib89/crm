# How-to: action

OData function and action recipes, taken from the CRMWorx build (§4, §11). See the
[CLI reference](../reference/cli.md) for every flag.

## Call an unbound function

```bash
crm --json action function RetrieveCurrentOrganization --params '{"AccessType":"Default"}'
```
`--params` is a JSON dict encoded inline per OData v4; returns the function result under `data`.

## Call a bound function

```bash
# Collection-bound: GET <entityset>/Microsoft.Dynamics.CRM.<Fn>(params)
crm --json action function <FunctionName> --bind-set <entityset>

# Record-bound: GET <entityset>(<guid>)/Microsoft.Dynamics.CRM.<Fn>(params)
crm --json action function RetrieveUserPrivileges --bind-set systemusers --bind-id <systemuser-guid>
```
`--bind-set` alone binds to the collection; adding `--bind-id` binds to a single record. Override the `Microsoft.Dynamics.CRM` namespace segment with `--cast <namespace>` only for custom namespaces. `--bind-id` requires `--bind-set`. Functions issue a **GET** (read-only, params encoded inline) — unlike `action invoke`, which issues a **POST**.

## Invoke an unbound action from a body file

```bash
crm --json action invoke AddAppComponents --body-file /tmp/cwx_addcomponents.json
```
Use `--body-file` (or `--body`) for the JSON payload; `AddAppComponents` binds typed entity references to a model-driven app.

## Invoke a bound action

```bash
crm --json action invoke <ActionName> --bind-set workflows --bind-id <workflow-guid> --body '{}'
```
`--bind-set` + `--bind-id` bind the action to a record; pass both together (override the namespace with `--cast` only for custom actions). `<ActionName>` is the action's schema/logical name (e.g. as listed in the entity's metadata).
