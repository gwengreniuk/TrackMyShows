// TMDb client with an IndexedDB cache. The key is entered in Settings and stays on the phone.
import { cacheGet, cacheSet } from './db.js';

const BASE = 'https://api.themoviedb.org/3';
const HOUR = 3600000;
let apiKey = '';
const memory = new Map();

export const setKey = (key) => { apiKey = (key || '').trim(); };
export const hasKey = () => !!apiKey;
export const img = (path, size = 'w342') => (path ? `https://image.tmdb.org/t/p/${size}${path}` : '');

async function get(path, params = {}, ttl = 12 * HOUR, { force = false } = {}) {
  const url = new URL(BASE + path);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  const cacheKey = 'tmdb:' + url.pathname + url.search;
  if (!force) {
    if (memory.has(cacheKey)) return memory.get(cacheKey);
    const hit = await cacheGet(cacheKey, ttl);
    if (hit !== undefined) {
      memory.set(cacheKey, hit);
      return hit;
    }
  }
  if (!apiKey) throw new Error('No TMDb API key set');
  const headers = { Accept: 'application/json' };
  if (apiKey.startsWith('eyJ')) headers.Authorization = 'Bearer ' + apiKey;
  else url.searchParams.set('api_key', apiKey);
  let data;
  try {
    const res = await fetch(url, { headers });
    if (res.status === 404) data = null;
    else if (!res.ok) throw new Error(`TMDb ${res.status}`);
    else data = await res.json();
  } catch (err) {
    const stale = await cacheGet(cacheKey);
    if (stale !== undefined) return stale;
    throw err;
  }
  memory.set(cacheKey, data);
  await cacheSet(cacheKey, data);
  return data;
}

/** Show details incl. seasons, last/next episode and streaming providers. */
export const tv = (id, opts) => get(`/tv/${id}`, { append_to_response: 'watch/providers,external_ids' }, 12 * HOUR, opts);
export const season = (id, s) => get(`/tv/${id}/season/${s}`, {}, 12 * HOUR);
export const movie = (id, opts) => get(`/movie/${id}`, { append_to_response: 'watch/providers,credits' }, 7 * 24 * HOUR, opts);
export const tvCredits = (id) => get(`/tv/${id}/aggregate_credits`, {}, 7 * 24 * HOUR);
export const person = (id) => get(`/person/${id}`, { append_to_response: 'combined_credits' }, 7 * 24 * HOUR);
export const searchMovie = async (q) => ((await get('/search/movie', { query: q, include_adult: 'false' }, 24 * HOUR)) || {}).results || [];
export const searchTv = async (q) => ((await get('/search/tv', { query: q, include_adult: 'false' }, 24 * HOUR)) || {}).results || [];

export const DISCOVER_LISTS = {
  trending_tv: { label: 'Trending TV', kind: 'tv' },
  now_playing: { label: 'In theatres', kind: 'movie' },
  upcoming_movies: { label: 'Upcoming movies', kind: 'movie' },
  top_tv: { label: 'Top rated TV', kind: 'tv' },
  top_movies: { label: 'Top rated movies', kind: 'movie' },
};

// Genre groups shown in Filters, with TMDb's ids for movies and for TV (their genre lists differ).
export const GENRES = [
  { name: 'Drama', movie: [18], tv: [18] },
  { name: 'Crime', movie: [80], tv: [80] },
  { name: 'Comedy', movie: [35], tv: [35] },
  { name: 'Action & Adventure', movie: [28, 12], tv: [10759] },
  { name: 'Thriller', movie: [53], tv: [] },
  { name: 'Mystery', movie: [9648], tv: [9648] },
  { name: 'Sci-Fi & Fantasy', movie: [878, 14], tv: [10765] },
  { name: 'Horror', movie: [27], tv: [] },
  { name: 'Romance', movie: [10749], tv: [] },
  { name: 'War & History', movie: [10752, 36], tv: [10768] },
  { name: 'Western', movie: [37], tv: [37] },
  { name: 'Documentary', movie: [99], tv: [99] },
  { name: 'Animation', movie: [16], tv: [16] },
  { name: 'Family & Kids', movie: [10751], tv: [10751, 10762] },
  { name: 'Reality', movie: [], tv: [10764] },
  { name: 'Music', movie: [10402], tv: [] },
];

// Content tag groups -> TMDb keyword ids. Community-tagged, so best effort.
export const CONTENT_TAGS = [
  { key: 'smoking', label: 'Smoking', ids: [919, 302633, 3170, 11333] },
  { key: 'violence', label: 'Violence & gore', ids: [312898, 367145, 10292, 13006] },
  { key: 'drugs', label: 'Drugs', ids: [14964, 11494, 1803, 2150, 2671] },
  { key: 'nudity', label: 'Nudity & sex', ids: [281741, 359980, 380475, 354470, 329280] },
  { key: 'suicide', label: 'Suicide & self-harm', ids: [236, 1252, 233130] },
  { key: 'assault', label: 'Sexual assault', ids: [570, 190327] },
  { key: 'childabuse', label: 'Child abuse', ids: [516] },
  { key: 'domestic', label: 'Domestic violence', ids: [11925] },
  { key: 'animals', label: 'Animal cruelty', ids: [205685] },
  { key: 'alcohol', label: 'Alcoholism', ids: [7464] },
];

// US movie certifications; NR = not rated
export const CERTS = ['G', 'PG', 'PG-13', 'R', 'NC-17', 'NR'];

export const filtersActive = (f) => !!f && !!((f.include || []).length || (f.exclude || []).length || f.minRating
  || Object.keys(f.certs || {}).length || Object.keys(f.content || {}).length);

export function applyFilters(params, kind, f) {
  if (!filtersActive(f)) return;
  const ids = (names) => names.flatMap((n) => GENRES.find((g) => g.name === n)?.[kind] || []);
  const inc = ids(f.include || []);
  if (inc.length) params.with_genres = inc.join('|'); // any of the chosen genres
  const exc = [...new Set([...(params.without_genres ? String(params.without_genres).split(',').map(Number) : []),
                           ...ids(f.exclude || [])])];
  if (exc.length) params.without_genres = exc.join(',');
  if (f.minRating) {
    params['vote_average.gte'] = Math.max(Number(params['vote_average.gte'] || 0), f.minRating);
    params['vote_count.gte'] = Math.max(Number(params['vote_count.gte'] || 0), 50);
  }
  const certs = f.certs || {};
  const withC = CERTS.filter((c) => certs[c] === 'with');
  const withoutC = CERTS.filter((c) => certs[c] === 'without');
  if (kind === 'movie' && (withC.length || withoutC.length)) {
    // "with" = only these ratings; "without" = every other rating (TMDb has no exclude for ratings)
    const allowed = withC.length ? withC : CERTS.filter((c) => !withoutC.includes(c));
    params.certification_country = 'US';
    params.certification = allowed.join('|');
  }
  const content = f.content || {};
  const idsFor = (mode) => CONTENT_TAGS.filter((t) => content[t.key] === mode).flatMap((t) => t.ids);
  const withK = idsFor('with');
  const withoutK = idsFor('without');
  if (withK.length) params.with_keywords = withK.join('|'); // titles tagged with any of them
  if (withoutK.length) params.without_keywords = withoutK.join(','); // hide anything tagged with any of them
}

/** One page of a Discover list: {results, totalPages}. */
export async function discover(list, page = 1, region = 'US', filters = null) {
  const isoDay = (ms) => new Date(ms).toISOString().slice(0, 10);
  const DAY_MS = 24 * HOUR;
  const today = isoDay(Date.now());
  const kind = DISCOVER_LISTS[list].kind;
  // Age ratings are US ratings; TMDb only matches them against US releases, so use US dates then.
  if (kind === 'movie' && Object.keys(filters?.certs || {}).length) region = 'US';
  let path;
  const params = { page };
  if (list === 'trending_tv') {
    if (filtersActive(filters)) {
      // TMDb's trending list can't be filtered: use popular shows with episodes airing in the last month
      path = '/discover/tv';
      Object.assign(params, { sort_by: 'popularity.desc', 'air_date.gte': isoDay(Date.now() - 30 * DAY_MS),
                              'air_date.lte': today, without_genres: '10763,10767' });
    } else {
      path = '/trending/tv/week';
    }
  } else if (list === 'now_playing') {
    path = '/discover/movie';
    Object.assign(params, { region, sort_by: 'popularity.desc', with_release_type: '2|3',
                            'release_date.gte': isoDay(Date.now() - 42 * DAY_MS), 'release_date.lte': today,
                            'primary_release_date.gte': isoDay(Date.now() - 365 * DAY_MS) }); // skip re-releases of old films
  } else if (list === 'upcoming_movies') {
    path = '/discover/movie';
    Object.assign(params, { region, sort_by: 'popularity.desc', with_release_type: '2|3',
                            'release_date.gte': isoDay(Date.now() + DAY_MS), 'release_date.lte': isoDay(Date.now() + 180 * DAY_MS),
                            'primary_release_date.gte': isoDay(Date.now() - 365 * DAY_MS) }); // skip re-releases of old films
  } else if (list === 'top_tv') {
    // Highly rated at any age; popularity order so well-loved titles come before obscure ones
    path = '/discover/tv';
    Object.assign(params, { sort_by: 'popularity.desc', 'vote_average.gte': 8, 'vote_count.gte': 300,
                            'first_air_date.lte': today, without_genres: '10763,10764,10767,16' });
  } else {
    path = '/discover/movie';
    Object.assign(params, { sort_by: 'popularity.desc', 'vote_average.gte': 7.6, 'vote_count.gte': 1500,
                            'primary_release_date.lte': today });
  }
  applyFilters(params, kind, filters);
  const data = (await get(path, params, 6 * HOUR)) || {};
  return { results: data.results || [], totalPages: Math.min(data.total_pages || 1, 500) };
}

/** Cached-only lookup (no network). */
export async function tvCached(id) {
  const url = new URL(BASE + `/tv/${id}`);
  url.searchParams.set('append_to_response', 'watch/providers,external_ids');
  const key = 'tmdb:' + url.pathname + url.search;
  if (memory.has(key)) return memory.get(key);
  const hit = await cacheGet(key);
  if (hit !== undefined) memory.set(key, hit);
  return hit;
}

export function providers(tvData, region) {
  const r = tvData?.['watch/providers']?.results?.[region];
  if (!r) return [];
  const seen = new Set();
  return [...(r.flatrate || []), ...(r.free || []), ...(r.ads || [])].filter((p) => {
    if (seen.has(p.provider_id)) return false;
    seen.add(p.provider_id);
    return true;
  });
}
