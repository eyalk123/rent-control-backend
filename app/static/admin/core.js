/* Admin analytics dashboard — shared core: state, formatting, time buckets, auth, fetching, tab switching, health chip.
   Loaded as a classic script by index.html, in order: core.js, charts.js, blocks.js, drill.js,
   the tab files, main.js. Top-level names are shared across files, so they must be unique.
   File map and conventions: app/analytics/CLAUDE.md. */

const $ = (s) => document.querySelector(s);
const TABS = ['overview', 'subscriptions', 'growth', 'countries', 'ai', 'platform', 'health'];
const state = {
  range: '90d', country: '', charts: {}, seq: 0, data: null,
  tab: 'overview', breakdown: 'language', countryMetric: 'owners',
};

/** Granularity follows the range rather than being a second control: a 7- or 30-day window
 *  reads best by day, anything longer by week. */
const granularityFor = (range) => (range === '7d' || range === '30d') ? 'day' : 'week';

/* ── formatting ────────────────────────────────────────────────────── */
const nf = new Intl.NumberFormat('en-US');
const num = (v) => (v === null || v === undefined) ? '—' : nf.format(v);
const pct = (v) => (v === null || v === undefined) ? '—' : v.toFixed(1) + '%';
// Timestamps arrive naive and are UTC (app.clock.utc_now_naive). Without the 'Z' the
// browser reads them as local time, which shifts a bucket label by a day for anyone west
// of UTC — the labels would disagree with the data by one row.
function parseUtc(iso) {
  const s = String(iso);
  return new Date(/[Zz]|[+-]\d\d:?\d\d$/.test(s) ? s : s + 'Z');
}
function bucketLabel(iso, granularity) {
  if (!iso) return '';
  const s = String(iso);
  const d = parseUtc(s);
  if (isNaN(d)) return s.slice(0, 10);
  const opts = granularity === 'month'
    ? { year: 'numeric', month: 'short', timeZone: 'UTC' }
    : { month: 'short', day: 'numeric', timeZone: 'UTC' };
  return d.toLocaleDateString('en-US', opts);
}
/** A bucket named for a title: "week of Oct 5", or the day itself. */
const periodLabel = (iso, granularity) =>
  (granularity === 'week' ? 'week of ' : '') + bucketLabel(iso, granularity);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* ── empty buckets ─────────────────────────────────────────────────── */
// SQL GROUP BY returns only buckets that had rows, so a quiet week is simply missing and a
// line chart draws straight across it — a week with zero signups reads as a busy one.
// These rebuild the full run of buckets and fill the holes with zeros.
const DAY_MS = 86400000;
/** Postgres date_trunc('day'|'week') in UTC: midnight, and Monday for weeks. */
function truncUtc(date, granularity) {
  const d = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
  return granularity === 'week' ? d - ((new Date(d).getUTCDay() + 6) % 7) * DAY_MS : d;
}
/** Every bucket start (ms) in the window. On 'All' the window opens at a fixed 2025 epoch,
 *  before the product existed, so it starts at the first bucket with data instead. */
function bucketKeys(d, rows) {
  const g = d.window.granularity;
  const step = g === 'week' ? 7 * DAY_MS : DAY_MS;
  const have = rows.map((r) => parseUtc(r.bucket).getTime()).filter((t) => !isNaN(t));
  let start = truncUtc(parseUtc(d.window.since), g);
  if (d.window.range === 'all') {
    if (!have.length) return [];
    start = Math.min(...have);
  }
  const end = truncUtc(parseUtc(d.generated_at), g);
  const keys = [];
  for (let t = start; t <= end; t += step) keys.push(t);
  return keys;
}
/** One row per bucket, with `fields` zeroed where the query returned nothing. No rows at all
 *  stays empty, so chartOrEmpty still says "no data yet" instead of drawing a flat zero. */
function fillBuckets(d, rows, fields) {
  rows = rows || [];
  if (!rows.length) return rows;
  const byKey = new Map(rows.map((r) => [parseUtc(r.bucket).getTime(), r]));
  return bucketKeys(d, rows).map((t) => byKey.get(t) ||
    Object.assign({ bucket: new Date(t).toISOString() },
                  Object.fromEntries(fields.map((f) => [f, 0]))));
}

/* ── auth ──────────────────────────────────────────────────────────── */
let FIREBASE_READY = false;

function initAuth() {
  const fail = (text) => {
    $('#login-msg').hidden = false;
    $('#login-msg').className = 'msg error';
    $('#login-msg').textContent = text;
    // Disable BOTH buttons. Leaving Google enabled here is what turned a missing config
    // into a confusing "No Firebase App has been created" on click.
    $('#login-btn').disabled = true;
    $('#google-btn').disabled = true;
  };

  if (typeof firebase === 'undefined') {
    return fail('The Firebase SDK did not load. Check your connection and reload.');
  }
  if (!FIREBASE_CONFIG || !FIREBASE_CONFIG.apiKey) {
    return fail('Firebase web config is not set on the server. Set FIREBASE_WEB_API_KEY ' +
                'and FIREBASE_WEB_APP_ID, then reload.');
  }
  firebase.initializeApp(FIREBASE_CONFIG);
  FIREBASE_READY = true;
  firebase.auth().onAuthStateChanged((user) => {
    if (!user) return;
    $('#login-view').hidden = true;
    $('#app-view').hidden = false;
    load();
  });
}

function showLoginError(err) {
  const msg = $('#login-msg');
  msg.hidden = false;
  msg.className = 'msg error';
  const code = err && err.code;
  if (code === 'auth/unauthorized-domain') {
    // The most likely failure on a fresh deployment: Firebase only runs the OAuth flow on
    // domains it has been told about, and this page is on the backend's domain, which is
    // not the one the web app uses.
    msg.textContent =
      'Firebase has not authorised this domain for sign-in. Add ' + location.hostname +
      ' in the Firebase console under Authentication → Settings → Authorized domains, ' +
      'then reload.';
  } else if (code === 'auth/popup-blocked') {
    msg.textContent = 'Your browser blocked the sign-in popup. Allow popups for this site and try again.';
  } else if (code === 'auth/popup-closed-by-user' || code === 'auth/cancelled-popup-request') {
    msg.textContent = 'Sign-in was cancelled.';
  } else {
    msg.textContent = (err && err.message) ? err.message : 'Sign-in failed.';
  }
}

$('#google-btn').addEventListener('click', async () => {
  if (!FIREBASE_READY) return;
  const msg = $('#login-msg');
  msg.hidden = false; msg.className = 'msg'; msg.textContent = 'Opening Google…';
  $('#google-btn').disabled = true;
  try {
    const provider = new firebase.auth.GoogleAuthProvider();
    // Always ask which account, rather than silently reusing whichever Google session the
    // browser happens to hold — the admin account may not be the default one.
    provider.setCustomParameters({ prompt: 'select_account' });
    await firebase.auth().signInWithPopup(provider);
  } catch (err) {
    showLoginError(err);
  } finally {
    $('#google-btn').disabled = false;
  }
});

$('#login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!FIREBASE_READY) return;
  const email = $('#email').value.trim();
  const password = $('#password').value;
  const msg = $('#login-msg');
  if (!email || !password) {
    msg.hidden = false; msg.className = 'msg error';
    msg.textContent = 'Enter an email and password, or use Continue with Google.';
    return;
  }
  msg.hidden = false; msg.className = 'msg'; msg.textContent = 'Signing in…';
  $('#login-btn').disabled = true;
  try {
    await firebase.auth().signInWithEmailAndPassword(email, password);
  } catch (err) {
    showLoginError(err);
    $('#login-btn').disabled = false;
  }
});

$('#signout').addEventListener('click', async () => {
  await firebase.auth().signOut();
  location.reload();
});

/* ── fetch ─────────────────────────────────────────────────────────── */
async function load() {
  // Every control click starts a request, and a slow older one can land after a newer one.
  // Only the latest may touch the page, or the controls say 7d over 90d charts.
  const seq = ++state.seq;
  const stale = () => seq !== state.seq;

  $('#banners').innerHTML = '';
  $('#meta').textContent = 'Loading…';
  $('#sections').style.opacity = '0.5';

  const qs = new URLSearchParams({ range: state.range, granularity: granularityFor(state.range) });
  if (state.country) qs.set('country', state.country);

  let res;
  try {
    // Asked for on every load, not once at sign-in: ID tokens expire after an hour, and the
    // endpoint answers an expired one with the same 404 as a non-admin. getIdToken() returns
    // the cached token until it is about to expire, then refreshes it.
    const user = firebase.auth().currentUser;
    if (!user) return location.reload();
    const token = await user.getIdToken();
    // Bearer header only — never a token in the query string, which would land in access
    // logs, proxy logs, browser history and Referer.
    res = await fetch('./analytics?' + qs.toString(), {
      headers: { Authorization: 'Bearer ' + token },
    });
  } catch (err) {
    if (stale()) return;
    return fail('Network error', String(err));
  }
  if (stale()) return;
  if (!res.ok) {
    let detail = '';
    try { detail = (await res.json()).detail || ''; } catch (_) { detail = await res.text().catch(() => ''); }
    if (stale()) return;
    if (res.status === 404) {
      return fail('HTTP 404 — not found',
        'Either this route does not exist, or your account is not in ADMIN_OWNER_IDS. ' +
        'The endpoint answers 404 for both so it cannot be used to probe for admin accounts.');
    }
    return fail('HTTP ' + res.status + ' ' + res.statusText, detail);
  }

  const data = await res.json();
  if (stale()) return;
  state.data = data;
  $('#meta').textContent =
    'Updated ' + new Date(data.generated_at + 'Z').toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) +
    (data.cached ? ' · cached' : '');
  $('#banners').innerHTML = (data.warnings || []).map((w) =>
    '<div class="banner warn"><strong>Data warning</strong>' + esc(w) + '</div>').join('');
  populateCountries(data.platform.owner_country);
  renderHealthChip(data);
  renderTab();
}

function fail(title, detail) {
  state.data = null;
  destroyAll();
  $('#meta').textContent = '';
  $('#sections').style.opacity = '';
  $('#health-chip').hidden = true;
  $('#banners').innerHTML =
    '<div class="banner error"><strong>' + esc(title) + '</strong>' + esc(detail) + '</div>';
  $('#sections').innerHTML = '';
}

/* ── shared labels ───────────────────────────────────────────────── */
const PLAN_LABELS = { tier_3_8: '3–8 properties', tier_9_15: '9–15 properties', tier_16_plus: '16+ properties' };
const regionNames = (typeof Intl.DisplayNames === 'function')
  ? new Intl.DisplayNames(['en'], { type: 'region' }) : null;
function countryName(code) {
  if (!code || code === 'unknown') return 'Not chosen yet';
  try { return (regionNames && regionNames.of(code)) || code; } catch (_) { return code; }
}

/* ── tabs ──────────────────────────────────────────────────────────── */
// tab -> [html(d), draw(d, granularity)]. Each tab file registers itself at its bottom.
const TAB_RENDER = {};

function renderTab() {
  document.querySelectorAll('#tabs [role=tab]').forEach((b) => {
    const on = b.dataset.tab === state.tab;
    b.setAttribute('aria-selected', String(on));
    b.tabIndex = on ? 0 : -1;
  });
  // The Countries tab compares countries with each other, so the filter does nothing there.
  // Disabled rather than hidden, so the bar does not jump when switching tabs.
  const sel = $('#country');
  sel.disabled = state.tab === 'countries';
  sel.title = sel.disabled ? 'This tab always compares every country' : '';
  const d = state.data;
  if (!d) return;
  destroyAll();
  const [html, draw] = TAB_RENDER[state.tab];
  $('#sections').innerHTML = html(d);
  $('#sections').style.opacity = '';
  draw(d, d.window.granularity);
}

/** The tab lives in the URL hash so a refresh — or a pasted link — lands on the same one. */
function tabFromHash() {
  const h = location.hash.replace('#', '');
  state.tab = TABS.includes(h) ? h : 'overview';
}
window.addEventListener('hashchange', () => { tabFromHash(); renderTab(); });
$('#tabs').addEventListener('click', (e) => {
  const b = e.target.closest('[role=tab]'); if (!b) return;
  location.hash = b.dataset.tab;
});
$('#tabs').addEventListener('keydown', (e) => {
  if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
  const i = TABS.indexOf(state.tab) + (e.key === 'ArrowRight' ? 1 : -1);
  const next = TABS[(i + TABS.length) % TABS.length];
  location.hash = next;
  document.querySelector('#tabs [data-tab="' + next + '"]').focus();
});

/* ── health chip ───────────────────────────────────────────────────── */
const STALE_MS = 36 * 3600 * 1000;
function isStale(job, now) {
  const lastOk = job.last_ok ? parseUtc(job.last_ok) : null;
  return !lastOk || (now - lastOk.getTime()) > STALE_MS;
}
function renderHealthChip(d) {
  const jobs = d.operations.jobs || [];
  const now = Date.now();
  const stale = jobs.filter((j) => isStale(j, now)).length;
  const chip = $('#health-chip');
  chip.hidden = false;
  chip.className = 'health-chip ' + (!jobs.length ? '' : stale ? 'bad' : 'ok');
  $('#health-text').textContent = !jobs.length ? 'No job runs in range'
    : stale ? stale + ' job' + (stale > 1 ? 's' : '') + ' stale' : 'All jobs healthy';
}
$('#health-chip').addEventListener('click', () => { location.hash = 'health'; });

function populateCountries(rows) {
  const sel = $('#country');
  if (sel.dataset.filled) return;
  (rows || []).forEach((r) => {
    const o = document.createElement('option');
    o.value = r.country; o.textContent = r.country + ' (' + r.owners + ')';
    sel.appendChild(o);
  });
  sel.dataset.filled = '1';
}
