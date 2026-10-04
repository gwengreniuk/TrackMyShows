// Rotten Tomatoes / IMDb / Metacritic scores from OMDb (free key: omdbapi.com/apikey.aspx).
// Looked up only when a movie or show page is opened, and cached for a week.
import { cacheGet, cacheSet } from './db.js';

const WEEK = 7 * 24 * 3600 * 1000;
let apiKey = '';
const memory = new Map();

export const setKey = (key) => { apiKey = (key || '').trim(); };
export const hasKey = () => !!apiKey;

/** {rt: '92%', imdb: '8.3', metacritic: '76'} (missing fields omitted), or null. */
export async function ratings(imdbId) {
  if (!apiKey || !imdbId) return null;
  if (memory.has(imdbId)) return memory.get(imdbId);
  const hit = await cacheGet('omdb:' + imdbId, WEEK);
  if (hit !== undefined) {
    memory.set(imdbId, hit);
    return hit;
  }
  const res = await fetch(`https://www.omdbapi.com/?i=${encodeURIComponent(imdbId)}&apikey=${encodeURIComponent(apiKey)}`);
  if (res.status === 401) throw new Error('OMDb key not accepted (check Settings)');
  const data = await res.json();
  let out = null;
  if (data.Response === 'True') {
    out = {};
    const rt = (data.Ratings || []).find((r) => r.Source === 'Rotten Tomatoes');
    if (rt) out.rt = rt.Value;
    if (data.imdbRating && data.imdbRating !== 'N/A') out.imdb = data.imdbRating;
    if (data.Metascore && data.Metascore !== 'N/A') out.metacritic = data.Metascore;
  }
  memory.set(imdbId, out);
  await cacheSet('omdb:' + imdbId, out);
  return out;
}
