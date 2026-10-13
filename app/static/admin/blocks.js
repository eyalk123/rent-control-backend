/* Admin analytics dashboard — HTML building blocks: tiles, KPIs, cards, horizontal bars, switches.
   Loaded as a classic script by index.html, in order: core.js, charts.js, blocks.js, drill.js,
   the tab files, main.js. Top-level names are shared across files, so they must be unique.
   File map and conventions: app/analytics/CLAUDE.md. */

/* ── building blocks ───────────────────────────────────────────────── */
const info = (note) => note
  ? '<span class="info" tabindex="0" role="note" aria-label="' + esc(note) + '" data-tip="' + esc(note) + '">i</span>'
  : '';
function tile(label, value, caption, small, note, drill) {
  return '<div' + drillAttrs(drill, 'card tile') + '><h3>' + esc(label) + info(note) + '</h3>' +
    '<div class="value' + (small ? ' small' : '') + '">' + esc(value) + '</div>' +
    (caption ? '<div class="caption">' + esc(caption) + '</div>' : '') + '</div>';
}
function kpi(label, value, caption, sparkId, note, drill) {
  return '<div' + drillAttrs(drill, 'card kpi') + '><h3>' + esc(label) + info(note) + '</h3>' +
    '<div class="value">' + esc(value) + '</div>' +
    '<div class="caption">' + esc(caption) + '</div>' +
    (sparkId ? '<div class="spark"><canvas id="' + sparkId + '"></canvas></div>' : '') + '</div>';
}
function card(title, note, body) {
  return '<div class="card"><h3>' + esc(title) + info(note) + '</h3>' + body + '</div>';
}
const canvasCard = (title, note, id) =>
  card(title, note, '<div class="chart-wrap"><canvas id="' + id + '"></canvas></div>');
const section = (title, body) =>
  '<section class="block">' + (title ? '<h2>' + esc(title) + '</h2>' : '') + body + '</section>';

/** The payload's caveats for the scopes this tab shows, collapsed at the bottom of it. */
function notesFor(d, scopes) {
  const items = (d.caveats || []).filter((c) => scopes.includes(c.scope));
  if (!items.length) return '';
  return '<details class="notes card"><summary>Before you trust these numbers (' + items.length +
    ')</summary><ul class="caveats">' +
    items.map((c) => '<li>' + esc(c.text) + '</li>').join('') + '</ul></details>';
}

/** Horizontal bars with the value and its share. `items` is [{label, value, drill?}], drawn
 *  in the order given, each bar scaled to the largest. */
function hbars(items, emptyText) {
  const total = items.reduce((s, x) => s + (x.value || 0), 0);
  if (!items.length || !total) return '<div class="empty">' + esc(emptyText || 'Nothing recorded yet') + '</div>';
  const max = Math.max(...items.map((x) => x.value || 0)) || 1;
  return '<div class="hbars">' + items.map((x) => {
    const v = x.value || 0;
    return '<div' + drillAttrs(x.drill, 'hbar') + '><div class="hbar-label" title="' + esc(x.label) + '">' + esc(x.label) +
      '</div><div class="hbar-track"><div class="hbar-fill" style="width:' + (100 * v / max).toFixed(1) +
      '%' + (x.color ? ';background:var(' + x.color + ')' : '') + '"></div></div><div class="hbar-value">' +
      num(v) + '<span>' + Math.round(100 * v / total) + '%</span></div></div>';
  }).join('') + '</div>';
}
/** A segmented switch. `options` is {value: label}. */
const switcher = (id, options, current, label) =>
  '<span class="segmented" id="' + id + '" aria-label="' + esc(label) + '">' +
  Object.entries(options).map(([k, v]) =>
    '<button data-v="' + k + '" aria-pressed="' + (k === current) + '">' + esc(v) + '</button>').join('') +
  '</span>';
/** Sum a field over rows, grouped by a key — for reshaping the subscription snapshot. */
function sumBy(rows, key, field) {
  const out = {};
  rows.forEach((r) => { out[r[key]] = (out[r[key]] || 0) + (r[field] || 0); });
  return out;
}
