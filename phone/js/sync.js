// Same protocol as the Kodi add-on (tms/sync.py): each device writes only its own
// events-<device>.jsonl file and merges everyone else's.
import * as db from './db.js';
import * as drive from './drive.js';

export function parseJsonl(text) {
  const out = [];
  for (const line of text.split('\n')) {
    const t = line.trim();
    if (!t) continue;
    try {
      const e = JSON.parse(t);
      if (e && typeof e === 'object') out.push(e);
    } catch { /* skip bad line */ }
  }
  return out;
}

export async function sync() {
  const me = await db.deviceId();
  const files = await drive.listEventFiles();
  const own = files.filter((f) => f.appProperties?.device === me)
    .sort((a, b) => (a.modifiedTime < b.modifiedTime ? 1 : -1));

  let uploaded = false;
  const seq = await db.kvGet('change_seq', 0);
  if (seq !== (await db.kvGet('synced_seq')) || !own.length) {
    const content = await db.ownEventsJsonl();
    let fileId = own[0]?.id;
    if (fileId) {
      try {
        await drive.updateFile(fileId, content);
      } catch (err) {
        if (err.status !== 404) throw err;
        fileId = null;
      }
    }
    if (!fileId) {
      const name = (await db.kvGet('device_name', 'Phone')).slice(0, 60);
      await drive.createFile(`events-${me}.jsonl`, content, { tms: 'events', device: me, device_name: name });
    }
    await db.kvSet('synced_seq', seq);
    uploaded = true;
  }

  const seen = await db.kvGet('drive_seen', {});
  let imported = 0;
  let others = 0;
  for (const f of files) {
    if (f.appProperties?.device === me) continue;
    others += 1;
    if (seen[f.id] === f.modifiedTime) continue;
    imported += await db.importEvents(parseJsonl(await drive.download(f.id)));
    seen[f.id] = f.modifiedTime;
  }
  await db.kvSet('drive_seen', seen);
  await db.kvSet('last_sync', Date.now());
  return { uploaded, imported, others };
}
