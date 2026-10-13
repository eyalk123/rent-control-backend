/* Admin analytics dashboard — Growth tab.
   Loaded as a classic script by index.html: core, charts, blocks, drill, the tab files, main.
   Top-level const/function names are shared across these files, so names must be unique.
   File map and conventions: app/analytics/CLAUDE.md. */

/* ── growth ────────────────────────────────────────────────────────── */
function retentionTable(d) {
  const r = d.engagement.retention || [];
  if (!r.length) return '<div class="empty">No cohorts with activity yet</div>';
  // Week N is days 7N..7N+7 after each owner's own signup, so a cohort can only be scored
  // once its last member (who signed up at the end of the cohort week) is past that. Before
  // then the query returns 0, which reads as "everyone left" instead of "too early to tell".
  const now = parseUtc(d.generated_at).getTime();
  const cohort = (x) => 'Cohort of ' + bucketLabel(x.cohort_week, 'week');
  const retained = (x, n, count) => {
    if (!x.cohort_size) return drillCell('—', null, 'num');
    const measured = parseUtc(x.cohort_week).getTime() + (n + 2) * 7 * DAY_MS;
    if (measured > now) {
      return drillCell('<span class="pill" title="Some owners in this cohort have not reached week ' + n +
        ' yet">pending</span>', null, 'num');
    }
    return drillCell(pct(100 * count / x.cohort_size),
      { t: cohort(x) + ' · active in week ' + n, s: 'retention', v: 'week_' + n, b: x.cohort_week }, 'num');
  };
  return '<div class="scroll-x"><table class="data"><thead><tr><th>Cohort week</th>' +
    '<th class="num">Size</th><th class="num">Week 1</th><th class="num">Week 4</th></tr></thead><tbody>' +
    r.map((x) =>
      '<tr><td>' + esc(bucketLabel(x.cohort_week, 'week')) + '</td>' +
      drillCell(num(x.cohort_size), { t: cohort(x), s: 'retention', b: x.cohort_week }, 'num') +
      retained(x, 1, x.retained_week_1) +
      retained(x, 4, x.retained_week_4) + '</tr>').join('') +
    '</tbody></table></div>';
}

function growthHTML(d) {
  const t = d.growth.totals || {};

  return section('Accounts', '<div class="grid tiles">' +
        tile('Live owners', num(t.live_owners), 'exist right now', false, null,
             { t: 'Live owners', s: 'all_owners' }) +
        tile('New', num(t.new_in_window), 'signed up in this range', false, null,
             { t: 'New signups', s: 'signups' }) +
        tile('Deleted', num(t.deleted_in_window), 'in this range', false,
             'Not filtered by country — the deletion record stores none.') +
        tile('Deleted all time', num(t.deleted_all_time), 'since 2026-07-31') +
      '</div><div class="grid charts">' +
        canvasCard('Signups', 'From owners.created_at', 'c-signups') +
        canvasCard('Active owners', 'Distinct owners who wrote something. Pure edits are not counted.', 'c-active') +
      '</div>') +
    section('Retention', '<div class="grid charts">' +
        card('By signup cohort', 'Cohort size shown so a small cohort is never read as a trend.',
             retentionTable(d)) +
      '</div>') +
    notesFor(d, ['funnel', 'engagement']);
}
function drawGrowth(d, g) {
  const s = fillBuckets(d, d.growth.signups, ['signups']);
  chartOrEmpty('c-signups', s, () => clickable({
    type: 'line',
    data: { labels: s.map((r) => bucketLabel(r.bucket, g)),
            datasets: [lineSeries('Signups', s.map((r) => r.signups), '--series-1')] },
    options: singleSeries(),
  }, (_ds, i) => ({ t: 'Signups · ' + periodLabel(s[i].bucket, g), s: 'signups', b: s[i].bucket })),
  'No signups in this range yet');

  const a = fillBuckets(d, d.engagement.active_owners, ['active_owners']);
  chartOrEmpty('c-active', a, () => clickable({
    type: 'line',
    data: { labels: a.map((r) => bucketLabel(r.bucket, g)),
            datasets: [lineSeries('Active owners', a.map((r) => r.active_owners), '--series-1')] },
    options: singleSeries(),
  }, (_ds, i) => ({ t: 'Active owners · ' + periodLabel(a[i].bucket, g), s: 'active', b: a[i].bucket })),
  'No write activity in this range yet');
}

TAB_RENDER.growth = [growthHTML, drawGrowth];
