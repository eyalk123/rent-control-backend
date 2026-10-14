/* Admin analytics dashboard — Chart.js helpers: options, series, value labels, empty states.
   Loaded as a classic script by index.html, in order: core.js, charts.js, blocks.js, drill.js,
   the tab files, main.js. Top-level names are shared across files, so they must be unique.
   File map and conventions: app/analytics/CLAUDE.md. */

/* ── chart helpers ─────────────────────────────────────────────────── */
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

function baseOptions(extra) {
  const grid = css('--grid'), text = css('--text-secondary');
  return Object.assign({
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: {
        display: true, position: 'bottom',
        labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: 'circle',
                  color: text, font: { size: 11 }, padding: 14 },
      },
      tooltip: {
        backgroundColor: css('--navy'), padding: 10, cornerRadius: 8,
        titleFont: { size: 12 }, bodyFont: { size: 12 }, displayColors: true, boxWidth: 8,
      },
    },
    scales: {
      x: { grid: { display: false }, border: { color: grid },
           ticks: { color: text, font: { size: 11 }, maxRotation: 0, autoSkipPadding: 12 } },
      y: { beginAtZero: true, grid: { color: grid, drawTicks: false },
           border: { display: false },
           ticks: { color: text, font: { size: 11 }, precision: 0, padding: 8 } },
    },
  }, extra || {});
}
/** baseOptions with the legend off — for single-series charts, where it only repeats the title. */
const singleSeries = (extra) => baseOptions(Object.assign({
  plugins: { legend: { display: false }, tooltip: baseOptions().plugins.tooltip },
}, extra || {}));

function destroy(id) { if (state.charts[id]) { state.charts[id].destroy(); delete state.charts[id]; } }
function destroyAll() { Object.keys(state.charts).forEach(destroy); }

/** Renders a chart, or an honest empty state — never a chart drawn from [] that reads as zero. */
function chartOrEmpty(canvasId, rows, build, emptyText) {
  const el = document.getElementById(canvasId);
  if (!el) return;
  const wrap = el.parentElement;
  destroy(canvasId);
  if (!CHARTS_AVAILABLE) {
    wrap.innerHTML = '<div class="empty">Chart library unavailable — the numbers and ' +
      'tables are unaffected.</div>';
    return;
  }
  if (!rows || rows.length === 0) {
    wrap.innerHTML = '<div class="empty">' + esc(emptyText || 'No data yet') + '</div>';
    return;
  }
  state.charts[canvasId] = new Chart(el.getContext('2d'), build());
}

/** A bare trend line under a KPI: no axes, no tooltip, nothing to read but the shape. */
function sparkline(canvasId, values) {
  const el = document.getElementById(canvasId);
  if (!el) return;
  if (!CHARTS_AVAILABLE || values.length < 2) { el.parentElement.remove(); return; }
  state.charts[canvasId] = new Chart(el.getContext('2d'), {
    type: 'line',
    data: { labels: values.map((_, i) => i), datasets: [{
      data: values, borderColor: css('--series-1'), borderWidth: 2,
      cubicInterpolationMode: 'monotone', pointRadius: 0, fill: false,
    }] },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false, events: [],
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: { x: { display: false }, y: { display: false, beginAtZero: true } },
    },
  });
}

function lineSeries(label, data, colorVar) {
  return {
    label, data,
    borderColor: css(colorVar), backgroundColor: css(colorVar),
    // Monotone, not a free tension: a smoothed curve overshoots below zero next to an empty
    // bucket, drawing negative signups.
    borderWidth: 2, cubicInterpolationMode: 'monotone',
    pointRadius: 0, pointHoverRadius: 5, pointHitRadius: 24,
  };
}
function barSeries(label, data, colorVar) {
  return {
    label, data,
    backgroundColor: css(colorVar),
    borderRadius: 4, borderSkipped: false,
    // A 2px surface gap between adjacent fills rather than a stroke around each bar.
    borderColor: css('--surface'), borderWidth: { top: 0, right: 1, bottom: 0, left: 1 },
    maxBarThickness: 34,
  };
}

/* Direct value labels — the relief for the two palette slots that sit below 3:1 on white,
   and the reason no value here is reachable only by hovering. */
const valueLabels = {
  id: 'valueLabels',
  afterDatasetsDraw(chart, _args, opts) {
    if (!opts || !opts.enabled) return;
    const { ctx } = chart;
    ctx.save();
    ctx.font = '600 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    ctx.fillStyle = css('--text-secondary');
    ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
    if (opts.stacked) { drawStackLabels(chart, ctx); ctx.restore(); return; }
    chart.data.datasets.forEach((ds, i) => {
      const meta = chart.getDatasetMeta(i);
      if (meta.hidden) return;
      meta.data.forEach((el, j) => {
        const v = ds.data[j];
        if (v === null || v === undefined || v === 0) return;
        ctx.fillText(nf.format(v), el.x, el.y - 4);
      });
    });
    ctx.restore();
  },
};
/** Stacked bars: each part inside its own segment (white, so only on dark fills such as
 *  --series-1 and --series-muted, and only where it fits), the total above the stack. */
function drawStackLabels(chart, ctx) {
  chart.data.labels.forEach((_, j) => {
    let total = 0, top = Infinity, x = null;
    chart.data.datasets.forEach((ds, i) => {
      const meta = chart.getDatasetMeta(i);
      const el = meta.data[j], v = ds.data[j] || 0;
      if (meta.hidden || !el || !v) return;
      total += v; x = el.x; top = Math.min(top, el.y);
      if (Math.abs(el.base - el.y) < 16) return;
      ctx.fillStyle = '#fff'; ctx.textBaseline = 'middle';
      ctx.fillText(nf.format(v), el.x, (el.y + el.base) / 2);
    });
    if (!total) return;
    ctx.fillStyle = css('--text-secondary'); ctx.textBaseline = 'bottom';
    ctx.fillText(nf.format(total), x, top - 4);
  });
}
// Guarded. This used to be a bare `Chart.register(...)` at the top level, and when the CDN
// 404'd it threw here — which stopped the rest of the script, including the Firebase
// initialisation at the bottom. The visible symptom was "No Firebase App has been created"
// on sign-in: an error about auth whose actual cause was a missing chart library.
// Charting is a feature of this page; signing in is the page working at all. Never let the
// first break the second.
const CHARTS_AVAILABLE = typeof Chart !== 'undefined';
if (CHARTS_AVAILABLE) {
  Chart.register(valueLabels);
} else {
  console.error('Chart.js failed to load — tables and numbers will still render.');
}
