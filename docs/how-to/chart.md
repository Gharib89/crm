# How-to: chart

Create, inspect, and delete system and user charts headlessly, without the chart
designer. See the [CLI reference](../reference/cli.md) for every flag.

## List charts for an entity

```bash
crm chart list contact
crm chart list contact --user          # user-owned charts
```

Output columns: `name`, `savedqueryvisualizationid` (or `userqueryvisualizationid`),
`isdefault` (system charts only). Every D365 org ships several stock system charts
for standard entities such as `contact`, `account`, and `opportunity`.

## Get a single chart by ID

```bash
crm --json chart get <savedqueryvisualizationid>
crm --json chart get <userqueryvisualizationid> --user
```

Returns the full chart record including `datadescription` and
`presentationdescription` XML.

## Create a chart from XML files

Author the `datadescription` (aggregate FetchXML) and `presentationdescription`
(rendering XML) offline, commit them to source control, then create the chart from
those files:

```bash
crm --json chart create contact \
  --name "Contacts by Source" \
  --data-description ./charts/contacts_by_source.data.xml \
  --presentation-description ./charts/contacts_by_source.pres.xml \
  --solution MyCRMSolution
```

Use `--no-publish` to defer `PublishAllXml` (the default is `--publish`).

### Example datadescription XML

```xml
<datadefinition>
  <fetchcollection>
    <fetch aggregate="true">
      <entity name="contact">
        <attribute alias="count" name="contactid" aggregate="count" />
        <attribute alias="source" name="leadsourcecode" groupby="true" />
      </entity>
    </fetch>
  </fetchcollection>
</datadefinition>
```

### Example presentationdescription XML

```xml
<Chart>
  <Series>
    <Series ChartType="Column" IsValueShownAsLabel="True" />
  </Series>
  <CategoryAxis>
    <Axis />
  </CategoryAxis>
</Chart>
```

## Create a chart backed by a web resource

For script-based (JavaScript) visualizations, pass the web resource name instead
of XML files. The two modes are mutually exclusive.

```bash
crm --json chart create contact \
  --name "D3 Contacts Chart" \
  --web-resource cwx_/scripts/contacts_chart.js \
  --solution MyCRMSolution
```

The web resource is resolved by name to its GUID before the chart is created.

## Create a user-owned chart

Pass `--user` to any write verb to target `userqueryvisualizations` instead of
`savedqueryvisualizations`:

```bash
crm --json chart create contact \
  --name "My Personal Chart" \
  --data-description ./data.xml \
  --presentation-description ./pres.xml \
  --user
```

## Delete a chart

```bash
crm --json chart delete <savedqueryvisualizationid>
crm --json chart delete <userqueryvisualizationid> --user
```

## Dry-run preview

`--dry-run` on any write verb returns `{_dry_run: true, would_*}` without issuing
the write:

```bash
crm --dry-run --json chart create contact \
  --name "Preview Chart" \
  --data-description ./data.xml \
  --presentation-description ./pres.xml
```

## Reuse charts across entities

`metadata clone-entity --with-charts` uses the same chart core to retarget
`datadescription` FetchXML when cloning an entity — the XML entity name and
`primaryentitytypecode` both move to the clone.
