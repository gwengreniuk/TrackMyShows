"""Tiny HTTP helper on top of urllib (no third-party dependencies)."""
import json
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = 'TrackMyShows/0.1 (Kodi add-on)'


class HttpError(Exception):
    def __init__(self, status, body, url):
        super().__init__('HTTP %s for %s: %s' % (status, url.split('?')[0], body[:300]))
        self.status = status
        self.body = body

    def json(self):
        try:
            data = json.loads(self.body)
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}


def request(method, url, params=None, data=None, json_body=None, headers=None, timeout=20, raw=False):
    if params:
        url += ('&' if '?' in url else '?') + urllib.parse.urlencode(params)
    headers = dict(headers or {})
    headers.setdefault('User-Agent', USER_AGENT)
    body = None
    if json_body is not None:
        body = json.dumps(json_body).encode('utf-8')
        headers.setdefault('Content-Type', 'application/json; charset=utf-8')
    elif isinstance(data, dict):
        body = urllib.parse.urlencode(data).encode('utf-8')
        headers.setdefault('Content-Type', 'application/x-www-form-urlencoded')
    elif data is not None:
        body = data if isinstance(data, bytes) else data.encode('utf-8')
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read()
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read().decode('utf-8', 'replace'), url)
    if raw:
        return content
    if not content:
        return None
    return json.loads(content.decode('utf-8'))
