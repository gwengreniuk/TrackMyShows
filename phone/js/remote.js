// Play on TV: send a command to a Kodi box through Google Drive (see tms/remote.py on the Kodi side).
import * as db from './db.js';
import * as drive from './drive.js';

const ONLINE_MS = 5 * 60 * 1000; // Kodi writes a heartbeat every 2 minutes while it's running
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Kodi boxes that have checked in, newest first: [{device, name, last_seen, online, ack, fileId}] */
export async function devices() {
  const files = await drive.listFiles('device');
  const list = await Promise.all(files.map(async (f) => {
    try {
      const d = JSON.parse(await drive.download(f.id));
      return { ...d, fileId: f.id, online: Date.now() - d.last_seen * 1000 < ONLINE_MS };
    } catch {
      return null;
    }
  }));
  return list.filter(Boolean).sort((a, b) => b.last_seen - a.last_seen);
}

/** fields: {target, show_tmdb, season, episode, title, mode: 'auto' | 'pick' | 'seren'} */
export async function send(fields) {
  const me = await db.deviceId();
  const cmd = { id: crypto.randomUUID().replace(/-/g, ''), ts: Date.now() / 1000, from: me, action: 'play', ...fields };
  const body = JSON.stringify(cmd);
  let fileId = await db.kvGet('remote_file_id');
  if (fileId) {
    try {
      await drive.updateFile(fileId, body);
    } catch (err) {
      if (err.status !== 404) throw err;
      fileId = null;
    }
  }
  if (!fileId) {
    const created = await drive.createFile(`remote-${me}.json`, body, { tms: 'remote', device: me });
    await db.kvSet('remote_file_id', created.id);
  }
  return cmd;
}

/** Wait for the TV to report back on a command; resolves to its ack or null on timeout. */
export async function waitForAck(device, cmdId, timeoutMs = 30000) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    await sleep(2500);
    try {
      const d = JSON.parse(await drive.download(device.fileId));
      if (d.ack?.id === cmdId) return d.ack;
    } catch { /* keep waiting */ }
  }
  return null;
}
