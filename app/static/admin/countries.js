/* Admin analytics dashboard — Countries tab.
   Loaded as a classic script by index.html: core, charts, blocks, drill, the tab files, main.
   Top-level const/function names are shared across these files, so names must be unique.
   File map and conventions: app/analytics/CLAUDE.md. */

/* ── countries ─────────────────────────────────────────────────────── */
const COUNTRY_METRICS = {
  owners: 'Owners', signups: 'Signups', active_owners: 'Active', properties: 'Properties',
  scans: 'Scans', messages: 'Assistant', paying: 'Paying',
};
/** The countries worth a line of their own; everything else is folded into "Other". */
const TREND_COUNTRIES = 4;
const TREND_COLORS = ['--series-1', '--series-2', '--series-3', '--series-4'];

function countriesHTML(d) {
  const c = d.countries || {};
  const rows = c.comparison || [];
  const m = state.countryMetric;
  const ranked = rows.slice().sort((a, b) => (b[m] || 0) - (a[m] || 0));
  const metricTitle = (country) => COUNTRY_METRICS[m] + ' · ' + country;
  const items = ranked.slice(0, 10).map((r) => ({ label: countryName(r.country), value: r[m] || 0,
    drill: countryDrill(metricTitle(countryName(r.country)), m, r.country) }));
  if (ranked.length > 10) {
    items.push({ label: 'Other (' + (ranked.length - 10) + ')',
                 value: ranked.slice(10).reduce((s, r) => s + (r[m] || 0), 0), color: '--series-muted',
                 drill: countryDrill(metricTitle('other countries'), m, null,
                                     ranked.slice(0, 10).map((r) => r.country)) });
  }
  const rate = (a, b) => b ? pct(100 * a / b) : '—';
  const table = rows.length
    ? '<div class="scroll-x"><table class="data"><thead><tr><th>Country</th>' +
      '<th class="num">Owners</th><th class="num">Signups</th><th class="num">Active</th>' +
      '<th class="num">% active</th><th class="num">Properties / owner</th>' +
      '<th class="num">Scans</th><th class="num">Assistant</th><th class="num">Paying</th>' +
      '<th class="num">% paying</th></tr></thead><tbody>' + rows.map((r) => {
        const name = countryName(r.country);
        // Counts open their owners; ratios do not, they are not a group of people.
        const cell = (metric, content) => drillCell(content,
          countryDrill(COUNTRY_METRICS[metric] + ' · ' + name, metric, r.country), 'num');
        return '<tr><td>' + esc(name) + '</td>' +
        cell('owners', num(r.owners)) +
        cell('signups', num(r.signups)) +
        cell('active_owners', num(r.active_owners)) +
        '<td class="num">' + rate(r.active_owners, r.owners) + '</td>' +
        '<td class="num">' + (r.owners ? (r.properties / r.owners).toFixed(1) : '—') + '</td>' +
        cell('scans', num(r.scans)) +
        cell('messages', num(r.messages)) +
        cell('paying', num(r.paying)) +
        '<td class="num">' + rate(r.paying, r.owners) + '</td></tr>';
      }).join('') +
      '</tbody></table></div>'
    : '<div class="empty">No owners yet</div>';

  return section('', '<div class="card"><div class="card-head"><h3>Top countries by ' +
        esc(COUNTRY_METRICS[m].toLowerCase()) +
        info('Owners, properties and paying are totals right now; the rest count this range only.') +
        '</h3><div class="scroll-x">' + switcher('country-metric', COUNTRY_METRICS, m, 'Metric') +
        '</div></div>' + hbars(items, 'Nothing recorded yet') + '</div>') +
    section('Side by side', card('Every country',
        'The % columns are what make a small country comparable to a big one.', table)) +
    section('Over time', '<div class="grid charts">' +
        canvasCard('Signups', 'The ' + TREND_COUNTRIES + ' largest countries, the rest as Other.', 'c-country-signups') +
        canvasCard('Active owners', 'Distinct owners who wrote something, per country.', 'c-country-active') +
      '</div>') +
    section('Where users are', '<div class="grid charts">' +
        canvasCard('Owners by country', 'Every live account, by the country chosen at signup. ' +
          'The ' + PIE_COUNTRIES + ' largest get a slice each, the rest share Other.', 'c-country-pie') +
      '</div>') +
    notesFor(d, ['countries']);
}
/** A country's owners for one metric of the comparison. `exclude` lists the countries
 *  drawn on their own, which is how "Other" is asked for. */
const countryDrill = (title, metric, country, exclude) => ({
  t: title, s: 'country', g: metric, v: country || undefined,
  x: exclude && exclude.length ? exclude.join(',') : undefined,
});
/** The largest countries by live owners, in order. */
function topCountries(d, n) {
  return ((d.countries || {}).comparison || []).slice()
    .sort((a, b) => (b.owners || 0) - (a.owners || 0)).slice(0, n).map((r) => r.country);
}
const PIE_COUNTRIES = 4;

/** Lines for the largest countries plus one "Other", on the full run of buckets. */
function countryTrend(d, g, canvasId, rows, field, emptyText) {
  const top = topCountries(d, TREND_COUNTRIES);
  const buckets = rows.length ? bucketKeys(d, rows) : [];
  const series = top.map((country, i) => ({ name: countryName(country), color: TREND_COLORS[i],
                                             match: (r) => r.country === country, country }));
  if (rows.some((r) => !top.includes(r.country))) {
    series.push({ name: 'Other', color: '--series-muted', match: (r) => !top.includes(r.country) });
  }
  const metricName = field === 'signups' ? 'Signups' : 'Active owners';
  chartOrEmpty(canvasId, rows, () => clickable({
    type: 'line',
    data: {
      labels: buckets.map((b) => bucketLabel(new Date(b).toISOString(), g)),
      datasets: series.map((s) => lineSeries(s.name, buckets.map((b) => rows
        .filter((r) => parseUtc(r.bucket).getTime() === b && s.match(r))
        .reduce((n, r) => n + (r[field] || 0), 0)), s.color)),
    },
    options: baseOptions(),
  }, (ds, i) => {
    const b = new Date(buckets[i]).toISOString();
    const sr = series[ds];
    return Object.assign(countryDrill(metricName + ' · ' + sr.name + ' · ' + periodLabel(b, g), field,
                                      sr.country, sr.country ? null : top), { b });
  }), emptyText);
}
function drawCountries(d, g) {
  const c = d.countries || {};
  countryTrend(d, g, 'c-country-signups', c.signups || [], 'signups', 'No signups in this range yet');
  countryTrend(d, g, 'c-country-active', c.active || [], 'active_owners', 'No write activity in this range yet');

  // Owners per country as a pie: the largest few, then everything else as one grey slice.
  const ranked = (c.comparison || []).filter((r) => r.owners > 0).slice()
    .sort((a, b) => b.owners - a.owners);
  const top = ranked.slice(0, PIE_COUNTRIES);
  const rest = ranked.slice(PIE_COUNTRIES);
  const slices = top.map((r, i) => ({ label: countryName(r.country), value: r.owners, color: TREND_COLORS[i],
                                      drill: countryDrill('Owners · ' + countryName(r.country), 'owners', r.country) }));
  if (rest.length) {
    slices.push({ label: 'Other (' + rest.length + ')', value: rest.reduce((n, r) => n + r.owners, 0),
                  color: '--series-muted',
                  drill: countryDrill('Owners · other countries', 'owners', null, top.map((r) => r.country)) });
  }
  const total = slices.reduce((n, x) => n + x.value, 0);
  chartOrEmpty('c-country-pie', slices, () => clickable({
    type: 'pie',
    data: {
      // Count and share in the legend itself, so no slice is readable only by hovering.
      labels: slices.map((x) => x.label + ' · ' + num(x.value) + ' (' + Math.round(100 * x.value / total) + '%)'),
      datasets: [{
        data: slices.map((x) => x.value),
        backgroundColor: slices.map((x) => css(x.color)),
        borderColor: css('--surface'), borderWidth: 2,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: 'right', labels: Object.assign({}, baseOptions().plugins.legend.labels, { font: { size: 12 } }) },
        tooltip: Object.assign({}, baseOptions().plugins.tooltip, {
          callbacks: { label: (ctx) => ' ' + num(ctx.parsed) + ' owners' },
        }),
      },
    },
  }, (_ds, i) => slices[i].drill), 'No owners yet');
  $('#country-metric').addEventListener('click', (e) => {
    const btn = e.target.closest('button'); if (!btn) return;
    state.countryMetric = btn.dataset.v;
    renderTab();
  });
}

TAB_RENDER.countries = [countriesHTML, drawCountries];
