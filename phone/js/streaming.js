// "Open in Netflix / Prime Video / Crave / BritBox on TV".
// Netflix and Prime Video IDs come from Wikidata (looked up by TMDb ID, cached); Crave and BritBox
// have no public IDs, so those just open the app.
import { cacheGet, cacheSet } from './db.js';

export const SERVICES = [
  { key: 'netflix', label: 'Netflix', match: /netflix/i, prop: 'P1874' },
  { key: 'prime', label: 'Prime Video', match: /amazon prime video|^prime video/i, prop: 'P8055' },
  { key: 'crave', label: 'Crave', match: /^crave/i, prop: null },
  { key: 'britbox', label: 'BritBox', match: /britbox/i, prop: null },
];

/** Services we can open on the TV, from TMDb's "where to watch" list. */
export function launchable(providers) {
  const out = [];
  for (const s of SERVICES) {
    if (providers.some((p) => s.match.test(p.provider_name)) && !out.includes(s)) out.push(s);
  }
  return out;
}

const WEEK = 7 * 24 * 3600 * 1000;

async function wikidata(params) {
  const url = new URL('https://www.wikidata.org/w/api.php');
  for (const [k, v] of Object.entries({ ...params, format: 'json', origin: '*' })) url.searchParams.set(k, v);
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Wikidata ${res.status}`);
  return res.json();
}

/** The title's ID in a service (e.g. Netflix 80057281), or '' if Wikidata doesn't have one. */
export async function serviceId(service, kind, tmdbId) {
  if (!service.prop) return '';
  const cacheKey = `svc:${kind}:${tmdbId}`;
  let ids = await cacheGet(cacheKey, WEEK);
  if (ids === undefined) {
    ids = {};
    const tmdbProp = kind === 'tv' ? 'P4983' : 'P4947';
    const search = await wikidata({ action: 'query', list: 'search', srsearch: `haswbstatement:${tmdbProp}=${tmdbId}`, srlimit: 1 });
    const qid = search.query?.search?.[0]?.title;
    if (qid) {
      const ent = await wikidata({ action: 'wbgetentities', ids: qid, props: 'claims' });
      const claims = ent.entities?.[qid]?.claims || {};
      for (const s of SERVICES) {
        if (!s.prop) continue;
        const v = claims[s.prop]?.[0]?.mainsnak?.datavalue?.value;
        if (v) ids[s.key] = String(v);
      }
    }
    await cacheSet(cacheKey, ids);
  }
  return ids[service.key] || '';
}
