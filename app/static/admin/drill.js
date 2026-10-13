/* Admin analytics dashboard — the owner-list popup behind every clickable number.
   Loaded as a classic script by index.html, in order: core.js, charts.js, blocks.js, drill.js,
   the tab files, main.js. Top-level names are shared across files, so they must be unique.
   File map and conventions: app/analytics/CLAUDE.md. */

/* ── click-through to the owner list ───────────────────────────────── */
// Every bar, point, slice, tile and cell that counts owners can open the same list. A mark
// carries a descriptor naming a segment in app/analytics/drilldown.py:
//   { t: title, s: segment, v: value, g: group, b: bucket start, x: excluded countries }
// The window and country always come from the payload on screen, so the list answers the
// question the chart was answering, even if the controls have moved since.

/** Attributes that make an element open the list. No descriptor, just the classes. */
function drillAttrs(desc, cls) {
  if (!desc) return cls ? ' class="' + cls + '"' : '';
  return ' class="' + (cls ? cls + ' ' : '') + 'drill" tabindex="0" role="button" data-drill="' +
    esc(JSON.stringify(desc)) + '"';
}
/** A <td> that opens the list, or a plain one when there is nothing to list. */
const drillCell = (content, desc, cls) => '<td' + (desc ? drillAttrs(desc, cls) : (cls ? ' class="' + cls + '"' : '')) +
  '>' + content + '</td>';

/** The mark under the pointer: the one hit directly, else the nearest — line charts draw no
 *  points, so demanding a direct hit would make them unclickable. Only inside the plot area,
 *  so a click on the legend toggles a series instead of also opening a list. A pie has no
 *  "nearest" worth guessing: there it is a direct hit or nothing. */
function pickElement(chart, evt) {
  const a = chart.chartArea;
  if (!a || evt.x < a.left || evt.x > a.right || evt.y < a.top || evt.y > a.bottom) return null;
  const hit = chart.getElementsAtEventForMode(evt, 'nearest', { intersect: true }, false);
  if (hit.length) return hit[0];
  if (chart.config.type === 'pie') return null;
  return chart.getElementsAtEventForMode(evt, 'nearest', { intersect: false }, false)[0] || null;
}
/** Makes a Chart.js config clickable. `resolve(datasetIndex, index)` returns a descriptor,
 *  or null for a mark with no owners behind it (an "Other" made of nothing, say). */
function clickable(config, resolve) {
  config.options = Object.assign({}, config.options, {
    onClick(evt, _els, chart) {
      const el = pickElement(chart, evt);
      const desc = el && resolve(el.datasetIndex, el.index);
      if (desc) openDrill(desc);
    },
    onHover(evt, _els, chart) {
      const el = pickElement(chart, evt);
      evt.native.target.style.cursor = (el && resolve(el.datasetIndex, el.index)) ? 'pointer' : '';
    },
  });
  return config;
}

const OWNER_PLAN_LABELS = { grandfathered: 'Grandfathered', free: 'Free' };
let drillSeq = 0;

async function openDrill(desc) {
  const d = state.data;
  if (!d) return;
  const seq = ++drillSeq;
  const w = d.window;
  const scope = (w.range === 'all' ? 'All time' : 'Last ' + w.range) +
    (desc.s === 'country' ? ' · every country' : w.country ? ' · ' + countryName(w.country) : '');
  $('#drill-title').textContent = desc.t || 'Owners';
  $('#drill-sub').textContent = scope + ' · loading…';
  $('#drill-body').innerHTML = '<div class="empty">Loading…</div>';
  if (!$('#drill').open) $('#drill').showModal();

  const qs = new URLSearchParams({ segment: desc.s, range: w.range, granularity: w.granularity });
  if (w.country) qs.set('country', w.country);
  if (desc.v != null) qs.set('value', desc.v);
  if (desc.g != null) qs.set('group', desc.g);
  if (desc.b != null) qs.set('bucket', desc.b);
  if (desc.x) qs.set('exclude', desc.x);

  let res, body;
  try {
    const user = firebase.auth().currentUser;
    if (!user) return location.reload();
    // Same rules as load(): a fresh token per request, and only ever in the header.
    const token = await user.getIdToken();
    res = await fetch('./analytics/owners?' + qs.toString(), { headers: { Authorization: 'Bearer ' + token } });
    body = await res.json().catch(() => ({}));
  } catch (err) {
    if (seq !== drillSeq) return;
    $('#drill-sub').textContent = scope;
    $('#drill-body').innerHTML = '<div class="banner error"><strong>Network error</strong>' + esc(String(err)) + '</div>';
    return;
  }
  if (seq !== drillSeq) return;
  if (!res.ok) {
    $('#drill-sub').textContent = scope;
    $('#drill-body').innerHTML = '<div class="banner error"><strong>HTTP ' + res.status + '</strong>' +
      esc(body.detail || res.statusText) + '</div>';
    return;
  }
  renderDrill(body, scope);
}

function renderDrill(body, scope) {
  const rows = body.owners || [];
  $('#drill-sub').textContent = scope + ' · ' + num(body.total) + ' owner' + (body.total === 1 ? '' : 's') +
    (body.total > rows.length ? ' · showing the newest ' + num(rows.length) : '');
  if (!rows.length) {
    $('#drill-body').innerHTML = '<div class="empty">No owners in this group.</div>';
    return;
  }
  const day = (iso) => iso ? parseUtc(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '—';
  const plan = (r) => (PLAN_LABELS[r.plan] || OWNER_PLAN_LABELS[r.plan] || r.plan) + (r.on_trial ? ' · trial' : '');
  $('#drill-body').innerHTML = '<table class="data"><thead><tr><th>Name</th><th>Email</th><th>Country</th>' +
    '<th>Signed up</th><th>Platform</th><th>Plan</th><th class="num">Properties</th><th>Last active</th>' +
    '</tr></thead><tbody>' + rows.map((r) =>
      // The uid rides in the tooltip: it is what to search for in Firebase or the database.
      '<tr title="' + esc(r.id) + '"><td>' + esc(r.name || '—') + '</td><td>' + esc(r.email || '—') + '</td>' +
      '<td>' + esc(countryName(r.country)) + '</td><td>' + day(r.signed_up) + '</td>' +
      '<td>' + esc(r.platform || '—') + '</td><td>' + esc(plan(r)) + '</td>' +
      '<td class="num">' + num(r.properties) + '</td><td>' + day(r.last_active) + '</td></tr>').join('') +
    '</tbody></table>';
}

$('#drill-close').addEventListener('click', () => $('#drill').close());
// A click on the backdrop lands on the <dialog> itself; one inside lands on its children.
$('#drill').addEventListener('click', (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });
// One delegated listener for every HTML mark. The info icon keeps its own job.
function drillFromEvent(e) {
  if (e.target.closest('.info')) return;
  const el = e.target.closest('[data-drill]');
  if (el) openDrill(JSON.parse(el.dataset.drill));
}
$('#sections').addEventListener('click', drillFromEvent);
$('#sections').addEventListener('keydown', (e) => {
  if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('[data-drill]')) {
    e.preventDefault();
    drillFromEvent(e);
  }
});
