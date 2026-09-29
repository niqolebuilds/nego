# Metabase and Apache Superset

The engine writes everything it knows to one SQLite file,
`data/warehouse.db`, built by:

```bash
python scripts/build_warehouse.py --breakage 0.10
```

The `mart_*` tables are computed by the same Python functions the MCP tools call,
so a Metabase question and a Claude answer always show the same number. Do not
re-derive prices in SQL: pack-size normalisation and inflation adjustment already
happened in `price_history.adjusted_price_per_clinical_unit`.

| Table / view | Use it for |
|---|---|
| `mart_savings_opportunities` | "Where to negotiate first" table |
| `mart_site_prices` | Internal price variance by site |
| `mart_vendor_prices` | Vendor scorecard, premium over the group's best |
| `mart_price_trend_monthly` | Line charts by month, vendor, site |
| `mart_vendor_spend` | Share of wallet |
| `mart_alerts` | Alert feed, filter by `severity` |
| `v_monthly_spend`, `v_spend_by_group` | Spend roll-ups |
| `price_history` | Drill-down to every row |
| `meta` | When it was built, and `is_sample` |

Rebuild after every new export (a cron job or a scheduled task). The build writes
to a temporary file and swaps it in, so dashboards never read a half-built file.

## Metabase

`deploy/docker-compose.yml` runs the official Metabase image next to the
warehouse. After `docker compose up`, open http://localhost:3000, finish setup,
then **Admin → Databases → Add database → SQLite**, with the file path
`/warehouse/warehouse.db`.

Suggested first dashboard: `mart_savings_opportunities` as a table sorted by
`total_opportunity`, `mart_vendor_spend` as a bar chart, and
`mart_price_trend_monthly` as a line chart with a SKU filter. Metabase's
dashboard subscriptions can then email the alert feed (`mart_alerts` where
`severity = 'high'`) to category managers every Monday.

## Apache Superset

Superset blocks SQLite by default. In `superset_config.py`:

```python
PREVENT_UNSAFE_DB_CONNECTIONS = False
```

Then **Settings → Database Connections → + Database → SQLite**, with the URI:

```
sqlite:////absolute/path/to/data/warehouse.db
```

Mount the file read-only into the Superset container. For production, copy the
same tables into Postgres (for example with `pgloader warehouse.db postgresql://...`)
and point Superset there.
