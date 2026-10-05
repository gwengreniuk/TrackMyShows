// Pure logic shared in spirit with the Kodi add-on (tms/state.py, tms/nextup.py).
// Both sides read and write the same event log, so the rules must match.

export const mediaKey = (m) =>
  m.kind === 'episode' ? `tv:${m.show_tmdb}:${m.season}:${m.episode}` : `movie:${m.tmdb}`;
export const showKey = (id) => `show:${id}`;
export const epKey = (showId, s, e) => `tv:${showId}:${s}:${e}`;
export const se = (s, e) => `S${String(s).padStart(2, '0')}E${String(e).padStart(2, '0')}`;
export const cmp = (a, b) => a[0] - b[0] || a[1] - b[1];

export const movieMedia = (tmdb, title, year) => {
  const m = { kind: 'movie', tmdb: +tmdb, title: title || '' };
  if (year) m.year = +year;
  return m;
};

export function episodeMedia(showId, showTitle, season, episode, title) {
  const m = { kind: 'episode', show_tmdb: +showId, show_title: showTitle || '', season: +season, episode: +episode };
  if (title) m.title = title;
  return m;
}

function newItem(key) {
  return { key, media: null, raw: null, watched: false, watchedTs: 0, plays: 0, progress: null,
           progressTs: 0, lastTs: 0, lastDevice: null, ignored: false };
}

export function buildState(events) {
  const evs = [...events].sort((a, b) => (a.ts || 0) - (b.ts || 0) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  const aliases = new Map();
  for (const e of evs) if (e.type === 'resolve' && e.media) aliases.set(e.key, e.media);
  const items = new Map();
  const hidden = new Set();
  const followed = new Map();
  const watchlist = new Map(); // movie key -> {media, ts}
  const dismissed = new Map(); // 'show:<id>' / 'movie:<id>' -> reason ('seen' | 'no'); hidden from Discover
  const paused = new Map(); // show id -> ts paused (started but not watching right now)
  for (const e of evs) {
    let { type, key } = e;
    const ts = e.ts || 0;
    if (!key || type === 'resolve') continue;
    if (type === 'dismiss' || type === 'undismiss') {
      if (type === 'dismiss') dismissed.set(key, e.reason || 'no');
      else dismissed.delete(key);
      continue;
    }
    if (key.startsWith('show:')) { // show-level events (link/unlink are Kodi-only)
      const id = parseInt(key.split(':')[1], 10);
      if (!id) continue;
      if (type === 'follow') {
        followed.set(id, { title: e.media?.show_title || '', ts });
        hidden.delete(id);
      } else if (type === 'hide') hidden.add(id);
      else if (type === 'unhide') hidden.delete(id);
      else if (type === 'pause') paused.set(id, ts);
      else if (type === 'resume') paused.delete(id);
      continue;
    }
    if (type === 'watchlist' || type === 'unwatchlist') {
      if (type === 'watchlist' && e.media) watchlist.set(key, { media: e.media, ts });
      else watchlist.delete(key);
      continue;
    }
    let media = e.media || null;
    if (aliases.has(key)) {
      media = aliases.get(key);
      key = mediaKey(media);
    }
    let it = items.get(key);
    if (!it) items.set(key, (it = newItem(key)));
    if (media) it.media = media;
    if (e.raw && !it.raw) it.raw = e.raw;
    it.lastTs = ts;
    it.lastDevice = e.device_name || it.lastDevice;
    if (type === 'watched') {
      Object.assign(it, { watched: true, watchedTs: ts, ignored: false, progress: null });
      it.plays += 1;
      // Watching a new episode of a paused show means you're watching it again
      if (media?.kind === 'episode' && paused.has(media.show_tmdb) && ts > paused.get(media.show_tmdb)) {
        paused.delete(media.show_tmdb);
      }
    } else if (type === 'unwatched') {
      Object.assign(it, { watched: false, progress: null });
    } else if (type === 'progress') {
      Object.assign(it, { progress: e.progress, progressTs: ts, ignored: false });
    } else if (type === 'ignore') {
      it.ignored = true;
    }
  }
  return { items, hidden, followed, watchlist, dismissed, paused };
}

/** Should a TMDb result be left out of Discover (already followed, listed, watched or dismissed)? */
export function alreadyKnown(state, shows, kind, id) {
  if (kind === 'tv') {
    return state.dismissed.has(showKey(id)) || !!shows.get(id)?.tracked;
  }
  const key = `movie:${id}`;
  return state.dismissed.has(key) || state.watchlist.has(key) || isWatched(state, key);
}

/** Movies on the list that aren't watched yet, oldest addition first. */
export function moviesToWatch(state) {
  return [...state.watchlist.entries()]
    .filter(([key]) => !isWatched(state, key))
    .map(([key, w]) => ({ key, media: w.media, ts: w.ts }))
    .sort((a, b) => a.ts - b.ts);
}

/** Every watched movie (from the list or tracked by Kodi), newest first. */
export function moviesWatched(state) {
  return [...state.items.values()]
    .filter((it) => it.media?.kind === 'movie' && it.watched && !it.ignored)
    .sort((a, b) => b.watchedTs - a.watchedTs);
}

export const isWatched = (state, key) => {
  const it = state.items.get(key);
  return !!(it && it.watched && !it.ignored);
};

/** Map show id -> {id, title, episodes: Map('s:e' -> item), lastTs, followed, hidden, active, tracked} */
export function collectShows(state) {
  const shows = new Map();
  const get = (id, title) => {
    let s = shows.get(id);
    if (!s) shows.set(id, (s = { id, title: title || '', episodes: new Map(), lastTs: 0, followed: false }));
    return s;
  };
  for (const [id, f] of state.followed) {
    const s = get(id, f.title);
    s.followed = true;
    s.lastTs = Math.max(s.lastTs, f.ts);
  }
  for (const it of state.items.values()) {
    if (it.ignored || it.media?.kind !== 'episode') continue;
    const m = it.media;
    const s = get(m.show_tmdb, m.show_title);
    if (m.show_title) s.title = m.show_title;
    s.episodes.set(`${m.season}:${m.episode}`, it);
    s.lastTs = Math.max(s.lastTs, it.lastTs);
  }
  for (const s of shows.values()) {
    s.hidden = state.hidden.has(s.id);
    s.tracked = s.followed || [...s.episodes.values()].some((i) => i.watched || i.progress);
    s.active = s.tracked && !s.hidden;
    s.paused = s.active && state.paused.has(s.id);
    s.status = !s.active ? 'off' : s.paused ? 'paused' : 'watching';
  }
  return shows;
}

export function watchedPairs(show) {
  const out = [];
  for (const [k, it] of show.episodes) {
    if (!it.watched) continue;
    const [s, e] = k.split(':').map(Number);
    if (s > 0) out.push([s, e]);
  }
  return out.sort(cmp);
}

const pair = (ep) => (ep && ep.season_number != null && ep.episode_number != null
  ? [ep.season_number, ep.episode_number] : null);
const ENDED = ['Ended', 'Canceled', 'Cancelled'];

export function seasonCounts(tv) {
  return (tv?.seasons || [])
    .filter((s) => s.season_number > 0 && s.episode_count > 0)
    .map((s) => [s.season_number, s.episode_count])
    .sort((a, b) => a[0] - b[0]);
}

/** Where are we in this show? tv = TMDb /tv/{id} details or null. */
export function nextUp(show, tv) {
  const watched = watchedPairs(show);
  const wset = new Set(watched.map((p) => p.join(':')));
  const last = watched.length ? watched[watched.length - 1] : null;
  const entry = { last, next: null, nextAirDate: null, status: 'unknown', watchedCount: watched.length,
                  airedCount: null, airedWatched: null };
  if (!tv) {
    entry.next = last ? [last[0], last[1] + 1] : [1, 1];
    return entry;
  }
  const seasons = seasonCounts(tv);
  const from = last || [1, 0];
  let cand = null;
  outer: for (const [sn, cnt] of seasons) {
    if (sn < from[0]) continue;
    for (let e = 1; e <= cnt; e++) {
      if (cmp([sn, e], from) > 0 && !wset.has(`${sn}:${e}`)) { cand = [sn, e]; break outer; }
    }
  }
  const L = pair(tv.last_episode_to_air);
  const N = tv.next_episode_to_air;
  if (L) {
    entry.airedCount = seasons.filter(([sn]) => sn < L[0]).reduce((a, [, c]) => a + c, 0) + L[1];
    entry.airedWatched = watched.filter((p) => cmp(p, L) <= 0).length;
  }
  if (cand && L && cmp(cand, L) <= 0) {
    entry.status = 'available';
    entry.next = cand;
  } else if (ENDED.includes(tv.status) && !N) {
    entry.status = 'finished';
  } else {
    entry.status = 'caught_up';
    if (N) {
      entry.next = pair(N);
      entry.nextAirDate = N.air_date || null;
    }
  }
  return entry;
}

const DAY = 86400000;
const daysSince = (date, today) => (today - Date.parse(date + 'T00:00:00')) / DAY;

/**
 * New seasons / episodes for active shows, plus what's coming up.
 * Returns {fresh: [...], upcoming: [...]}; each has a stable id for "seen" tracking.
 */
export function releases(shows, tvById, today = Date.now(), { seasonWindow = 120, episodeWindow = 14 } = {}) {
  const fresh = [];
  const upcoming = [];
  for (const show of shows.values()) {
    if (!show.active) continue;
    const tv = tvById.get(show.id);
    if (!tv) continue;
    const title = tv.name || show.title;
    const watched = watchedPairs(show);
    const maxW = watched.length ? watched[watched.length - 1] : null;
    const L = tv.last_episode_to_air;
    if (L && L.air_date) {
      const premiere = (tv.seasons || []).find((s) => s.season_number === L.season_number)?.air_date;
      const age = premiere ? daysSince(premiere, today) : Infinity;
      if (L.season_number > (maxW ? maxW[0] : 0) && age >= 0 && age <= seasonWindow) {
        fresh.push({ id: `season:${show.id}:${L.season_number}`, kind: 'season', showId: show.id, title,
                     season: L.season_number, date: premiere, poster: tv.poster_path });
      } else if (maxW && L.season_number === maxW[0] && cmp([L.season_number, L.episode_number], maxW) > 0 &&
                 daysSince(L.air_date, today) <= episodeWindow) {
        fresh.push({ id: `ep:${show.id}:${L.season_number}:${L.episode_number}`, kind: 'episode', showId: show.id,
                     title, season: L.season_number, episode: L.episode_number, date: L.air_date,
                     poster: tv.poster_path });
      }
    }
    const N = tv.next_episode_to_air;
    if (N && N.air_date) {
      upcoming.push({ id: `up:${show.id}:${N.season_number}:${N.episode_number}`,
                      kind: N.episode_number === 1 ? 'premiere' : 'next', showId: show.id, title,
                      season: N.season_number, episode: N.episode_number, date: N.air_date,
                      poster: tv.poster_path });
    }
  }
  fresh.sort((a, b) => (a.date < b.date ? 1 : -1));
  upcoming.sort((a, b) => (a.date < b.date ? -1 : 1));
  return { fresh, upcoming };
}
