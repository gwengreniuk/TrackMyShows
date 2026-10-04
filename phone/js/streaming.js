// "Open in Netflix / Prime Video on TV" (BritBox and Crave open in Prime Video).
// Netflix and Prime Video IDs come from Wikidata (looked up by TMDb ID, cached); without one,
// the TV opens that app's search for the title.
import { cacheGet, cacheSet } from './db.js';

// app = which TV app opens it. BritBox and Crave are subscribed through Amazon (Prime Video Channels),
// so they open in Prime Video too.
export const SERVICES = [
  { key: 'netflix', app: 'netflix', label: 'Netflix', match: /netflix/i, prop: 'P1874' },
  { key: 'prime', app: 'prime', label: 'Prime Video', match: /amazon prime video|^prime video/i, prop: 'P8055' },
  { key: 'britbox', app: 'prime', label: 'BritBox (Prime Video)', match: /britbox/i, prop: 'P8055' },
  { key: 'crave', app: 'prime', label: 'Crave (Prime Video)', match: /^crave/i, prop: 'P8055' },
];

/** Buttons to show, from TMDb's "where to watch" list: one per TV app. */
export function launchable(providers) {
  const out = [];
  for (const s of SERVICES) {
    if (out.some((o) => o.app === s.app)) continue; // e.g. Prime Video and BritBox both open Prime Video
    if (providers.some((p) => s.match.test(p.provider_name))) out.push(s);
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
        const v = claims[s.prop]?.[0]?.mainsnak?.datavalue?.value;
        if (v) ids[s.app] = String(v);
      }
    }
    await cacheSet(cacheKey, ids);
  }
  return ids[service.app] || '';
}
