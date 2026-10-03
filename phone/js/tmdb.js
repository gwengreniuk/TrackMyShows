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
export const tv = (id, opts) => get(`/tv/${id}`, { append_to_response: 'watch/providers' }, 12 * HOUR, opts);
export const season = (id, s) => get(`/tv/${id}/season/${s}`, {}, 12 * HOUR);
export const movie = (id, opts) => get(`/movie/${id}`, { append_to_response: 'watch/providers' }, 7 * 24 * HOUR, opts);
export const searchMovie = async (q) => ((await get('/search/movie', { query: q, include_adult: 'false' }, 24 * HOUR)) || {}).results || [];
export const searchTv = async (q) => ((await get('/search/tv', { query: q, include_adult: 'false' }, 24 * HOUR)) || {}).results || [];

/** Cached-only lookup (no network). */
export async function tvCached(id) {
  const url = new URL(BASE + `/tv/${id}`);
  url.searchParams.set('append_to_response', 'watch/providers');
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
