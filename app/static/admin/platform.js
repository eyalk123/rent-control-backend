/* Admin analytics dashboard — Platform tab.
   Loaded as a classic script by index.html: core, charts, blocks, drill, the tab files, main.
   Top-level const/function names are shared across these files, so names must be unique.
   File map and conventions: app/analytics/CLAUDE.md. */

/* ── platform ──────────────────────────────────────────────────────── */
// One card with a switch instead of five near-identical tables. Bars with a share are easier
// to compare at a glance than a column of raw counts.
// Countries have their own tab.
const BREAKDOWNS = {
  language:         { label: 'Language', key: 'locale', value: 'owners', unit: 'owners',
                      note: 'Which of the two legal texts they accepted — covers every owner.' },
  signup_platform:  { label: 'Signup platform', key: 'platform', value: 'owners', unit: 'owners',
                      note: 'From the mandatory consent gate, so it covers every owner.' },
  devices:          { label: 'Push devices', key: 'platform', value: 'owners', unit: 'owners',
                      note: 'Owners with a registered push token, by device platform.' },
};
function breakdownBody(d) {
  const b = BREAKDOWNS[state.breakdown];
  return hbars((d.platform[state.breakdown] || [])
    .map((r) => ({ label: r[b.key], value: r[b.value] || 0,
                   drill: { t: b.label + ' · ' + (r[b.key] || 'not recorded'), s: state.breakdown,
                            v: r[b.key] == null ? undefined : r[b.key] } }))
    .sort((x, y) => y.value - x.value));
}
// Builds that predate the X-Client-* headers (2026-09-18) are stored as 'unknown'. Named for
// what they almost certainly are, so the biggest bar does not read as a data bug.
const APP_LABELS = { mobile: 'Mobile', web: 'Web', unknown: 'Not reported (older app build)' };
function platformHTML(d) {
  const p = d.platform;
  const writesByApp = sumBy(p.client_usage || [], 'app', 'writes');
  const allWrites = Object.values(writesByApp).reduce((s, v) => s + v, 0);
  const reported = allWrites ? pct(100 * (allWrites - (writesByApp.unknown || 0)) / allWrites) : null;
  const overlap = (p.client_overlap || []).filter((r) => r.segment !== 'unknown');
  const overlapBody = overlap.length
    ? '<table class="data"><thead><tr><th>Segment</th><th class="num">Owners</th>' +
      '<th class="num">Writes</th></tr></thead><tbody>' + overlap.map((r) =>
        '<tr' + drillAttrs({ t: 'Owners on ' + r.segment, s: 'client_overlap', v: r.segment }) +
        '><td>' + esc(r.segment) + '</td><td class="num">' + num(r.owners) +
        '</td><td class="num">' + num(r.writes) + '</td></tr>').join('') + '</tbody></table>'
    : '<div class="empty">No client usage recorded yet.</div>';

  const b = BREAKDOWNS[state.breakdown];
  const labels = Object.fromEntries(Object.entries(BREAKDOWNS).map(([k, v]) => [k, v.label]));

  return section('Where owners work', '<div class="grid charts">' +
        card('Writes by client',
          'A glance and an evening of work are not the same, so this counts writes, not sessions. ' +
          'Builds from before 2026-09-18 do not say which app they are and show as not reported.',
          '<div class="chart-wrap"><canvas id="c-client"></canvas></div>' +
          (reported ? '<div class="caption" style="font-size:12px;color:var(--muted);margin-top:8px">' +
            reported + ' of writes in this range came from a build that reports its app.</div>' : '')) +
        card('Mobile / web / both', 'Owners can appear in both. Owners seen only on unreported builds are left out.', overlapBody) +
      '</div>') +
    section('Who they are', '<div class="card"><div class="card-head"><h3>' + esc(b.label) +
        ' <span style="font-weight:400;color:var(--muted)">· ' + esc(b.unit) + '</span>' + info(b.note) +
        '</h3><div class="scroll-x">' + switcher('breakdown-seg', labels, state.breakdown, 'Breakdown') + '</div></div>' +
        '<div id="breakdown-body">' + breakdownBody(d) + '</div></div>') +
    notesFor(d, ['platform']);
}
function drawPlatform(d, g) {
  const rows = d.platform.client_over_time || [];
  const order = ['mobile', 'web', 'unknown'];
  const apps = [...new Set(rows.map((r) => r.app))]
    .sort((a, b) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99));
  // Rows are one per (bucket, app), so the full bucket run is built here rather than by
  // fillBuckets; a bucket with no row for an app is already drawn as 0 below.
  const buckets = rows.length ? bucketKeys(d, rows) : [];
  // Colour follows the entity, not its position in the array. If 'unknown' stops appearing,
  // mobile and web must keep the colours they had — a series that changes colour when the
  // data changes is unreadable across two screenshots.
  const appColor = { mobile: '--series-1', web: '--series-2' };
  chartOrEmpty('c-client', rows, () => clickable({
    type: 'bar',
    data: {
      labels: buckets.map((b) => bucketLabel(new Date(b).toISOString(), g)),
      datasets: apps.map((app) => barSeries(APP_LABELS[app] || app,
        buckets.map((b) => {
          const hit = rows.find((r) => parseUtc(r.bucket).getTime() === b && r.app === app);
          return hit ? (hit.writes || 0) : 0;
        }), appColor[app] || '--series-muted')),
    },
    options: baseOptions(),
  }, (ds, i) => {
    const b = new Date(buckets[i]).toISOString();
    return { t: 'Wrote from ' + (APP_LABELS[apps[ds]] || apps[ds]) + ' · ' + periodLabel(b, g),
             s: 'client_app', v: apps[ds], b };
  }), 'No client usage recorded yet.');

  $('#breakdown-seg').addEventListener('click', (e) => {
    const btn = e.target.closest('button'); if (!btn) return;
    state.breakdown = btn.dataset.v;
    renderTab();
  });
}

TAB_RENDER.platform = [platformHTML, drawPlatform];
