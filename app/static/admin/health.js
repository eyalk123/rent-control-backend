/* Admin analytics dashboard — Health tab.
   Loaded as a classic script by index.html: core, charts, blocks, drill, the tab files, main.
   Top-level const/function names are shared across these files, so names must be unique.
   File map and conventions: app/analytics/CLAUDE.md. */

/* ── health ────────────────────────────────────────────────────────── */
function healthHTML(d) {
  const jobs = d.operations.jobs || [];
  const now = Date.now();
  const jobsBody = jobs.length
    ? '<div class="scroll-x"><table class="data"><thead><tr><th>Job</th><th class="num">Runs</th>' +
      '<th class="num">Failed</th><th>Last success</th><th>State</th></tr></thead><tbody>' +
      jobs.map((j) => {
        const stale = isStale(j, now);
        return '<tr><td>' + esc(j.job_name) + '</td>' +
          '<td class="num">' + num(j.runs) + '</td>' +
          '<td class="num">' + num(j.bad_runs) + '</td>' +
          '<td>' + (j.last_ok ? parseUtc(j.last_ok).toLocaleString() : 'never') + '</td>' +
          '<td><span class="pill ' + (stale ? 'bad' : 'ok') + '">' +
            (stale ? 'stale' : 'healthy') + '</span></td></tr>';
      }).join('') + '</tbody></table></div>'
    : '<div class="empty">No job runs recorded in this range</div>';

  const dh = d.operations.data_health || [];
  const dhBody = dh.length
    ? '<div class="scroll-x"><table class="data"><thead><tr><th>Table</th><th class="num">Rows</th>' +
      '<th>Earliest</th><th>Latest</th></tr></thead><tbody>' + dh.map((r) =>
        '<tr><td>' + esc(r.table_name) + '</td><td class="num">' + num(r.rows) + '</td><td>' +
        (r.earliest ? String(r.earliest).slice(0, 10) : '—') + '</td><td>' +
        (r.latest ? String(r.latest).slice(0, 10) : '—') + '</td></tr>').join('') +
      '</tbody></table></div>'
    : '<div class="empty">No tables reported</div>';

  return section('Background jobs', card('Last runs',
      'Stale = no success in 36 hours. A cron that dies quietly stops reminders and says nothing.', jobsBody)) +
    section('Data health', card('Tables behind the metrics',
      'Row counts and the real date range behind every number on this dashboard.', dhBody));
}

TAB_RENDER.health = [healthHTML, () => {}];
