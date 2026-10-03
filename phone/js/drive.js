// Google Drive via Google Identity Services (browser token flow) and the Drive REST API.
// Same drive.file scope and file layout as the Kodi add-on, so both see the same files.

const SCOPE = 'https://www.googleapis.com/auth/drive.file';
const API = 'https://www.googleapis.com/drive/v3';
const UPLOAD = 'https://www.googleapis.com/upload/drive/v3';
const TOKEN_KEY = 'tms_google_token';

export class AuthNeeded extends Error {
  constructor(msg = 'Google sign-in needed') { super(msg); this.name = 'AuthNeeded'; }
}

let client = null;
let clientFor = '';
let pending = null;
let token = null;
try { token = JSON.parse(localStorage.getItem(TOKEN_KEY) || 'null'); } catch { token = null; }

function saveToken(t) {
  token = t;
  try {
    if (t) localStorage.setItem(TOKEN_KEY, JSON.stringify(t));
    else localStorage.removeItem(TOKEN_KEY);
  } catch { /* storage unavailable */ }
}

export const hasValidToken = () => !!token && token.exp > Date.now() + 60000;
export const googleLoaded = () => !!window.google?.accounts?.oauth2;

function ensureClient(clientId) {
  if (client && clientFor === clientId) return;
  client = window.google.accounts.oauth2.initTokenClient({
    client_id: clientId,
    scope: SCOPE,
    callback: (r) => {
      const p = pending;
      pending = null;
      if (!p) return;
      if (r.error) p.reject(new Error(r.error_description || r.error));
      else {
        saveToken({ access_token: r.access_token, exp: Date.now() + Number(r.expires_in || 3600) * 1000 });
        p.resolve();
      }
    },
    error_callback: (e) => {
      const p = pending;
      pending = null;
      if (p) p.reject(new Error(e?.type === 'popup_closed' ? 'Sign-in window was closed' : e?.message || 'Sign-in failed'));
    },
  });
  clientFor = clientId;
}

/** Must be called from a tap (it may open a Google popup). */
export function signIn(clientId) {
  if (!googleLoaded()) return Promise.reject(new Error('Google sign-in did not load. Check your connection.'));
  if (!clientId) return Promise.reject(new Error('No Google client ID configured (Settings).'));
  ensureClient(clientId);
  return new Promise((resolve, reject) => {
    pending = { resolve, reject };
    client.requestAccessToken({ prompt: '' });
  });
}

export function signOut() {
  // Only forget the token on this phone; revoking would also sign out the Kodi boxes.
  saveToken(null);
}

async function api(method, url, { params, body, headers = {}, text = false } = {}) {
  if (!hasValidToken()) throw new AuthNeeded();
  const u = new URL(url);
  for (const [k, v] of Object.entries(params || {})) u.searchParams.set(k, v);
  const res = await fetch(u, { method, body, headers: { ...headers, Authorization: 'Bearer ' + token.access_token } });
  if (res.status === 401) {
    saveToken(null);
    throw new AuthNeeded('Google sign-in expired');
  }
  if (!res.ok) {
    const err = new Error(`Google Drive error ${res.status}: ${(await res.text()).slice(0, 200)}`);
    err.status = res.status;
    throw err;
  }
  if (text) return res.text();
  return res.status === 204 ? null : res.json();
}

async function folderId() {
  const found = await api('GET', API + '/files', { params: {
    q: "appProperties has { key='tms' and value='folder' } and trashed = false",
    fields: 'files(id)', orderBy: 'createdTime', spaces: 'drive' } });
  if (found.files?.length) return found.files[0].id;
  const created = await api('POST', API + '/files', {
    params: { fields: 'id' },
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: 'TrackMyShows', mimeType: 'application/vnd.google-apps.folder',
                           appProperties: { tms: 'folder' } }),
  });
  return created.id;
}

export const listEventFiles = () => listFiles('events');

/** Files this app created with appProperties tms=<kind> ('events', 'device', 'remote'). */
export async function listFiles(kind) {
  const files = [];
  let page = null;
  do {
    const params = { q: `appProperties has { key='tms' and value='${kind}' } and trashed = false`,
                     fields: 'nextPageToken,files(id,name,modifiedTime,appProperties)', pageSize: 100, spaces: 'drive' };
    if (page) params.pageToken = page;
    const r = await api('GET', API + '/files', { params });
    files.push(...(r.files || []));
    page = r.nextPageToken;
  } while (page);
  return files;
}

export async function createFile(name, content, appProperties) {
  const meta = { name, parents: [await folderId()], appProperties, mimeType: 'text/plain' };
  const boundary = 'tms' + crypto.randomUUID().replace(/-/g, '');
  const body = `--${boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n${JSON.stringify(meta)}\r\n` +
    `--${boundary}\r\nContent-Type: text/plain\r\n\r\n${content}\r\n--${boundary}--\r\n`;
  return api('POST', UPLOAD + '/files', { params: { uploadType: 'multipart', fields: 'id,modifiedTime' },
                                          headers: { 'Content-Type': `multipart/related; boundary=${boundary}` }, body });
}

export const updateFile = (id, content) =>
  api('PATCH', `${UPLOAD}/files/${id}`, { params: { uploadType: 'media', fields: 'id,modifiedTime' },
                                          headers: { 'Content-Type': 'text/plain' }, body: content });

export const download = (id) => api('GET', `${API}/files/${id}`, { params: { alt: 'media' }, text: true });
