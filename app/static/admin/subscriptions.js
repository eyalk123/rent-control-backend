/* Admin analytics dashboard — Subscriptions tab.
   Loaded as a classic script by index.html: core, charts, blocks, drill, the tab files, main.
   Top-level const/function names are shared across these files, so names must be unique.
   File map and conventions: app/analytics/CLAUDE.md. */

/* ── subscriptions ─────────────────────────────────────────────────── */
// Built before billing goes live, so every panel must read sensibly at zero and fill in by
// itself once the first webhook lands — no code change on launch day.
const PAYING_STATES = ['active', 'canceling', 'payment_failed'];
const SOURCE_LABELS = { apple: 'App Store', google: 'Google Play', paddle: 'Web (Paddle)' };
const STATE_LABELS = {
  active: 'Active', trial: 'Trial', canceling: 'Canceled, paid to period end',
  payment_failed: 'Payment failed, in grace', ended: 'Ended',
};
function money(amount, currency) {
  try {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency, maximumFractionDigits: 0 }).format(amount);
  } catch (_) {
    return nf.format(Math.round(amount)) + ' ' + currency;
  }
}
function subscriptionSummary(d) {
  const rows = (d.subscriptions && d.subscriptions.state) || [];
  const live = rows.filter((r) => PAYING_STATES.includes(r.state));
  const byState = sumBy(rows, 'state', 'subscriptions');
  // Per currency, never across them: there is no honest single total of shekels and dollars.
  const mrr = {};
  live.forEach((r) => {
    if (r.currency && r.monthly_amount != null) mrr[r.currency] = (mrr[r.currency] || 0) + r.monthly_amount;
  });
  return {
    rows, live, byState, mrr,
    paying: live.reduce((s, r) => s + (r.subscriptions || 0), 0),
    trial: byState.trial || 0,
  };
}
/** `drill(key, label)`, when given, makes each bar open its owners. */
const labelled = (counts, labels, drill) => Object.entries(counts)
  .map(([k, v]) => ({ label: labels[k] || k, value: v, drill: drill ? drill(k, labels[k] || k) : null }))
  .sort((a, b) => b.value - a.value);

function subscriptionsHTML(d) {
  const sub = d.subscriptions || {};
  const s = subscriptionSummary(d);
  const acc = sub.accounts || {}, wh = sub.webhook || {};
  const mrr = Object.entries(s.mrr).sort((a, b) => b[1] - a[1]);
  const banner = (!s.rows.length && !wh.events)
    ? '<div class="banner warn"><strong>Billing is not live yet</strong>No subscription has been ' +
      'recorded. Every panel here fills in by itself once the first RevenueCat webhook lands.</div>'
    : '';

  const currencyTable = mrr.length
    ? '<table class="data"><thead><tr><th>Currency</th><th class="num">Subscribers</th>' +
      '<th class="num">Per month</th></tr></thead><tbody>' + mrr.map(([c, v]) =>
        '<tr><td>' + esc(c) + '</td>' +
        drillCell(num(s.live.filter((r) => r.currency === c).reduce((n, r) => n + r.subscriptions, 0)),
                  { t: 'Paying subscribers · ' + c, s: 'subs_paying', g: 'currency', v: c }, 'num') +
        '<td class="num">' + esc(money(v, c)) + '</td></tr>').join('') + '</tbody></table>'
    : '<div class="empty">No paying subscribers yet</div>';

  const whRow = (label, value, bad) => '<tr><td>' + esc(label) + '</td><td class="num">' +
    (bad ? '<span class="pill bad">' + num(value) + '</span>' : num(value)) + '</td></tr>';
  const webhook = '<table class="data"><tbody>' +
    whRow('Events in this range', wh.events) +
    whRow('Applied', wh.applied) +
    whRow('Unrecognised product', wh.unmapped, (wh.unmapped || 0) > 0) +
    whRow('Arrived out of order', wh.stale) +
    whRow('Sandbox (store testing)', wh.sandbox) +
    '<tr><td>Last received</td><td class="num">' +
      (wh.last_received ? parseUtc(wh.last_received).toLocaleString() : 'never') + '</td></tr>' +
    '</tbody></table>';

  return banner +
    section('', '<div class="grid kpis four">' +
      kpi('Paying subscribers', num(s.paying), s.trial ? '+' + num(s.trial) + ' on trial' : 'right now', null,
          'Includes canceled subscriptions still inside the period they paid for, and failed payments still in grace.',
          { t: 'Paying subscribers', s: 'subs_paying' }) +
      kpi('Monthly revenue', mrr.length ? money(mrr[0][1], mrr[0][0]) : '—',
          mrr.length > 1 ? '+ ' + mrr.slice(1).map(([c, v]) => money(v, c)).join(' · ') : 'store prices, per month', null,
          'Yearly plans divided by 12. One figure per currency — they are never added together.',
          { t: 'Paying subscribers', s: 'subs_paying' }) +
      kpi('Free accounts at the limit', num(acc.free_at_limit), 'of ' + num(acc.free) + ' free accounts', null,
          'Free accounts with 2 properties, the free plan\'s ceiling. Their next property needs a paid plan.',
          { t: 'Free accounts at the limit', s: 'account_plan', v: 'free_at_limit' }) +
      kpi('Grandfathered', num(acc.grandfathered), 'top plan for free, for good', null,
          'Accounts from before billing keep the top plan permanently (owners.granted_plan). Not counted as paying.',
          { t: 'Grandfathered accounts', s: 'account_plan', v: 'grandfathered' }) +
    '</div>') +
    section('Accounts', '<div class="grid charts">' +
      card('Every account by plan', 'Each live account counted once.', hbars([
        { label: 'Paid plan (incl. trial)', value: acc.on_paid_plan,
          drill: { t: 'Accounts on a paid plan (incl. trial)', s: 'account_plan', v: 'paid' } },
        { label: 'Grandfathered', value: acc.grandfathered,
          drill: { t: 'Grandfathered accounts', s: 'account_plan', v: 'grandfathered' } },
        { label: 'Free', value: acc.free, drill: { t: 'Free accounts', s: 'account_plan', v: 'free' } },
      ], 'No accounts yet')) +
      canvasCard('Owners by number of properties',
        'Grouped by the plans\' property bands, so each bar is where those owners would land on pricing.', 'c-dist') +
    '</div>') +
    section('Subscribers', '<div class="grid charts four">' +
      card('By plan', 'Paying subscribers only.', hbars(labelled(sumBy(s.live, 'plan', 'subscriptions'), PLAN_LABELS,
           (k, l) => ({ t: 'Paying subscribers · ' + l, s: 'subs_paying', g: 'plan', v: k })), 'No paying subscribers yet')) +
      card('By store', 'Where they bought. App Store and Google Play subscriptions can only be canceled in the store.',
           hbars(labelled(sumBy(s.live, 'source', 'subscriptions'), SOURCE_LABELS,
           (k, l) => ({ t: 'Paying subscribers · ' + l, s: 'subs_paying', g: 'source', v: k })), 'No paying subscribers yet')) +
      card('By status', 'Every subscription ever recorded, including ended ones.',
           hbars(labelled(s.byState, STATE_LABELS,
           (k, l) => ({ t: 'Subscriptions · ' + l, s: 'subs_any', g: 'state', v: k })), 'No subscriptions yet')) +
      card('Revenue by currency', 'Store-reported prices, for orientation — not accounting.', currencyTable) +
    '</div>') +
    section('Over time', '<div class="grid charts">' +
      canvasCard('New vs canceled', 'From the webhook log, sandbox purchases excluded.', 'c-sub-events') +
      card('RevenueCat webhook', 'Is billing data arriving, and is it understood? An unrecognised product leaves a payer on the free plan.', webhook) +
    '</div>') +
    notesFor(d, ['subscriptions']);
}
function drawSubscriptions(d, g) {
  const dist = d.engagement.distribution;
  const bandLabel = { '0': '0', '1-2': '1–2 (free)', '3-8': '3–8', '9-15': '9–15', '16+': '16+' };
  const axis = (text) => ({ display: true, text, color: css('--muted'), font: { size: 11 } });
  chartOrEmpty('c-dist', dist && dist.properties.some(Boolean) ? dist.buckets : [], () => clickable({
    type: 'bar',
    data: { labels: dist.buckets.map((b) => bandLabel[b] || b),
            datasets: [barSeries('Owners', dist.properties, '--series-1')] },
    options: singleSeries({
      plugins: { legend: { display: false }, tooltip: baseOptions().plugins.tooltip, valueLabels: { enabled: true } },
      scales: {
        x: Object.assign({}, baseOptions().scales.x, { title: axis('Properties per owner') }),
        y: Object.assign({}, baseOptions().scales.y, { title: axis('Owners') }),
      },
    }),
  }, (_ds, i) => ({ t: 'Owners with ' + (bandLabel[dist.buckets[i]] || dist.buckets[i]) + ' properties',
                    s: 'property_band', v: dist.buckets[i] })), 'No owners yet');

  const e = fillBuckets(d, (d.subscriptions || {}).events, ['started', 'canceled']);
  const eventKinds = [['started', 'New'], ['canceled', 'Canceled']];
  chartOrEmpty('c-sub-events', e, () => clickable({
    type: 'bar',
    data: { labels: e.map((r) => bucketLabel(r.bucket, g)), datasets: [
      barSeries('New', e.map((r) => r.started), '--series-1'),
      barSeries('Canceled', e.map((r) => r.canceled), '--series-2'),
    ] },
    options: baseOptions(),
  }, (ds, i) => ({ t: eventKinds[ds][1] + ' subscriptions · ' + periodLabel(e[i].bucket, g),
                   s: 'subs_events', v: eventKinds[ds][0], b: e[i].bucket })), 'No subscription events yet');
}

TAB_RENDER.subscriptions = [subscriptionsHTML, drawSubscriptions];
