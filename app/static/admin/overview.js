/* Admin analytics dashboard — Overview tab.
   Loaded as a classic script by index.html: core, charts, blocks, drill, the tab files, main.
   Top-level const/function names are shared across these files, so names must be unique.
   File map and conventions: app/analytics/CLAUDE.md. */

/* ── overview ──────────────────────────────────────────────────────── */
function funnelBody(d) {
  const f = d.funnel || {};
  const steps = [
    ['Signed up', f.step_signed_up, 'step_signed_up'],
    ['Added a property', f.step_property, 'step_property'],
    ['Added a renter', f.step_renter, 'step_renter'],
    ['Recorded a transaction', f.step_transaction, 'step_transaction'],
    ['Exported a report', f.step_export, 'step_export'],
  ];
  const top = steps[0][1] || 0;
  if (!top) return '<div class="empty">No owners signed up in this range yet, so there is no funnel to draw.</div>';
  return '<div class="funnel">' + steps.map(([label, v, key], i) => {
    const value = v || 0;
    const w = (value / top) * 100;
    // Below ~14% the value would be clipped by its own bar, so it sits outside instead.
    const inside = w >= 14;
    const bar = '<div class="funnel-bar' + (value === 0 ? ' zero' : '') + '" style="width:' + w.toFixed(1) + '%">' +
      (inside ? num(value) : '') + '</div>' +
      (inside ? '' : '<div class="funnel-outside" style="left:' + w.toFixed(1) + '%">' + num(value) + '</div>');
    const prev = i > 0 ? (steps[i - 1][1] || 0) : null;
    const drop = (prev && prev > 0) ? (100 * (prev - value) / prev) : null;
    const dropLine = drop === null ? '' :
      '<div class="funnel-drop' + (drop >= 50 ? ' bad' : '') + '">↓ ' + drop.toFixed(0) +
      '% drop-off (' + num(prev - value) + ' owners)</div>';
    return '<div' + drillAttrs({ t: 'Activation funnel · ' + label, s: 'funnel', v: key }, 'funnel-step') +
      '><div class="funnel-label">' + esc(label) +
      '</div><div class="funnel-track">' + bar + '</div></div>' + dropLine;
  }).join('') + '</div>';
}

function overviewHTML(d) {
  const g = d.window.granularity;
  const t = d.growth.totals || {};
  const subs = subscriptionSummary(d);
  const active = fillBuckets(d, d.engagement.active_owners, ['active_owners']);
  // The last bucket is the current, unfinished one — a Monday-morning number would read as
  // a collapse every week. The one before it is the latest complete period.
  const lastFull = active.length >= 2 ? active[active.length - 2].active_owners : null;
  const scans = (d.ai.scans || []).reduce((s, r) => s + (r.scans || 0), 0);
  const saved = (d.ai.scans || []).reduce((s, r) => s + (r.saved || 0), 0);
  const messages = (d.ai.assistant || []).reduce((s, r) => s + (r.messages || 0), 0);

  return section('', '<div class="grid kpis">' +
      kpi('Paying subscribers', num(subs.paying),
          subs.trial ? '+' + num(subs.trial) + ' on trial' : 'right now', null,
          'Subscriptions that confer a paid plan right now, trials excluded. Grandfathered accounts are not counted.',
          { t: 'Paying subscribers', s: 'subs_paying' }) +
      kpi('Live owners', num(t.live_owners), '+' + num(t.new_in_window) + ' in this range', null,
          'Accounts that exist right now. Deleting an account removes its row.',
          { t: 'Live owners', s: 'all_owners' }) +
      kpi('New signups', num(t.new_in_window), 'in this range', 'k-signups', null,
          { t: 'New signups', s: 'signups' }) +
      kpi('Active owners', num(lastFull), g === 'week' ? 'last full week' : 'yesterday', 'k-active',
          'Distinct owners who wrote something — a property, renter, transaction, export, scan or assistant message.',
          lastFull === null ? null : { t: 'Active owners · ' + (g === 'week' ? 'last full week' : 'yesterday'),
                                       s: 'active', b: active[active.length - 2].bucket }) +
      kpi('Lease scans', num(scans), scans ? pct(100 * saved / scans) + ' saved' : 'in this range', 'k-scans', null,
          { t: 'Owners who scanned a lease', s: 'scans' }) +
      kpi('Assistant messages', num(messages), 'in this range', 'k-agent', null,
          { t: 'Owners who used the assistant', s: 'assistant' }) +
    '</div>') +
    section('Activation funnel', card('Signup → first report export',
      'Each step requires every step before it, so the funnel can only go down. Cohort starts ' +
      d.window.cohort_floor + '.', funnelBody(d))) +
    notesFor(d, ['funnel']);
}
function drawOverview(d) {
  sparkline('k-signups', fillBuckets(d, d.growth.signups, ['signups']).map((r) => r.signups));
  sparkline('k-active', fillBuckets(d, d.engagement.active_owners, ['active_owners']).map((r) => r.active_owners));
  sparkline('k-scans', fillBuckets(d, d.ai.scans, ['scans']).map((r) => r.scans));
  sparkline('k-agent', fillBuckets(d, d.ai.assistant, ['messages']).map((r) => r.messages));
}

TAB_RENDER.overview = [overviewHTML, drawOverview];
