"""Premiumize cloud: find the file for an episode inside a folder you linked to a show.

Uses the Premiumize login Seren already has (its premiumize.token setting), or the
official Premiumize add-on's API key (its "pin" setting).
"""
import json
import re
from urllib.parse import parse_qsl, unquote, urlsplit

from . import net, parser
from .net import HttpError

API = 'https://www.premiumize.me/api'
MAX_FOLDERS_PER_LEVEL = 12
MAX_DEPTH = 2


class PremiumizeError(Exception):
    pass


class Premiumize:
    def __init__(self, token, http=net.request, apikey=''):
        self.token = (token or '').strip()
        self.apikey = (apikey or '').strip()
        self.http = http

    @property
    def available(self):
        return bool(self.token or self.apikey)

    def _call(self, method, path, data=None, params=None):
        if not self.available:
            raise PremiumizeError('Premiumize is not set up. Sign in to Premiumize in Seren first.')
        headers = {}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        else:
            params = dict(params or {}, apikey=self.apikey)
        try:
            result = self.http(method, API + path, data=data, params=params, headers=headers)
        except HttpError as e:
            raise PremiumizeError('Premiumize error (HTTP %s)' % e.status)
        if not isinstance(result, dict) or result.get('status') != 'success':
            raise PremiumizeError((result or {}).get('message') or 'Premiumize request failed')
        return result

    def list_folder(self, folder_id=None):
        """{'content': [...], 'name': ..., 'parent_id': ...}; items have id, name, type ('file'/'folder'), link."""
        return self._call('POST', '/folder/list', data={'id': folder_id} if folder_id else {})

    def search(self, query):
        return self._call('GET', '/folder/search', params={'q': query}).get('content') or []

    def find_episode(self, folder_id, season, episode, folder_name=''):
        """Return the Premiumize file item for SxxEyy under folder_id, or None.

        Looks in the folder, then its subfolders (season folders first). If the linked folder
        is itself a season folder (e.g. "Show.S02"), also tries its parent for other seasons.
        """
        listing = self.list_folder(folder_id)
        name = folder_name or listing.get('name') or ''
        hit = self._search(listing, name, int(season), int(episode), 0)
        if hit is None and parser.season_of(name) is not None and listing.get('parent_id'):
            parent = self.list_folder(listing['parent_id'])
            hit = self._search(parent, parent.get('name') or '', int(season), int(episode), 0)
        return hit

    def discover(self, title, year, season, episode):
        """Find a folder for an unlinked show by searching the cloud for its title.

        Returns (folder, file) or None. Folders naming a different year are skipped, so
        a 2008 remake's folder is never used for the 1982 original.
        """
        want = _norm(title)
        if not want:
            return None
        candidates = []
        for folder in self.search(title):
            name = folder.get('name', '')
            if folder.get('type') != 'folder' or want not in _norm(name):
                continue
            years = {int(y) for y in re.findall(r'(?<!\d)(19\d\d|20\d\d)(?!\d)', name)}
            if year and years and int(year) not in years:
                continue
            candidates.append((0 if year and int(year) in years else 1, len(name), folder))
        for _, _, folder in sorted(candidates, key=lambda c: (c[0], c[1]))[:3]:
            hit = self.find_episode(folder['id'], season, episode, folder.get('name', ''))
            if hit:
                return folder, hit
        return None

    def find_movie(self, title, year=None):
        """Find a movie file in the cloud by title (and year when the name has one). Returns the file or None."""
        want = _norm(title)
        if not want:
            return None

        def year_ok(name):
            years = {int(y) for y in re.findall(r'(?<!\d)(19\d\d|20\d\d)(?!\d)', name)}
            return not (year and years and int(year) not in years)

        def title_ok(name):
            p = parser.parse(name) or {}
            got = _norm(p.get('title') or name)
            return got == want or (got.startswith(want) and len(got) - len(want) <= 4)

        results = self.search(title)
        files = [r for r in results if r.get('type') == 'file' and parser.is_video(r.get('name', ''))
                 and 'sample' not in r['name'].lower() and title_ok(r['name']) and year_ok(r['name'])]
        if files:
            return max(files, key=lambda f: f.get('size') or 0)
        for folder in [r for r in results if r.get('type') == 'folder' and title_ok(r.get('name', ''))
                       and year_ok(r.get('name', ''))][:3]:
            videos = [c for c in self.list_folder(folder['id']).get('content') or []
                      if c.get('type') == 'file' and parser.is_video(c.get('name', ''))
                      and 'sample' not in c['name'].lower()]
            if videos:
                return max(videos, key=lambda f: f.get('size') or 0)
        return None

    def _search(self, listing, folder_name, season, episode, depth):
        content = listing.get('content') or []
        for item in content:
            if item.get('type') == 'file' and parser.is_video(item.get('name', '')):
                p = parser.parse('%s/%s' % (folder_name, item['name']))
                if p and p.get('kind') == 'episode' and p['season'] == season and episode in p['episodes']:
                    return item
        if depth >= MAX_DEPTH:
            return None

        def rank(folder):
            s = parser.season_of(folder.get('name', ''))
            return 0 if s == season else 1 if s is None else 2

        folders = sorted((c for c in content if c.get('type') == 'folder'), key=rank)
        for folder in folders[:MAX_FOLDERS_PER_LEVEL]:
            hit = self._search(self.list_folder(folder['id']), folder.get('name', ''), season, episode, depth + 1)
            if hit:
                return hit
        return None


def _norm(text):
    text = re.sub(r'^the\s+', '', (text or '').lower())
    return re.sub(r'[^a-z0-9]+', '', text)


def folder_from_path(path, folder_name=''):
    """Premiumize folder behind a Kodi container path, from Seren's My Files or the official add-on."""
    if not path or not path.startswith('plugin://'):
        return None
    parts = urlsplit(path)
    params = dict(parse_qsl(parts.query))
    if parts.netloc == 'plugin.video.seren' and params.get('action') == 'myFilesFolder':
        try:
            args = json.loads(unquote(params.get('action_args', '')))
        except ValueError:
            return None
        if isinstance(args, dict) and args.get('debrid_provider') == 'premiumize' and args.get('id'):
            return {'id': str(args['id']), 'name': args.get('name') or folder_name}
    if parts.netloc == 'plugin.video.premiumizemetv' and params.get('mode') == 'file_list' and params.get('folder_id'):
        return {'id': str(params['folder_id']), 'name': folder_name}
    return None
