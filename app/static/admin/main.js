/* Admin analytics dashboard — controls and startup. Loaded last.
   Loaded as a classic script by index.html: core, charts, blocks, drill, the tab files, main.
   Top-level const/function names are shared across these files, so names must be unique.
   File map and conventions: app/analytics/CLAUDE.md. */

/* ── controls ──────────────────────────────────────────────────────── */
$('#range-seg').addEventListener('click', (e) => {
  const btn = e.target.closest('button'); if (!btn) return;
  [...e.currentTarget.children].forEach((b) => b.setAttribute('aria-pressed', String(b === btn)));
  state.range = btn.dataset.v;
  load();
});
$('#country').addEventListener('change', (e) => { state.country = e.target.value; load(); });
$('#refresh').addEventListener('click', load);
tabFromHash();
renderTab();

// Last statement on purpose: onAuthStateChanged can call load() -> render, which uses the
// const helpers defined above, and a const is not readable before its definition runs.
// Nothing above may throw at top level or this never executes — that is what the missing
// Chart.js did, and why Chart.register is now guarded.
initAuth();
