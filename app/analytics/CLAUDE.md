# Admin analytics dashboard — working guide

Internal product-metrics page at `/admin/dashboard`. The why (access control, caveats, what each
number means) is in `../../README.md` → *Admin analytics dashboard* and `../../../PLATFORM.md` §16.
This file is the map: **read only the files a change needs.**

## Where things live

| Layer | File | Holds |
|---|---|---|
| Aggregate SQL | `app/analytics/queries.py` | One UPPER_CASE constant per chart. **Counts only, no identity.** Search for the constant; don't read the whole file |
| Owner-list SQL | `app/analytics/drilldown.py` | `SEGMENTS`: one `seg` CTE per clickable group, wrapped in one shared SELECT. **The only place that returns owner name/email** |
| Payload | `app/analytics/service.py` | `build_payload` (key → query), `get_owner_list`, `_caveats` |
| Routes | `app/api/routers/admin.py` | `/dashboard`, `/assets/{name}` (allowlisted), `/analytics`, `/analytics/owners` |
| Page shell | `app/static/admin/index.html` | Markup, script order. No data |
| Styles | `app/static/admin/dashboard.css` | |
| Shared JS | `core.js` | state, formatting, time buckets, auth, fetch, `PLAN_LABELS`, `countryName`, tab switching |
| | `charts.js` | `baseOptions`, `singleSeries`, `lineSeries`, `barSeries`, `chartOrEmpty`, `sparkline` |
| | `blocks.js` | `tile`, `kpi`, `card`, `canvasCard`, `section`, `hbars`, `switcher`, `notesFor`, `sumBy` |
| | `drill.js` | the owner-list popup: `drillAttrs`, `drillCell`, `clickable`, `openDrill` |
| One file per tab | `overview.js` `subscriptions.js` `growth.js` `countries.js` `ai.js` `platform.js` `health.js` | `<tab>HTML(d)` returns markup, `draw<Tab>(d, g)` draws charts, last line registers `TAB_RENDER.<tab>` |
| Startup | `main.js` | control listeners, then `initAuth()`. Loads last |
| Tests | `tests/test_admin_analytics.py` | access control, binds, PII greps, asset allowlist |

Tab → payload key: overview `funnel`, `growth.totals` · subscriptions `subscriptions.*`,
`engagement.distribution` · growth `growth.*`, `engagement.*` · countries `countries.*` ·
ai `ai.*` · platform `platform.*` · health `operations.*`.

## Rules that bite

- **Plain scripts, no bundler.** Every file shares one global scope: top-level names must be
  unique across files, and a top-level statement may only use names from files loaded before it
  (order is in `index.html`). Functions called later, at render time, can live anywhere.
- New asset file → add its `<script>`/`<link>` to `index.html`; the route serves any `.js`/`.css`
  in the folder, nothing else.
- `queries.py` never selects a name, email, phone or address — a test greps it. Identity goes in
  `drilldown.py`, owners only, never a renter column.
- Times are naive UTC. Buckets come back as ISO strings; parse with `parseUtc`, fill gaps with
  `fillBuckets` so empty weeks draw as 0.
- Charts go through `chartOrEmpty`, so no data reads as "no data yet", not as zero.

## Recipes

**Add a chart:** query in `queries.py` (take `:since`/`:until`, join `scoped_owners`) → key in
`build_payload` → card in the tab's `<tab>HTML` → draw it in `draw<Tab>`.

**Make it clickable:** add a segment to `drilldown.SEGMENTS` that restates the chart's query as
"which owner ids", using `:lo`/`:hi` for the clicked time slice. Then hand the page a descriptor
`{ t: title, s: segment, v: value, g: group, b: bucket, x: excluded countries }`:
`hbars` item `.drill`, last arg of `tile`/`kpi`, `drillCell(content, desc, cls)` for a table cell,
or wrap a Chart.js config: `clickable(config, (datasetIndex, index) => desc)`.

## Checking a change

- Always: `python -m pytest tests/test_admin_analytics.py -q --no-cov` (seconds; SQLite, so it
  cannot run the SQL).
- SQL or owner-list changes: `python ops/analytics_check.py verify` — seeds a scratch local
  Postgres and checks every owner list against its chart's number, then drops it.
- Visual check, only when asked: `python ops/analytics_check.py preview` serves the real page with
  sign-in stubbed at `http://127.0.0.1:8765/admin/dashboard`.
