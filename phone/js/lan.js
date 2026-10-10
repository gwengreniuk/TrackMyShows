// Direct remote control of a Kodi box on the home network, through the TrackMyShows add-on's
// HTTPS bridge (tms/bridge.py). The box's address and secret token come from its Drive heartbeat.

export const hasLan = (tv) => !!(tv && tv.lan && tv.lan.ip && tv.lan.port && tv.lan.token);
export const certUrl = (tv) => `https://${tv.lan.ip}:${tv.lan.port}/`;

export async function rpc(tv, method, params, timeoutMs = 4000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`https://${tv.lan.ip}:${tv.lan.port}/rpc`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-TMS-Token': tv.lan.token },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, ...(params ? { params } : {}) }),
      signal: ctrl.signal,
    });
    if (res.status === 403) throw new Error('The TV rejected the connection (token changed). Sync and try again.');
    if (!res.ok) throw new Error(`TV error ${res.status}`);
    const data = await res.json();
    if (data?.error) throw new Error(data.error.message || 'Kodi error');
    return data?.result;
  } finally {
    clearTimeout(timer);
  }
}

export const action = (tv, name) => rpc(tv, 'Input.ExecuteAction', { action: name });
export const sendText = (tv, text, done = true) => rpc(tv, 'Input.SendText', { text, done });
export const home = (tv) => rpc(tv, 'GUI.ActivateWindow', { window: 'home' });
