/* Admin analytics dashboard — AI tab.
   Loaded as a classic script by index.html: core, charts, blocks, drill, the tab files, main.
   Top-level const/function names are shared across these files, so names must be unique.
   File map and conventions: app/analytics/CLAUDE.md. */

/* ── AI ────────────────────────────────────────────────────────────── */
function aiHTML(d) {
  const acc = d.ai.accuracy || {}, cap = d.ai.cap_hits || {}, lim = d.ai.limits || {};
  const scans = d.ai.scans || [];
  const totalScans = scans.reduce((s, r) => s + (r.scans || 0), 0);
  const saved = scans.reduce((s, r) => s + (r.saved || 0), 0);

  const cf = d.ai.corrected_fields || [];
  const fields = cf.length
    ? '<div class="scroll-x"><table class="data"><thead><tr><th>Section</th><th>Field</th>' +
      '<th class="num">Corrections</th><th class="num">Scans</th></tr></thead><tbody>' +
      cf.slice(0, 15).map((r) => '<tr' + drillAttrs({ t: 'Corrected "' + r.field + '"', s: 'corrected_field',
                                                         v: r.field, g: r.section }) +
        '><td>' + esc(r.section) + '</td><td>' + esc(r.field) +
        '</td><td class="num">' + num(r.corrections) + '</td><td class="num">' +
        num(r.scans_affected) + '</td></tr>').join('') + '</tbody></table></div>'
    : '<div class="empty">No corrections recorded yet</div>';

  return section('Lease scanning', '<div class="grid tiles">' +
        tile('Scans', num(totalScans), 'in this range', false, null,
             { t: 'Owners who scanned a lease', s: 'scans' }) +
        tile('Saved', totalScans ? pct(100 * saved / totalScans) : '—', 'the rest were abandoned', false,
             'Abandonment is an upper bound — a save whose callback never arrived reads as abandoned.',
             { t: 'Owners who saved a scanned lease', s: 'scans', v: 'saved' }) +
        tile('Corrected fields / scan', num(acc.avg_changed_per_scan),
             acc.pct_fields_corrected != null ? pct(acc.pct_fields_corrected) + ' of prefilled fields' : '') +
      '</div><div class="grid charts">' +
        canvasCard('Saved vs abandoned', '', 'c-scans') +
        card('Most-corrected fields',
             'Field names only — the corrected values are lease content and are never queried.', fields) +
      '</div>') +
    section('Assistant', '<div class="grid tiles">' +
        tile('Messages', num((d.ai.assistant || []).reduce((s, r) => s + (r.messages || 0), 0)), 'in this range',
             false, null, { t: 'Owners who used the assistant', s: 'assistant' }) +
        tile('Owners at daily cap', num(cap.owners_at_cap),
             'reached ' + num(lim.daily_message_limit) + ' messages in a day', false,
             'An estimate: counts owners who reached the limit, not owners who were turned away.',
             { t: 'Owners who reached the daily assistant cap', s: 'assistant_cap' }) +
      '</div><div class="grid charts">' +
        canvasCard('Messages', 'One per user message', 'c-agent') +
      '</div>') +
    notesFor(d, ['ai']);
}
function drawAI(d, g) {
  const s = fillBuckets(d, d.ai.scans, ['scans', 'saved']);
  const outcomes = [['saved', 'Saved'], ['abandoned', 'Abandoned']];
  chartOrEmpty('c-scans', s, () => clickable({
    type: 'bar',
    data: { labels: s.map((r) => bucketLabel(r.bucket, g)), datasets: [
      barSeries('Saved', s.map((r) => r.saved), '--series-1'),
      barSeries('Abandoned', s.map((r) => (r.scans || 0) - (r.saved || 0)), '--series-2'),
    ] },
    options: baseOptions({
      scales: Object.assign(baseOptions().scales, {
        x: Object.assign({}, baseOptions().scales.x, { stacked: true }),
        y: Object.assign({}, baseOptions().scales.y, { stacked: true }),
      }),
    }),
  }, (ds, i) => ({ t: outcomes[ds][1] + ' lease scans · ' + periodLabel(s[i].bucket, g),
                   s: 'scans', v: outcomes[ds][0], b: s[i].bucket })), 'No lease scans in this range yet');

  const a = fillBuckets(d, d.ai.assistant, ['messages']);
  chartOrEmpty('c-agent', a, () => clickable({
    type: 'line',
    data: { labels: a.map((r) => bucketLabel(r.bucket, g)),
            datasets: [lineSeries('Messages', a.map((r) => r.messages), '--series-1')] },
    options: singleSeries(),
  }, (_ds, i) => ({ t: 'Assistant users · ' + periodLabel(a[i].bucket, g), s: 'assistant', b: a[i].bucket })),
  'No assistant usage in this range yet');
}

TAB_RENDER.ai = [aiHTML, drawAI];
