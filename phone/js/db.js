// IndexedDB storage: the merged event log plus a small key/value store (settings, cache, sync state).

let dbPromise = null;

function open() {
  if (!dbPromise) {
    dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open('trackmyshows', 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        db.createObjectStore('events', { keyPath: 'id' });
        db.createObjectStore('kv');
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }
  return dbPromise;
}

async function run(store, mode, body) {
  const db = await open();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(store, mode);
    let result;
    body(tx.objectStore(store), (v) => { result = v; });
    tx.oncomplete = () => resolve(result);
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });
}

export const kvGet = (key, fallback = null) =>
  run('kv', 'readonly', (s, done) => {
    const r = s.get(key);
    r.onsuccess = () => done(r.result === undefined ? fallback : r.result);
  });

export const kvSet = (key, value) => run('kv', 'readwrite', (s) => s.put(value, key));
export const kvDel = (key) => run('kv', 'readwrite', (s) => s.delete(key));

export const allEvents = () =>
  run('events', 'readonly', (s, done) => {
    const r = s.getAll();
    r.onsuccess = () => done(r.result);
  });

export async function deviceId() {
  let id = await kvGet('device_id');
  if (!id) {
    id = crypto.randomUUID().replace(/-/g, '');
    await kvSet('device_id', id);
  }
  return id;
}

/** Create and store events written by this phone. specs: [{type, key, ...fields}] */
export async function addEvents(specs) {
  const device = await deviceId();
  const deviceName = await kvGet('device_name', 'Phone');
  const now = Date.now() / 1000; // seconds, same as the Kodi side
  const events = specs.map((spec, i) => {
    const e = { v: 1, id: crypto.randomUUID().replace(/-/g, ''), device, device_name: deviceName, ts: now + i * 1e-3 };
    for (const [k, v] of Object.entries(spec)) if (v !== undefined && v !== null) e[k] = v;
    return e;
  });
  await run('events', 'readwrite', (s) => events.forEach((e) => s.put(e)));
  await kvSet('change_seq', (await kvGet('change_seq', 0)) + 1);
  return events;
}

/** Merge events from other devices; resolves to the number that were new. */
export function importEvents(events) {
  return run('events', 'readwrite', (s, done) => {
    let added = 0;
    done(0);
    for (const e of events) {
      if (!e || !e.id || !e.device || e.ts == null || !e.type || !e.key) continue;
      const r = s.get(e.id);
      r.onsuccess = () => {
        if (!r.result) {
          s.put(e);
          added += 1;
          done(added);
        }
      };
    }
  });
}

export async function ownEventsJsonl() {
  const me = await deviceId();
  const mine = (await allEvents()).filter((e) => e.device === me).sort((a, b) => a.ts - b.ts);
  return mine.map((e) => JSON.stringify(e)).join('\n') + (mine.length ? '\n' : '');
}

export async function cacheGet(key, maxAgeMs) {
  const hit = await kvGet('cache:' + key);
  if (!hit) return undefined;
  if (maxAgeMs != null && Date.now() - hit.ts > maxAgeMs) return undefined;
  return hit.v;
}

export const cacheSet = (key, v) => kvSet('cache:' + key, { ts: Date.now(), v });
