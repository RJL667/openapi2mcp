# Lakekeeper — Iceberg REST catalog as a read-only MCP surface

Generated from Lakekeeper's own committed spec, unmodified:

    docs/docs/api/rest-catalog-open-api.yaml

Command:

```bash
python3 openapi2mcp.py \
  --spec https://raw.githubusercontent.com/lakekeeper/lakekeeper/main/docs/docs/api/rest-catalog-open-api.yaml \
  --name lakekeeper_cat --out ./examples/lakekeeper --max-tools 8 \
  --include 'namespace|table|view'
```

8 tools, `initialize` + `tools/list` + per-tool schema check green:

| tool | required args |
|---|---|
| `listnamespaces` | prefix |
| `loadnamespacemetadata` | prefix, namespace |
| `listtables` | prefix, namespace |
| `loadtable` | prefix, namespace, table |
| `loadcredentials` | prefix, namespace, table |
| `fetchplanningresult` | prefix, namespace, table, plan-id |
| `listviews` | prefix, namespace |
| `loadview` | prefix, namespace, view |

This is the *discovery* surface — the "where does tableA live, and what is its
schema" question that has to work before any agent can write a dbt model against
the catalog. Every tool is a GET; nothing here can mutate the catalog.

The companion management surface (`docs/docs/api/management-open-api.yaml`)
generates the permissions/assignments tools — warehouses, roles, project and
server assignments — and is the natural second slice once read-only discovery is
proven.

Run it:

```bash
cd examples/lakekeeper
python3 smoke_test.py          # schema check
python3 smoke_test.py --call   # execute against a live Lakekeeper
```
