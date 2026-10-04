import { GOOGLE_CLIENT_ID, VERSION } from './config.js';
import * as db from './db.js';
import * as S from './state.js';
import * as tmdb from './tmdb.js';
import * as drive from './drive.js';
import { sync } from './sync.js';
import * as remote from './remote.js';
import * as omdb from './omdb.js';

const app = {
  state: null,
  shows: new Map(),
  tv: new Map(),
  seasons: new Map(),
  settings: {},
  sync: { status: 'off', error: null, last: null },
  seen: new Set(),
  showFilter: 'active',
  search: { q: '', results: [], busy: false },
  msearch: { q: '', results: [], busy: false },
  disc: { list: 'trending_tv', pages: {}, items: {}, busy: false, error: null },
  movieInfo: new Map(),
  scores: new Map(), // imdb id -> OMDb ratings
  openSeasons: new Set(),
  lastRoute: '',
  scrollPos: {}, // route -> scroll position, so Back returns to the same spot
  eventCount: 0,
};

const view = document.getElementById('view');
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const DAY = 86400000;

// ---------------------------------------------------------------- helpers
function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso + 'T00:00:00');
  const days = Math.round((d - new Date(new Date().toDateString())) / DAY);
  if (days === 0) return 'today';
  if (days === 1) return 'tomorrow';
  if (days === -1) return 'yesterday';
  if (days > 1 && days < 7) return `in ${days} days`;
  if (days < -1 && days > -7) return `${-days} days ago`;
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' });
}

function timeAgo(ms) {
  if (!ms) return 'never';
  const m = Math.round((Date.now() - ms) / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m} min ago`;
  if (m < 1440) return `${Math.round(m / 60)} h ago`;
  return new Date(ms).toLocaleDateString();
}

const isAired = (iso) => !!iso && Date.parse(iso + 'T00:00:00') <= Date.now();
const showTitle = (id) => app.tv.get(id)?.name || app.shows.get(id)?.title || 'Show';

async function pool(items, n, fn) {
  const queue = [...items];
  await Promise.all(Array.from({ length: n }, async () => {
    while (queue.length) await fn(queue.shift());
  }));
}

let renderQueued = false;
function renderSoon() {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => { renderQueued = false; render(); });
}

// ---------------------------------------------------------------- data
async function loadSettings() {
  const region = (navigator.language.split('-')[1] || 'US').toUpperCase();
  app.settings = {
    tmdbKey: await db.kvGet('tmdb_key', ''),
    omdbKey: await db.kvGet('omdb_key', ''),
    region: await db.kvGet('region', region),
    deviceName: await db.kvGet('device_name', 'Phone'),
    clientId: (await db.kvGet('google_client_id', '')) || GOOGLE_CLIENT_ID,
    signedInBefore: await db.kvGet('google_signed_in', false),
    lastTv: await db.kvGet('last_tv', null),
    watchView: await db.kvGet('watch_view', 'cards'),
  };
  tmdb.setKey(app.settings.tmdbKey);
  omdb.setKey(app.settings.omdbKey);
  app.seen = new Set(await db.kvGet('seen_releases', []));
  app.sync.last = await db.kvGet('last_sync');
}

async function refresh() {
  const events = await db.allEvents();
  app.eventCount = events.length;
  app.state = S.buildState(events);
  app.shows = S.collectShows(app.state);
}

async function loadTv({ force = false } = {}) {
  const ids = [...app.shows.values()].filter((s) => s.tracked).map((s) => s.id);
  for (const id of ids) {
    if (!app.tv.has(id)) {
      const cached = await tmdb.tvCached(id);
      if (cached) app.tv.set(id, cached);
    }
  }
  renderSoon();
  if (!tmdb.hasKey()) return;
  await pool(ids, 4, async (id) => {
    try {
      const data = await tmdb.tv(id, { force });
      if (data) app.tv.set(id, data);
    } catch { /* offline: keep cached */ }
  });
  renderSoon();
}

async function ensureTv(id) {
  if (app.tv.has(id)) return app.tv.get(id);
  const data = (await tmdb.tvCached(id)) || (tmdb.hasKey() ? await tmdb.tv(id) : null);
  if (data) app.tv.set(id, data);
  return data;
}

async function loadMovies() {
  const keys = [...new Set([...app.state.watchlist.keys(), ...S.moviesWatched(app.state).slice(0, 40).map((it) => it.key)])];
  await pool(keys, 4, async (key) => {
    const id = +key.split(':')[1];
    if (app.movieInfo.has(id) || !tmdb.hasKey()) return;
    try {
      const data = await tmdb.movie(id);
      if (data) app.movieInfo.set(id, data);
    } catch { /* offline */ }
  });
  renderSoon();
}

const seasonLoads = new Map();
function ensureSeason(id, s) {
  const key = `${id}:${s}`;
  if (app.seasons.has(key) || !tmdb.hasKey()) return;
  if (!seasonLoads.has(key)) {
    seasonLoads.set(key, tmdb.season(id, s).then((data) => {
      app.seasons.set(key, data || { episodes: [] });
      renderSoon();
    }).catch(() => seasonLoads.delete(key)));
  }
}

// ---------------------------------------------------------------- sync
let syncTimer = null;
function scheduleSync(delay = 2500) {
  clearTimeout(syncTimer);
  syncTimer = setTimeout(() => runSync(false), delay);
}

async function runSync(interactive) {
  if (app.sync.status === 'syncing') return;
  if (!app.settings.clientId) {
    app.sync.status = 'off';
    renderHeader();
    if (interactive) location.hash = '#/settings';
    return;
  }
  if (!drive.hasValidToken()) {
    if (!interactive) {
      app.sync.status = app.settings.signedInBefore ? 'needs-tap' : 'off';
      renderHeader();
      return;
    }
    try {
      await drive.signIn(app.settings.clientId);
      await db.kvSet('google_signed_in', true);
      app.settings.signedInBefore = true;
    } catch (err) {
      app.sync.status = 'error';
      app.sync.error = err.message;
      renderHeader();
      toast(err.message);
      return;
    }
  }
  app.sync.status = 'syncing';
  renderHeader();
  try {
    const result = await sync();
    app.sync.status = 'ok';
    app.sync.error = null;
    app.sync.last = Date.now();
    if (result.imported) {
      await refresh();
      loadTv();
      loadMovies();
    }
    if (interactive) toast(result.imported ? `Synced: ${result.imported} new from your other devices` : 'Up to date');
  } catch (err) {
    if (err instanceof drive.AuthNeeded) app.sync.status = 'needs-tap';
    else {
      app.sync.status = 'error';
      app.sync.error = err.message;
      if (interactive) toast('Sync failed: ' + err.message);
    }
  }
  renderHeader();
  renderSoon();
}

// ---------------------------------------------------------------- actions
async function commit(specs, message, undoSpecs) {
  await db.addEvents(specs);
  await refresh();
  render();
  scheduleSync();
  if (message) {
    toast(message, undoSpecs && undoSpecs.length ? async () => {
      await db.addEvents(undoSpecs);
      await refresh();
      render();
      scheduleSync();
    } : null);
  }
}

function epSpec(id, s, e, type) {
  const media = S.episodeMedia(id, showTitle(id), s, e);
  return { type, key: S.mediaKey(media), media };
}

function markEpisode(id, s, e, watched) {
  const type = watched ? 'watched' : 'unwatched';
  return commit([epSpec(id, s, e, type)], `${S.se(s, e)} marked ${type}`,
                [epSpec(id, s, e, watched ? 'unwatched' : 'watched')]);
}

function markMany(id, pairs, label) {
  const todo = pairs.filter(([s, e]) => !S.isWatched(app.state, S.epKey(id, s, e)));
  if (!todo.length) return toast('Already marked as watched');
  return commit(todo.map(([s, e]) => epSpec(id, s, e, 'watched')), `${todo.length} episode${todo.length > 1 ? 's' : ''} marked watched${label ? ' ' + label : ''}`,
                todo.map(([s, e]) => epSpec(id, s, e, 'unwatched')));
}

function markUpTo(id, sn, en) {
  const pairs = [];
  for (const [s, count] of S.seasonCounts(app.tv.get(id))) {
    if (s > sn) break;
    for (let e = 1; e <= count; e++) if (s < sn || e <= en) pairs.push([s, e]);
  }
  return markMany(id, pairs, `up to ${S.se(sn, en)}`);
}

function markSeason(id, sn) {
  const season = app.seasons.get(`${id}:${sn}`);
  let pairs;
  if (season?.episodes?.length) {
    pairs = season.episodes.filter((ep) => isAired(ep.air_date)).map((ep) => [sn, ep.episode_number]);
  } else {
    const count = S.seasonCounts(app.tv.get(id)).find(([s]) => s === sn)?.[1] || 0;
    pairs = Array.from({ length: count }, (_, i) => [sn, i + 1]);
  }
  return markMany(id, pairs, `in season ${sn}`);
}

function setActive(id, on) {
  const show = app.shows.get(id);
  const title = showTitle(id);
  if (!on) return commit([{ type: 'hide', key: S.showKey(id) }], `${title} deactivated`, [{ type: 'unhide', key: S.showKey(id) }]);
  if (show?.tracked) return commit([{ type: 'unhide', key: S.showKey(id) }], `${title} is active`);
  return follow(id, title);
}

async function follow(id, title) {
  await commit([{ type: 'follow', key: S.showKey(id), media: { kind: 'show', show_tmdb: id, show_title: title } }],
               `Following ${title}`, [{ type: 'hide', key: S.showKey(id) }]);
  ensureTv(id).then(renderSoon).catch(() => {});
}

function movieFromResult(r) {
  return S.movieMedia(r.id, r.title, (r.release_date || '').slice(0, 4) || undefined);
}

function addMovie(m) {
  const key = S.mediaKey(m);
  return commit([{ type: 'watchlist', key, media: m }], `${m.title} added to your list`,
                [{ type: 'unwatchlist', key }]).then(loadMovies);
}

function removeMovie(m) {
  const key = S.mediaKey(m);
  return commit([{ type: 'unwatchlist', key }], `${m.title} removed`, [{ type: 'watchlist', key, media: m }]);
}

function markMovie(m, watched) {
  const key = S.mediaKey(m);
  const type = watched ? 'watched' : 'unwatched';
  return commit([{ type, key, media: m }], `${m.title} marked ${type}`,
                [{ type: watched ? 'unwatched' : 'watched', key, media: m }]);
}

function movieSheet(m) {
  const key = S.mediaKey(m);
  const done = S.isWatched(app.state, key);
  const listed = app.state.watchlist.has(key);
  sheet(m.year ? `${m.title} (${m.year})` : m.title, [
    { label: 'Play on TV', fn: () => playMovieOnTv(m) },
    { label: 'Play on TV, choosing the source in Seren', fn: () => playMovieOnTv(m, 'pick') },
    { label: done ? 'Mark unwatched' : 'Mark watched', fn: () => markMovie(m, !done) },
    listed ? { label: 'Remove from list', fn: () => removeMovie(m), danger: true }
      : { label: 'Add to list', fn: () => addMovie(m) },
  ]);
}

async function markSeen(ids) {
  ids.forEach((id) => app.seen.add(id));
  await db.kvSet('seen_releases', [...app.seen].slice(-500));
  renderHeader();
  renderSoon();
}

// ---------------------------------------------------------------- play on TV
function ensureSignedIn() {
  // Must start inside the tap handler: signing in may open a Google popup.
  if (drive.hasValidToken()) return Promise.resolve();
  if (!app.settings.clientId) return Promise.reject(new Error('Set up Google sync in Settings first'));
  return drive.signIn(app.settings.clientId).then(async () => {
    await db.kvSet('google_signed_in', true);
    app.settings.signedInBefore = true;
  });
}

function pickTv(online) {
  if (online.length === 1) return Promise.resolve(online[0]);
  const last = app.settings.lastTv;
  return new Promise((resolve) => {
    const ordered = [...online].sort((a, b) => (b.device === last) - (a.device === last));
    sheet('Play on which TV?', ordered.map((d) => ({ label: d.name || 'Kodi', fn: () => resolve(d) })));
    document.getElementById('sheet').addEventListener('click', (ev) => {
      if (ev.target.closest('[data-close]')) resolve(null);
    }, { once: true });
  });
}

function playOnTv(id, s, e, mode = 'auto') {
  return sendToTv(`${showTitle(id)} ${S.se(s, e)}`,
                  { show_tmdb: id, season: s, episode: e, title: showTitle(id), mode });
}

function playMovieOnTv(m, mode = 'auto') {
  return sendToTv(m.title, { kind: 'movie', movie_tmdb: m.tmdb, title: m.title, year: m.year || '', mode });
}

async function sendToTv(label, fields) {
  const signing = ensureSignedIn();
  try {
    await signing;
    toast('Looking for your TVs…');
    const all = await remote.devices();
    const online = all.filter((d) => d.online);
    if (!online.length) {
      const seen = all[0] ? ` (${all[0].name} last seen ${timeAgo(all[0].last_seen * 1000)})` : '';
      return toast(`No TV is running Kodi right now${seen}. Open Kodi on the TV and try again.`);
    }
    const tv = await pickTv(online);
    if (!tv) return;
    app.settings.lastTv = tv.device;
    await db.kvSet('last_tv', tv.device);
    const cmd = await remote.send({ target: tv.device, ...fields });
    toast(`Sent ${label} to ${tv.name}…`);
    const ack = await remote.waitForAck(tv, cmd.id);
    if (!ack) toast(`${tv.name} didn't answer. Is Kodi still open?`);
    else if (ack.status === 'error') toast(`${tv.name}: ${ack.message}`);
    else toast(`${tv.name}: ${ack.message}`);
  } catch (err) {
    toast(err.message);
  }
}

// ---------------------------------------------------------------- UI chrome
let toastTimer = null;
function toast(message, undo) {
  const el = document.getElementById('toast');
  el.innerHTML = `<span>${esc(message)}</span>${undo ? '<button type="button">Undo</button>' : ''}`;
  el.hidden = false;
  if (undo) {
    el.querySelector('button').onclick = () => {
      el.hidden = true;
      undo();
    };
  }
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, undo ? 6000 : 3500);
}

function sheet(title, options, text = '') {
  const el = document.getElementById('sheet');
  el.innerHTML = `<div class="sheet-backdrop" data-close></div><div class="sheet-panel" role="dialog" aria-label="${esc(title)}">
    <div class="sheet-title">${esc(title)}</div>
    ${text ? `<p class="sheet-text">${esc(text)}</p>` : ''}
    ${options.map((o, i) => `<button type="button" class="sheet-btn${o.danger ? ' danger' : ''}" data-i="${i}">${esc(o.label)}</button>`).join('')}
    <button type="button" class="sheet-btn cancel" data-close>Cancel</button></div>`;
  el.hidden = false;
  el.onclick = (ev) => {
    const btn = ev.target.closest('[data-i],[data-close]');
    if (!btn) return;
    el.hidden = true;
    if (btn.dataset.i != null) options[+btn.dataset.i].fn();
  };
}

const SYNC_LABEL = { off: 'Sync off', ok: 'Synced', syncing: 'Syncing…', 'needs-tap': 'Tap to sync', error: 'Sync error' };

function renderHeader() {
  const btn = document.getElementById('syncBtn');
  btn.dataset.status = app.sync.status;
  btn.title = app.sync.status === 'error' ? app.sync.error || 'Sync error' : `${SYNC_LABEL[app.sync.status]} · ${timeAgo(app.sync.last)}`;
  btn.querySelector('.sync-label').textContent = SYNC_LABEL[app.sync.status];
  const unseen = S.releases(app.shows, app.tv).fresh.filter((r) => !app.seen.has(r.id)).length;
  const badge = document.getElementById('newBadge');
  badge.textContent = unseen;
  badge.hidden = !unseen;
}

function route() {
  const [name, arg] = (location.hash.replace(/^#\/?/, '') || 'watching').split('/');
  return { name, arg };
}

function render() {
  if (!app.state) return;
  const r = route();
  const key = `${r.name}/${r.arg || ''}`;
  const sameRoute = key === app.lastRoute;
  const scroll = window.scrollY;
  document.querySelectorAll('#nav a').forEach((a) => a.classList.toggle('on', a.dataset.tab === r.name));
  if (r.name === 'search' && sameRoute) {
    renderSearchResults();
  } else if (r.name === 'movies' && sameRoute && document.activeElement?.id === 'mq') {
    renderMovieResults(); // don't rebuild the page while typing in the search box
  } else {
    const views = { watching: watchingView, new: newView, movies: moviesView, shows: showsView, search: searchView,
                    show: () => showView(+r.arg), movie: () => movieView(+r.arg), episode: () => episodeView(r.arg),
                    settings: settingsView };
    view.innerHTML = (views[r.name] || watchingView)();
    if (r.name === 'search') renderSearchResults();
    if (r.name === 'movies') renderMovieResults();
  }
  app.lastRoute = key;
  // Same page: keep position. Coming back to a list: return to where you were. New page: top.
  window.scrollTo(0, sameRoute ? scroll : (app.scrollPos[key] || 0));
  renderHeader();
}

// ---------------------------------------------------------------- views
function onboarding() {
  return `<section class="empty">
    <h2>Welcome to TrackMyShows</h2>
    <p>Two quick things to set up:</p>
    <ol class="steps">
      <li>Add your free <b>TMDb API key</b> (the same one the Kodi add-on uses).</li>
      <li><b>Sign in with Google</b> to sync with your Kodi boxes.</li>
    </ol>
    <a class="btn primary" href="#/settings">Open settings</a>
  </section>`;
}

function poster(path, cls = 'poster') {
  return path ? `<img class="${cls}" src="${tmdb.img(path, 'w185')}" alt="" loading="lazy">` : `<div class="${cls} ph"></div>`;
}

function providerLogos(tv, max = 3) {
  const list = tmdb.providers(tv, app.settings.region).filter((p) => p.logo_path).slice(0, max);
  return list.map((p) => `<img class="prov" src="${tmdb.img(p.logo_path, 'w92')}" alt="${esc(p.provider_name)}" title="${esc(p.provider_name)}">`).join('');
}

const RANK = { available: 0, unknown: 1, caught_up: 2, finished: 3 };

function watchingRows() {
  return [...app.shows.values()].filter((s) => s.active).map((show) => {
    const tv = app.tv.get(show.id);
    return { show, tv, nu: S.nextUp(show, tv) };
  }).sort((a, b) => RANK[a.nu.status] - RANK[b.nu.status] || b.show.lastTs - a.show.lastTs);
}

function nextTitle(id, next) {
  if (!next) return '';
  ensureSeason(id, next[0]);
  return app.seasons.get(`${id}:${next[0]}`)?.episodes?.find((ep) => ep.episode_number === next[1])?.name || '';
}

function card({ show, tv, nu }) {
  const title = tv?.name || show.title;
  let sub;
  if (nu.status === 'available') {
    const t = nextTitle(show.id, nu.next);
    sub = `<b>Next: ${S.se(...nu.next)}</b>${t ? ' · ' + esc(t) : ''}`;
  } else if (nu.status === 'caught_up') {
    sub = nu.nextAirDate ? `Caught up · ${nu.next?.[1] === 1 ? 'Season ' + nu.next[0] + ' premieres' : 'next episode'} ${esc(fmtDate(nu.nextAirDate))}` : 'All caught up';
  } else if (nu.status === 'finished') {
    sub = 'Finished';
  } else {
    sub = nu.last ? `Last watched ${S.se(...nu.last)}` : 'Not started';
  }
  const pct = nu.airedCount ? Math.round((nu.airedWatched / nu.airedCount) * 100) : 0;
  const counts = nu.airedCount ? `${nu.airedWatched} of ${nu.airedCount} episodes` : `${nu.watchedCount} watched`;
  return `<article class="card" data-href="#/show/${show.id}">
    ${poster(tv?.poster_path)}
    <div class="card-body">
      <div class="card-title">${esc(title)}</div>
      <div class="card-sub">${sub}</div>
      <div class="bar" aria-hidden="true"><span style="width:${pct}%"></span></div>
      <div class="card-meta"><span>${counts}</span><span class="provs">${providerLogos(tv, 2)}</span></div>
    </div>
    ${nu.status === 'available' ? `<button type="button" class="play" data-action="playTv" data-id="${show.id}" data-s="${nu.next[0]}" data-e="${nu.next[1]}" aria-label="Play ${S.se(...nu.next)} on TV">▶</button>` : ''}
    ${nu.status === 'available' ? `<button type="button" class="check" data-action="markNext" data-id="${show.id}" data-s="${nu.next[0]}" data-e="${nu.next[1]}" aria-label="Mark ${S.se(...nu.next)} watched">✓</button>` : ''}
  </article>`;
}

function compactRow({ show, tv, nu }) {
  let right;
  if (nu.status === 'available') right = `<b>${S.se(...nu.next)}</b>`;
  else if (nu.status === 'caught_up') right = nu.nextAirDate ? esc(fmtDate(nu.nextAirDate)) : 'Caught up';
  else if (nu.status === 'finished') right = 'Finished';
  else right = nu.last ? S.se(...nu.last) : 'New';
  const pct = nu.airedCount ? Math.round((nu.airedWatched / nu.airedCount) * 100) : 0;
  return `<a class="crow" href="#/show/${show.id}" style="--pct:${pct}%">
    <span class="crow-title">${esc(tv?.name || show.title)}</span>
    <span class="crow-next">${right}</span>
  </a>`;
}

function viewToggle() {
  const v = app.settings.watchView;
  return `<div class="seg" role="group" aria-label="Layout">
    <button type="button" class="${v === 'cards' ? 'on' : ''}" data-action="setView" data-v="cards" aria-pressed="${v === 'cards'}">Cards</button>
    <button type="button" class="${v === 'compact' ? 'on' : ''}" data-action="setView" data-v="compact" aria-pressed="${v === 'compact'}">Compact</button>
  </div>`;
}

function watchingView() {
  if (!tmdb.hasKey()) return onboarding();
  const rows = watchingRows();
  if (!rows.length) {
    return `<section class="empty"><h2>Nothing here yet</h2>
      <p>Shows you watch in Kodi appear automatically. For Netflix, Prime and others, search for the show and add it.</p>
      <a class="btn primary" href="#/search">Add a show</a></section>`;
  }
  const up = rows.filter((r) => r.nu.status === 'available' || r.nu.status === 'unknown');
  const caught = rows.filter((r) => r.nu.status === 'caught_up');
  const done = rows.filter((r) => r.nu.status === 'finished');
  const compact = app.settings.watchView === 'compact';
  const item = compact ? compactRow : card;
  const list = (rs) => (compact ? `<div class="clist">${rs.map(item).join('')}</div>` : rs.map(item).join(''));
  return `<div class="watch-head">${viewToggle()}</div>
    ${up.length ? `<h2 class="section">Up next</h2>${list(up)}` : ''}
    ${caught.length ? `<h2 class="section">Caught up</h2>${list(caught)}` : ''}
    ${done.length ? `<details class="fold"><summary class="section">Finished (${done.length})</summary>${list(done)}</details>` : ''}`;
}

function newView() {
  if (!tmdb.hasKey()) return onboarding();
  const { fresh, upcoming } = S.releases(app.shows, app.tv);
  const unseen = fresh.filter((r) => !app.seen.has(r.id));
  const row = (r, isFresh) => {
    let what;
    if (r.kind === 'season') what = `Season ${r.season} is out`;
    else if (r.kind === 'episode') what = `New episode ${S.se(r.season, r.episode)}`;
    else if (r.kind === 'premiere') what = `Season ${r.season} premieres`;
    else what = `Next episode ${S.se(r.season, r.episode)}`;
    const isNew = isFresh && !app.seen.has(r.id);
    return `<article class="row" data-action="openRelease" data-id="${r.showId}" data-rid="${esc(r.id)}">
      ${poster(r.poster, 'thumb')}
      <div class="row-body"><div class="row-title">${esc(r.title)} ${isNew ? '<span class="pill">NEW</span>' : ''}</div>
      <div class="row-sub">${what} · ${esc(fmtDate(r.date))}</div></div>
      <span class="provs">${providerLogos(app.tv.get(r.showId), 2)}</span>
    </article>`;
  };
  return `<div class="section-head"><h2 class="section">New for you</h2>
      ${unseen.length ? `<button type="button" class="link" data-action="seenAll">Mark all seen</button>` : ''}</div>
    ${fresh.length ? fresh.map((r) => row(r, true)).join('') : '<p class="muted pad">No new seasons or episodes for your active shows right now.</p>'}
    <h2 class="section">Coming up</h2>
    ${upcoming.length ? upcoming.map((r) => row(r, false)).join('') : '<p class="muted pad">No announced air dates yet.</p>'}
    <p class="muted pad small">Checked against TMDb each time you open the app.</p>`;
}

function showsView() {
  const all = [...app.shows.values()].filter((s) => s.tracked).sort((a, b) => showTitle(a.id).localeCompare(showTitle(b.id)));
  const f = app.showFilter;
  const list = all.filter((s) => (f === 'all' ? true : f === 'active' ? s.active : !s.active));
  const chip = (k, label) => `<button type="button" class="chip${f === k ? ' on' : ''}" data-action="filter" data-f="${k}">${label}</button>`;
  return `<div class="chips">${chip('active', `Active (${all.filter((s) => s.active).length})`)}${chip('inactive', `Inactive (${all.filter((s) => !s.active).length})`)}${chip('all', 'All')}</div>
    ${list.map((s) => {
      const tv = app.tv.get(s.id);
      const nu = S.nextUp(s, tv);
      return `<article class="row" data-href="#/show/${s.id}">
        ${poster(tv?.poster_path, 'thumb')}
        <div class="row-body"><div class="row-title">${esc(showTitle(s.id))}</div>
        <div class="row-sub">${nu.last ? 'Last ' + S.se(...nu.last) : 'Not started'}${tv?.status ? ' · ' + esc(tv.status) : ''}</div></div>
        <label class="switch" data-stop><input type="checkbox" data-change="active" data-id="${s.id}" ${s.active ? 'checked' : ''} aria-label="Active"><span></span></label>
      </article>`;
    }).join('') || '<p class="muted pad">No shows in this list.</p>'}`;
}

function movieRow(m, { watchedTs } = {}) {
  const info = app.movieInfo.get(m.tmdb);
  const done = S.isWatched(app.state, S.mediaKey(m));
  const bits = [];
  if (info?.release_date && Date.parse(info.release_date) > Date.now()) bits.push(`Out ${fmtDate(info.release_date)}`);
  else if (m.year) bits.push(m.year);
  if (info?.runtime) bits.push(`${Math.floor(info.runtime / 60)}h ${String(info.runtime % 60).padStart(2, '0')}m`);
  if (watchedTs) bits.push(`watched ${new Date(watchedTs * 1000).toLocaleDateString()}`);
  return `<article class="mrow">
    ${poster(info?.poster_path, 'thumb')}
    <div class="row-body" data-href="#/movie/${m.tmdb}">
      <div class="row-title">${esc(m.title)}</div>
      <div class="row-sub">${esc(bits.join(' · '))}</div>
    </div>
    <span class="provs">${providerLogos(info, 2)}</span>
    ${done ? '' : `<button type="button" class="play" data-action="playMovie" data-m="${esc(JSON.stringify(m))}" aria-label="Play ${esc(m.title)} on TV">▶</button>
    <button type="button" class="check" data-action="markMovie" data-m="${esc(JSON.stringify(m))}" aria-label="Mark ${esc(m.title)} watched">✓</button>`}
  </article>`;
}

const scoreLoads = new Set();
function scoreBadges(imdbId) {
  if (!imdbId || !omdb.hasKey()) return '';
  if (!app.scores.has(imdbId) && !scoreLoads.has(imdbId)) {
    scoreLoads.add(imdbId);
    omdb.ratings(imdbId).then((r) => { app.scores.set(imdbId, r); renderSoon(); })
      .catch((err) => { app.scores.set(imdbId, null); console.warn(err); });
  }
  const r = app.scores.get(imdbId);
  if (!r) return '';
  const parts = [];
  if (r.rt) {
    const pct = parseInt(r.rt, 10);
    parts.push(`<span class="score" title="Rotten Tomatoes (critics)">${pct >= 60 ? '🍅' : '🤢'} ${esc(r.rt)}</span>`);
  }
  if (r.imdb) parts.push(`<span class="score" title="IMDb">IMDb ${esc(r.imdb)}</span>`);
  if (r.metacritic) parts.push(`<span class="score" title="Metacritic">MC ${esc(r.metacritic)}</span>`);
  return parts.length ? `<div class="scores">${parts.join('')}</div>` : '';
}

function episodeView(arg) {
  const [id, sn, en] = String(arg).split(':').map(Number);
  const tv = app.tv.get(id);
  if (!tv) {
    ensureTv(id).then(renderSoon).catch((err) => toast(err.message));
    return '<p class="muted pad">Loading…</p>';
  }
  ensureSeason(id, sn);
  const season = app.seasons.get(`${id}:${sn}`);
  if (!season) return '<p class="muted pad">Loading…</p>';
  const eps = season.episodes || [];
  const idx = eps.findIndex((x) => x.episode_number === en);
  const ep = eps[idx];
  if (!ep) return `<p class="muted pad">Episode not found. <a href="#/show/${id}">Back to ${esc(tv.name)}</a></p>`;

  const done = S.isWatched(app.state, S.epKey(id, sn, en));
  const aired = isAired(ep.air_date);
  const facts = [];
  if (ep.air_date) facts.push(aired ? new Date(ep.air_date + 'T00:00:00').toLocaleDateString() : `Airs ${fmtDate(ep.air_date)}`);
  if (ep.runtime) facts.push(`${ep.runtime} min`);
  if (ep.vote_count > 5 && ep.vote_average) facts.push(`★ ${ep.vote_average.toFixed(1)}`);
  const crew = (job) => (ep.crew || []).filter((c) => c.job === job).map((c) => c.name);
  const directors = crew('Director');
  const writers = [...new Set([...crew('Writer'), ...crew('Teleplay'), ...crew('Story')])];
  const guests = (ep.guest_stars || []).slice(0, 8);

  // previous / next, crossing season boundaries
  const counts = S.seasonCounts(tv);
  let prev = idx > 0 ? [sn, eps[idx - 1].episode_number] : null;
  let next = idx < eps.length - 1 ? [sn, eps[idx + 1].episode_number] : null;
  if (!prev) {
    const before = counts.filter(([s]) => s < sn).pop();
    if (before) prev = [before[0], before[1]];
  }
  if (!next) {
    const after = counts.find(([s]) => s > sn);
    if (after) next = [after[0], 1];
  }
  const navLink = (p, label) => (p ? `<a class="btn" href="#/episode/${id}:${p[0]}:${p[1]}" data-replace>${label}</a>` : '<span></span>');

  return `<div class="hero ep-hero"${ep.still_path ? ` style="background-image:url('${tmdb.img(ep.still_path, 'w780')}')"` : ''}></div>
    <div class="ep-head">
      <a class="muted small" href="#/show/${id}">‹ ${esc(tv.name)}</a>
      <h1>${S.se(sn, en)} · ${esc(ep.name || '')}</h1>
      <div class="muted">${esc(facts.join(' · '))}</div>
    </div>
    ${ep.overview ? `<p class="overview">${esc(ep.overview)}</p>` : '<p class="muted pad">No summary yet.</p>'}
    ${done
      ? `<p class="note">You've watched this. <button type="button" class="link" data-action="epUnwatch" data-id="${id}" data-s="${sn}" data-e="${en}">Mark unwatched</button></p>`
      : `<div class="next-actions">
          <button type="button" class="btn primary" data-action="playTv" data-id="${id}" data-s="${sn}" data-e="${en}" ${aired ? '' : 'disabled'}>▶ Play on TV</button>
          <button type="button" class="btn" data-action="markNext" data-id="${id}" data-s="${sn}" data-e="${en}">✓ Watched</button>
        </div>
        <button type="button" class="link" data-action="epUpTo" data-id="${id}" data-s="${sn}" data-e="${en}">Mark watched up to here</button>`}
    ${directors.length || writers.length ? `<div class="credits">
        ${directors.length ? `<div><span class="muted">Directed by</span> ${esc(directors.join(', '))}</div>` : ''}
        ${writers.length ? `<div><span class="muted">Written by</span> ${esc(writers.join(', '))}</div>` : ''}
      </div>` : ''}
    ${guests.length ? `<h2 class="section">Guest stars</h2>
      <div class="guests">${guests.map((g) => `<div class="guest">
        ${g.profile_path ? `<img src="${tmdb.img(g.profile_path, 'w185')}" alt="" loading="lazy">` : '<div class="guest-ph"></div>'}
        <div class="guest-name">${esc(g.name)}</div>${g.character ? `<div class="muted small">${esc(g.character)}</div>` : ''}</div>`).join('')}</div>` : ''}
    <div class="ep-nav">${navLink(prev, '‹ Previous')}${navLink(next, 'Next ›')}</div>`;
}

async function ensureMovie(id) {
  if (app.movieInfo.has(id)) return app.movieInfo.get(id);
  const data = await tmdb.movie(id);
  if (data) app.movieInfo.set(id, data);
  return data;
}

function movieView(id) {
  const info = app.movieInfo.get(id);
  if (!info) {
    ensureMovie(id).then(renderSoon).catch((err) => toast(err.message));
    return '<p class="muted pad">Loading…</p>';
  }
  const year = (info.release_date || '').slice(0, 4);
  const m = S.movieMedia(id, info.title, year || undefined);
  const key = S.mediaKey(m);
  const watched = S.isWatched(app.state, key);
  const listed = app.state.watchlist.has(key);
  const unreleased = info.release_date && Date.parse(info.release_date) > Date.now();
  const facts = [];
  if (unreleased) facts.push(`Out ${fmtDate(info.release_date)}`);
  else if (year) facts.push(year);
  if (info.runtime) facts.push(`${Math.floor(info.runtime / 60)}h ${String(info.runtime % 60).padStart(2, '0')}m`);
  if (info.vote_count > 50) facts.push(`★ ${info.vote_average.toFixed(1)}`);
  const genres = (info.genres || []).map((g) => g.name).join(', ');
  const provs = tmdb.providers(info, app.settings.region);
  const item = app.state.items.get(key);
  const pm = esc(JSON.stringify(m));
  return `<div class="hero"${info.backdrop_path ? ` style="background-image:url('${tmdb.img(info.backdrop_path, 'w780')}')"` : ''}></div>
    <div class="show-head">
      ${poster(info.poster_path, 'poster big')}
      <div>
        <h1>${esc(info.title)}</h1>
        <div class="muted">${esc(facts.join(' · '))}</div>
        ${genres ? `<div class="muted small">${esc(genres)}</div>` : ''}
        ${scoreBadges(info.imdb_id)}
      </div>
    </div>
    ${info.tagline ? `<p class="tagline">${esc(info.tagline)}</p>` : ''}
    ${info.overview ? `<p class="overview">${esc(info.overview)}</p>` : ''}
    ${provs.length ? `<div class="where"><span class="muted">Watch on</span>${provs.map((p) => `<span class="prov-chip">${p.logo_path ? `<img src="${tmdb.img(p.logo_path, 'w92')}" alt="">` : ''}${esc(p.provider_name)}</span>`).join('')}</div>` : ''}
    ${watched
      ? `<p class="note">Watched${item?.watchedTs ? ` on ${new Date(item.watchedTs * 1000).toLocaleDateString()}` : ''}. <button type="button" class="link" data-action="movieUnwatch" data-m="${pm}">Mark unwatched</button></p>`
      : `<div class="next-actions">
          <button type="button" class="btn primary" data-action="playMovie" data-m="${pm}">▶ Play on TV</button>
          <button type="button" class="btn" data-action="markMovie" data-m="${pm}">✓ Watched</button>
        </div>`}
    <div class="btns movie-btns">
      ${listed ? `<button type="button" class="btn" data-action="movieUnlist" data-m="${pm}">Remove from list</button>`
        : watched ? '' : `<button type="button" class="btn" data-action="addMovie" data-m="${pm}">+ Add to list</button>`}
      ${!watched && !listed ? `<button type="button" class="btn" data-action="movieNo" data-m="${pm}">Not interested</button>` : ''}
    </div>`;
}

function moviesView() {
  if (!tmdb.hasKey()) return onboarding();
  const todo = S.moviesToWatch(app.state);
  const watched = S.moviesWatched(app.state);
  return `<form class="searchbar inline" data-form="msearch"><input id="mq" type="search" placeholder="Search movies to add" value="${esc(app.msearch.q)}" autocomplete="off" enterkeyhint="search"></form>
    <div id="mresults"></div>
    <h2 class="section">To watch${todo.length ? ` (${todo.length})` : ''}</h2>
    ${todo.length ? todo.map((t) => movieRow(t.media)).join('') : '<p class="muted pad">Search above to add movies you want to watch.</p>'}
    ${watched.length ? `<details class="fold"><summary class="section">Watched (${watched.length})</summary>
      ${watched.map((it) => movieRow(it.media, { watchedTs: it.watchedTs })).join('')}</details>` : ''}`;
}

function renderMovieResults() {
  const box = document.getElementById('mresults');
  if (!box) return;
  const ms = app.msearch;
  if (!ms.q) { box.innerHTML = ''; return; }
  if (ms.busy) { box.innerHTML = '<p class="muted pad">Searching…</p>'; return; }
  if (!ms.results.length) { box.innerHTML = '<p class="muted pad">No movies found.</p>'; return; }
  box.innerHTML = ms.results.map((r) => {
    const m = movieFromResult(r);
    const key = S.mediaKey(m);
    const state = S.isWatched(app.state, key) ? '<span class="tag">Watched</span>'
      : app.state.watchlist.has(key) ? '<span class="tag">On your list</span>'
        : `<button type="button" class="btn small" data-action="addMovie" data-m="${esc(JSON.stringify(m))}">Add</button>`;
    return `<article class="row" data-href="#/movie/${r.id}">
      ${poster(r.poster_path, 'thumb')}
      <div class="row-body"><div class="row-title">${esc(r.title)}${m.year ? ` <span class="muted">(${m.year})</span>` : ''}</div>
      <div class="row-sub clamp">${esc(r.overview || '')}</div></div>${state}</article>`;
  }).join('');
}

let movieSearchTimer = null;
function onMovieSearchInput(q) {
  app.msearch.q = q;
  clearTimeout(movieSearchTimer);
  if (!q.trim()) {
    app.msearch.results = [];
    renderMovieResults();
    return;
  }
  movieSearchTimer = setTimeout(async () => {
    app.msearch.busy = true;
    renderMovieResults();
    try {
      app.msearch.results = (await tmdb.searchMovie(q.trim())).slice(0, 15);
    } catch (err) {
      toast(err.message);
    }
    app.msearch.busy = false;
    renderMovieResults();
  }, 350);
}

function searchView() {
  return `<form class="searchbar" data-form="search"><input id="q" type="search" placeholder="Search TV shows" value="${esc(app.search.q)}" autocomplete="off" enterkeyhint="search"></form>
    <div id="results"></div>`;
}

// ---------------------------------------------------------------- discover
async function loadDiscover(list, more = false) {
  const d = app.disc;
  if (d.busy || !tmdb.hasKey()) return;
  const page = more ? (d.pages[list] || 1) + 1 : 1;
  if (!more && d.items[list]) return;
  d.busy = true;
  d.error = null;
  renderSearchResults();
  try {
    const { results, totalPages } = await tmdb.discover(list, page, app.settings.region);
    d.items[list] = more ? [...(d.items[list] || []), ...results] : results;
    d.pages[list] = page;
    d.total = { ...(d.total || {}), [list]: totalPages };
  } catch (err) {
    d.error = err.message;
  }
  d.busy = false;
  renderSearchResults();
}

function discoverItem(r, kind) {
  const isTv = kind === 'tv';
  const title = isTv ? r.name : r.title;
  const date = isTv ? r.first_air_date : r.release_date;
  const year = (date || '').slice(0, 4);
  const sub = [];
  if (app.disc.list === 'upcoming_movies' && date) sub.push(`Out ${fmtDate(date)}`);
  else if (year) sub.push(year);
  if (r.vote_count > 50 && r.vote_average) sub.push(`★ ${r.vote_average.toFixed(1)}`);
  const payload = esc(JSON.stringify({ kind, id: r.id, title, year }));
  return `<article class="row disc" data-href="#/${isTv ? 'show' : 'movie'}/${r.id}">
    ${poster(r.poster_path, 'thumb')}
    <div class="row-body">
      <div class="row-title">${esc(title)}</div>
      <div class="row-sub">${esc(sub.join(' · '))}</div>
      <div class="row-sub clamp">${esc(r.overview || '')}</div>
    </div>
    <div class="disc-actions">
      <button type="button" class="btn small" data-action="discAdd" data-p="${payload}">Add</button>
      <button type="button" class="btn small ghost" data-action="discRemove" data-p="${payload}" aria-label="Remove ${esc(title)} from suggestions">Remove</button>
    </div>
  </article>`;
}

function renderDiscover(box) {
  const d = app.disc;
  const chips = Object.entries(tmdb.DISCOVER_LISTS).map(([key, l]) =>
    `<button type="button" class="chip${d.list === key ? ' on' : ''}" data-action="discList" data-l="${key}">${l.label}</button>`).join('');
  const meta = tmdb.DISCOVER_LISTS[d.list];
  const all = d.items[d.list];
  if (!all && !d.busy && !d.error) loadDiscover(d.list);
  const visible = (all || []).filter((r) => !S.alreadyKnown(app.state, app.shows, meta.kind, r.id));
  const canMore = all && (d.pages[d.list] || 1) < ((d.total || {})[d.list] || 1);
  let body;
  if (d.error) body = `<p class="error pad">${esc(d.error)}</p>`;
  else if (!all) body = '<p class="muted pad">Loading…</p>';
  else if (!visible.length) body = '<p class="muted pad">Nothing new here. Try loading more.</p>';
  else body = visible.map((r) => discoverItem(r, meta.kind)).join('');
  box.innerHTML = `<div class="chips">${chips}</div>${body}
    ${canMore ? `<button type="button" class="btn wide" data-action="discMore20" ${d.busy ? 'disabled' : ''}>${d.busy ? 'Loading…' : 'Load more'}</button>` : ''}`;
}

function dismiss(p, reason) {
  const key = p.kind === 'tv' ? S.showKey(p.id) : `movie:${p.id}`;
  const specs = [{ type: 'dismiss', key, reason }];
  if (reason === 'seen' && p.kind === 'movie') {
    specs.push({ type: 'watched', key, media: S.movieMedia(p.id, p.title, p.year) });
  }
  const undo = [{ type: 'undismiss', key }];
  if (reason === 'seen' && p.kind === 'movie') undo.push({ type: 'unwatched', key, media: S.movieMedia(p.id, p.title, p.year) });
  const msg = reason === 'seen' ? `${p.title}: marked as watched` : `${p.title} removed from suggestions`;
  return commit(specs, msg, undo);
}

function renderSearchResults() {
  const box = document.getElementById('results');
  if (!box) return;
  if (!tmdb.hasKey()) {
    box.innerHTML = '<p class="muted pad">Add your TMDb key in Settings to search.</p>';
    return;
  }
  if (!app.search.q.trim()) {
    renderDiscover(box);
    return;
  }
  if (app.search.busy) {
    box.innerHTML = '<p class="muted pad">Searching…</p>';
    return;
  }
  if (app.search.q && !app.search.results.length) {
    box.innerHTML = '<p class="muted pad">No shows found.</p>';
    return;
  }
  box.innerHTML = app.search.results.map((r) => {
    const s = app.shows.get(r.id);
    const year = (r.first_air_date || '').slice(0, 4);
    const button = s?.active
      ? '<span class="tag">Following</span>'
      : `<button type="button" class="btn small" data-action="follow" data-id="${r.id}" data-title="${esc(r.name)}">${s?.tracked ? 'Reactivate' : 'Add'}</button>`;
    return `<article class="row" data-href="#/show/${r.id}">
      ${poster(r.poster_path, 'thumb')}
      <div class="row-body"><div class="row-title">${esc(r.name)}${year ? ` <span class="muted">(${year})</span>` : ''}</div>
      <div class="row-sub clamp">${esc(r.overview || '')}</div></div>${button}</article>`;
  }).join('');
}

let searchTimer = null;
function onSearchInput(q) {
  app.search.q = q;
  clearTimeout(searchTimer);
  if (!q.trim()) {
    app.search.results = [];
    renderSearchResults();
    return;
  }
  searchTimer = setTimeout(async () => {
    app.search.busy = true;
    renderSearchResults();
    try {
      app.search.results = (await tmdb.searchTv(q.trim())).slice(0, 20);
    } catch (err) {
      toast(err.message);
    }
    app.search.busy = false;
    renderSearchResults();
  }, 350);
}

function showView(id) {
  const tv = app.tv.get(id);
  if (!tv) {
    ensureTv(id).then(renderSoon).catch((err) => toast(err.message));
    return '<p class="muted pad">Loading…</p>';
  }
  const show = app.shows.get(id) || { id, title: tv.name, episodes: new Map(), active: false, tracked: false };
  const nu = S.nextUp(show, tv);
  const years = `${(tv.first_air_date || '').slice(0, 4)}${tv.status === 'Ended' && tv.last_air_date ? '–' + tv.last_air_date.slice(0, 4) : ''}`;
  const provs = tmdb.providers(tv, app.settings.region);
  if (app.openedFor !== id) { // first visit: open the season you're in
    app.openedFor = id;
    app.openSeasons.clear();
    if (nu.next) app.openSeasons.add(`${id}:${nu.next[0]}`);
  }
  const seasons = (tv.seasons || []).filter((s) => s.episode_count > 0)
    .sort((a, b) => (a.season_number === 0) - (b.season_number === 0) || a.season_number - b.season_number);

  const seasonBlock = (s) => {
    const sn = s.season_number;
    const key = `${id}:${sn}`;
    const open = app.openSeasons.has(key);
    const watched = [...show.episodes.entries()].filter(([k, it]) => k.startsWith(sn + ':') && it.watched).length;
    let body = '';
    if (open) {
      ensureSeason(id, sn);
      const data = app.seasons.get(key);
      body = data ? (data.episodes || []).map((ep) => {
        const done = S.isWatched(app.state, S.epKey(id, sn, ep.episode_number));
        const future = !isAired(ep.air_date);
        return `<div class="ep${done ? ' done' : ''}${future ? ' future' : ''}">
          <button type="button" class="tick" data-action="toggleEp" data-id="${id}" data-s="${sn}" data-e="${ep.episode_number}" ${future ? 'disabled' : ''} aria-label="${done ? 'Mark unwatched' : 'Mark watched'}">${done ? '✓' : ''}</button>
          <div class="ep-main" data-href="#/episode/${id}:${sn}:${ep.episode_number}">
            <div class="ep-title">${ep.episode_number}. ${esc(ep.name || '')}</div>
            <div class="ep-sub">${ep.air_date ? esc(fmtDate(ep.air_date)) : 'No date'}${ep.runtime ? ' · ' + ep.runtime + ' min' : ''}</div>
          </div></div>`;
      }).join('') : '<p class="muted pad">Loading…</p>';
    }
    return `<section class="season${open ? ' open' : ''}">
      <div class="season-head" data-action="toggleSeason" data-key="${key}">
        <span class="season-name">${esc(s.name || 'Season ' + sn)}</span>
        <span class="muted">${watched}/${s.episode_count}</span>
        ${open ? `<button type="button" class="link" data-action="markSeason" data-id="${id}" data-s="${sn}">Mark all</button>` : ''}
        <span class="chev">${open ? '▾' : '▸'}</span>
      </div>${open ? `<div class="eps">${body}</div>` : ''}</section>`;
  };

  let nextLine = '';
  if (nu.status === 'available') {
    const t = nextTitle(id, nu.next);
    nextLine = `<div class="next-actions">
      <button type="button" class="btn primary" data-action="playTv" data-id="${id}" data-s="${nu.next[0]}" data-e="${nu.next[1]}">▶ Play ${S.se(...nu.next)} on TV</button>
      <button type="button" class="btn" data-action="markNext" data-id="${id}" data-s="${nu.next[0]}" data-e="${nu.next[1]}">✓ Watched</button>
    </div>${t ? `<p class="muted next-title"><a href="#/episode/${id}:${nu.next[0]}:${nu.next[1]}">${S.se(...nu.next)} · ${esc(t)} ›</a></p>` : ''}`;
  } else if (nu.status === 'caught_up') {
    nextLine = `<p class="note">You're caught up.${nu.nextAirDate ? ` Next: ${S.se(...nu.next)} ${esc(fmtDate(nu.nextAirDate))}.` : ''}</p>`;
  } else if (nu.status === 'finished') {
    nextLine = '<p class="note">You finished this show.</p>';
  }

  return `<div class="hero"${tv.backdrop_path ? ` style="background-image:url('${tmdb.img(tv.backdrop_path, 'w780')}')"` : ''}></div>
    <div class="show-head">
      ${poster(tv.poster_path, 'poster big')}
      <div>
        <h1>${esc(tv.name)}</h1>
        <div class="muted">${esc(years)} · ${esc(tv.status || '')} · ${tv.number_of_seasons || '?'} season${tv.number_of_seasons === 1 ? '' : 's'}</div>
        ${scoreBadges(tv.external_ids?.imdb_id)}
        <label class="toggle-row"><span>Active</span><span class="switch"><input type="checkbox" data-change="active" data-id="${id}" ${show.active ? 'checked' : ''}><span></span></span></label>
      </div>
    </div>
    ${provs.length ? `<div class="where"><span class="muted">Watch on</span>${provs.map((p) => `<span class="prov-chip">${p.logo_path ? `<img src="${tmdb.img(p.logo_path, 'w92')}" alt="">` : ''}${esc(p.provider_name)}</span>`).join('')}</div>` : ''}
    ${tv.overview ? `<p class="overview clamp4" data-action="expand">${esc(tv.overview)}</p>` : ''}
    ${nextLine}
    ${seasons.map(seasonBlock).join('')}`;
}

function settingsView() {
  const st = app.settings;
  const signedIn = drive.hasValidToken() || st.signedInBefore;
  return `<h2 class="section">Settings</h2>
  <form class="form" data-form="settings">
    <fieldset><legend>Google Drive sync</legend>
      <p class="muted">${signedIn ? `Status: ${SYNC_LABEL[app.sync.status]} · last sync ${timeAgo(app.sync.last)}` : 'Sign in with the same Google account your Kodi boxes use.'}</p>
      ${app.sync.status === 'error' && app.sync.error ? `<p class="error">${esc(app.sync.error)}</p>` : ''}
      <div class="btns">
        <button type="button" class="btn primary" data-action="syncNow">${signedIn ? 'Sync now' : 'Sign in with Google'}</button>
        ${signedIn ? '<button type="button" class="btn" data-action="signOut">Sign out</button>' : ''}
      </div>
      <label>Google client ID (Web application)<input name="clientId" value="${esc(st.clientId)}" placeholder="xxxx.apps.googleusercontent.com" autocomplete="off"></label>
    </fieldset>
    <fieldset><legend>TMDb</legend>
      <label>API key<input name="tmdbKey" value="${esc(st.tmdbKey)}" autocomplete="off" spellcheck="false"></label>
      <label>OMDb API key, for Rotten Tomatoes scores (free at omdbapi.com)<input name="omdbKey" value="${esc(st.omdbKey)}" autocomplete="off" spellcheck="false"></label>
      <label>Streaming region (2 letters)<input name="region" value="${esc(st.region)}" maxlength="2" autocapitalize="characters"></label>
    </fieldset>
    <fieldset><legend>This phone</legend>
      <label>Device name<input name="deviceName" value="${esc(st.deviceName)}"></label>
    </fieldset>
    <button type="submit" class="btn primary wide">Save</button>
  </form>
  <p class="muted pad small">Install: in Chrome, open the ⋮ menu and choose <b>Install app</b> (or <b>Add to Home screen</b>).<br>
  TrackMyShows ${VERSION} · ${app.eventCount} history entries</p>`;
}

// ---------------------------------------------------------------- events
const actions = {
  markNext: (d) => markEpisode(+d.id, +d.s, +d.e, true),
  playTv: (d) => playOnTv(+d.id, +d.s, +d.e),
  addMovie: (d) => addMovie(JSON.parse(d.m)),
  discList: (d) => { app.disc.list = d.l; renderSearchResults(); },
  discMore20: () => loadDiscover(app.disc.list, true),
  discAdd: (d) => {
    const p = JSON.parse(d.p);
    if (p.kind === 'tv') return follow(p.id, p.title);
    return addMovie(S.movieMedia(p.id, p.title, p.year));
  },
  discRemove: (d) => dismiss(JSON.parse(d.p), 'removed'),
  discMore: (d) => {
    const p = JSON.parse(d.p);
    sheet(p.year ? `${p.title} (${p.year})` : p.title, [
      { label: "I've watched it", fn: () => dismiss(p, 'seen') },
      { label: 'Not interested', fn: () => dismiss(p, 'no') },
      ...(p.kind === 'tv' ? [{ label: 'Open show page', fn: () => { location.hash = `#/show/${p.id}`; } }] : []),
    ]);
  },

  playMovie: (d) => playMovieOnTv(JSON.parse(d.m)),
  markMovie: (d) => markMovie(JSON.parse(d.m), true),
  movieMenu: (d) => movieSheet(JSON.parse(d.m)),
  movieUnwatch: (d) => markMovie(JSON.parse(d.m), false),
  movieUnlist: (d) => removeMovie(JSON.parse(d.m)),
  movieNo: (d) => {
    const m = JSON.parse(d.m);
    dismiss({ kind: 'movie', id: m.tmdb, title: m.title, year: m.year }, 'no');
  },
  expand: (d, el) => el.classList.toggle('clamp4'),
  epUnwatch: (d) => markEpisode(+d.id, +d.s, +d.e, false),
  epUpTo: (d) => markUpTo(+d.id, +d.s, +d.e),
  setView: async (d) => {
    app.settings.watchView = d.v;
    await db.kvSet('watch_view', d.v);
    render();
  },
  toggleEp: (d) => markEpisode(+d.id, +d.s, +d.e, !S.isWatched(app.state, S.epKey(+d.id, +d.s, +d.e))),
  epMenu: (d) => {
    const id = +d.id; const s = +d.s; const e = +d.e;
    const done = S.isWatched(app.state, S.epKey(id, s, e));
    const ep = app.seasons.get(`${id}:${s}`)?.episodes?.find((x) => x.episode_number === e);
    sheet(`${showTitle(id)} ${S.se(s, e)}${ep?.name ? ` · ${ep.name}` : ''}`, [
      { label: 'Play on TV', fn: () => playOnTv(id, s, e) },
      { label: 'Play on TV, choosing the source in Seren', fn: () => playOnTv(id, s, e, 'pick') },
      { label: done ? 'Mark unwatched' : 'Mark watched', fn: () => markEpisode(id, s, e, !done) },
      { label: `Mark watched up to ${S.se(s, e)}`, fn: () => markUpTo(id, s, e) },
      { label: `Mark all of season ${s} watched`, fn: () => markSeason(id, s) },
    ], ep?.overview || '');
  },
  markSeason: (d) => markSeason(+d.id, +d.s),
  toggleSeason: (d) => {
    if (app.openSeasons.has(d.key)) app.openSeasons.delete(d.key);
    else app.openSeasons.add(d.key);
    render();
  },
  follow: (d) => follow(+d.id, d.title),
  filter: (d) => { app.showFilter = d.f; render(); },
  seenAll: () => markSeen(S.releases(app.shows, app.tv).fresh.map((r) => r.id)),
  openRelease: (d) => { markSeen([d.rid]); location.hash = `#/show/${d.id}`; },
  syncNow: () => runSync(true),
  signOut: async () => {
    drive.signOut();
    await db.kvSet('google_signed_in', false);
    app.settings.signedInBefore = false;
    app.sync.status = 'off';
    render();
    toast('Signed out on this phone');
  },
};

view.addEventListener('click', (ev) => {
  if (ev.target.closest('[data-stop]')) return;
  const el = ev.target.closest('[data-action]');
  if (el && !el.disabled) {
    ev.preventDefault();
    ev.stopPropagation();
    actions[el.dataset.action]?.(el.dataset, el);
    return;
  }
  const link = ev.target.closest('[data-href]');
  if (link) {
    location.hash = link.dataset.href;
    return;
  }
  const swap = ev.target.closest('a[data-replace]');
  if (swap) {
    ev.preventDefault();
    location.replace(swap.getAttribute('href'));
  }
});

view.addEventListener('change', (ev) => {
  const el = ev.target;
  if (el.dataset.change === 'active') setActive(+el.dataset.id, el.checked);
});

view.addEventListener('input', (ev) => {
  if (ev.target.id === 'q') onSearchInput(ev.target.value);
  if (ev.target.id === 'mq') onMovieSearchInput(ev.target.value);
});

view.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const form = ev.target;
  if (form.dataset.form === 'search' || form.dataset.form === 'msearch') {
    form.querySelector('input').blur();
    return;
  }
  if (form.dataset.form === 'settings') {
    const f = new FormData(form);
    const next = {
      clientId: String(f.get('clientId') || '').trim(),
      tmdbKey: String(f.get('tmdbKey') || '').trim(),
      omdbKey: String(f.get('omdbKey') || '').trim(),
      region: String(f.get('region') || 'US').trim().toUpperCase() || 'US',
      deviceName: String(f.get('deviceName') || 'Phone').trim() || 'Phone',
    };
    await db.kvSet('google_client_id', next.clientId === GOOGLE_CLIENT_ID ? '' : next.clientId);
    await db.kvSet('tmdb_key', next.tmdbKey);
    await db.kvSet('omdb_key', next.omdbKey);
    omdb.setKey(next.omdbKey);
    await db.kvSet('region', next.region);
    await db.kvSet('device_name', next.deviceName);
    Object.assign(app.settings, next);
    tmdb.setKey(next.tmdbKey);
    toast('Saved');
    loadTv();
    render();
  }
});

document.getElementById('syncBtn').addEventListener('click', () => runSync(true));
history.scrollRestoration = 'manual'; // we restore positions ourselves
window.addEventListener('hashchange', () => {
  if (app.lastRoute) app.scrollPos[app.lastRoute] = window.scrollY; // remember where we left this page
  if (route().name !== 'show') app.openedFor = null;
  render();
});
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible') return;
  loadTv();
  if (drive.hasValidToken() && Date.now() - (app.sync.last || 0) > 5 * 60000) runSync(false);
});

// ---------------------------------------------------------------- boot
(async function boot() {
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('./sw.js').catch(() => {});
  await loadSettings();
  await refresh();
  window.tmsStarted = true;
  render();
  loadTv();
  loadMovies();
  if (drive.hasValidToken()) runSync(false);
  else {
    app.sync.status = app.settings.signedInBefore ? 'needs-tap' : 'off';
    renderHeader();
  }
})();
