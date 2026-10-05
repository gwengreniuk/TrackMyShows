"""Fold the event log into the current picture of what has been watched.

Event types:
  watched / unwatched   mark an item
  progress              partial watch (position, total, pct)
  resolve               "raw:<hash> is really <media>" (fixes an unidentified item)
  ignore                hide an item (e.g. junk unidentified entry)
  follow                start tracking a show (even one never played in Kodi, e.g. Netflix)
  hide / unhide         deactivate / reactivate a show
  link / unlink         attach a show to a Premiumize folder ({'pm': {'id', 'name'}})
  watchlist / unwatchlist   add/remove a movie on the "to watch" list (key movie:<tmdb>)
"""
from . import model


class Item:
    def __init__(self, key):
        self.key = key
        self.media = None
        self.raw = None
        self.watched = False
        self.watched_ts = 0.0
        self.plays = 0
        self.progress = None
        self.progress_ts = 0.0
        self.last_ts = 0.0
        self.last_device = None
        self.ignored = False

    @property
    def kind(self):
        return (self.media or {}).get('kind')

    @property
    def in_progress(self):
        return bool(self.progress) and self.progress_ts > self.watched_ts

    def describe(self):
        return model.describe(self.media, self.raw)


class State:
    def __init__(self):
        self.items = {}
        self.hidden_shows = set()
        self.followed = {}  # show_tmdb -> {'title', 'ts'}
        self.links = {}  # show_tmdb -> {'id', 'name'} or None (explicitly unlinked)
        self.watchlist = {}  # movie key -> {'media', 'ts'}
        self.paused = {}  # show_tmdb -> ts paused (started, not watching right now)

    @classmethod
    def build(cls, events):
        state = cls()
        events = sorted(events, key=lambda e: (e.get('ts', 0), e.get('id', '')))
        aliases = {}
        for e in events:
            if e.get('type') == 'resolve' and e.get('media'):
                aliases[e['key']] = e['media']
        for e in events:
            state._apply(e, aliases)
        return state

    def _apply(self, e, aliases):
        type_, key, ts = e.get('type'), e.get('key'), e.get('ts', 0)
        if not key or type_ == 'resolve':
            return
        if key.startswith('show:'):
            try:
                show_id = int(key.split(':', 1)[1])
            except (IndexError, ValueError):
                return
            if type_ == 'link':
                self.links[show_id] = e.get('pm')
            elif type_ == 'unlink':
                self.links[show_id] = None
            elif type_ == 'follow':
                self.followed[show_id] = {'title': (e.get('media') or {}).get('show_title') or '', 'ts': ts}
                self.hidden_shows.discard(show_id)
            elif type_ == 'hide':
                self.hidden_shows.add(show_id)
            elif type_ == 'unhide':
                self.hidden_shows.discard(show_id)
            elif type_ == 'pause':
                self.paused[show_id] = ts
            elif type_ == 'resume':
                self.paused.pop(show_id, None)
            return
        if type_ in ('dismiss', 'undismiss'):  # phone-only: hides a title from Discover
            return
        if type_ in ('watchlist', 'unwatchlist'):
            if type_ == 'watchlist' and e.get('media'):
                self.watchlist[key] = {'media': e['media'], 'ts': ts}
            else:
                self.watchlist.pop(key, None)
            return
        media = e.get('media')
        if key in aliases:
            media = aliases[key]
            key = model.media_key(media)
        item = self.items.get(key)
        if item is None:
            item = self.items[key] = Item(key)
        if media:
            item.media = media
        if e.get('raw') and not item.raw:
            item.raw = e['raw']
        item.last_ts = ts
        item.last_device = e.get('device_name') or item.last_device
        if type_ == 'watched':
            item.watched, item.watched_ts, item.ignored = True, ts, False
            sid = (media or {}).get('show_tmdb') if (media or {}).get('kind') == 'episode' else None
            if sid in self.paused and ts > self.paused[sid]:
                self.paused.pop(sid)  # watching a new episode resumes a paused show
            item.plays += 1
            item.progress = None
        elif type_ == 'unwatched':
            item.watched = False
            item.progress = None
        elif type_ == 'progress':
            item.progress, item.progress_ts, item.ignored = e.get('progress'), ts, False
        elif type_ == 'ignore':
            item.ignored = True

    # ---- queries ------------------------------------------------------
    def _visible(self):
        return (i for i in self.items.values() if not i.ignored)

    def get(self, key):
        return self.items.get(key)

    def is_watched(self, key):
        item = self.items.get(key)
        return bool(item and item.watched and not item.ignored)

    def shows(self):
        """{show_tmdb: {'id', 'title', 'episodes': {(s, e): Item}, 'last_ts', 'followed'}}"""
        shows = {}
        for show_id, f in self.followed.items():
            shows[show_id] = {'id': show_id, 'title': f['title'], 'episodes': {}, 'last_ts': f['ts'], 'followed': True}
        for item in self._visible():
            if item.kind != 'episode':
                continue
            m = item.media
            show = shows.setdefault(m['show_tmdb'], {'id': m['show_tmdb'], 'title': m.get('show_title') or '',
                                                     'episodes': {}, 'last_ts': 0.0, 'followed': False})
            show['episodes'][(m['season'], m['episode'])] = item
            show['last_ts'] = max(show['last_ts'], item.last_ts)
            if m.get('show_trakt'):
                show['trakt'] = m['show_trakt']
            if m.get('show_title'):
                show['title'] = m['show_title']
            if m.get('pm_folder'):
                show['pm_auto'] = m['pm_folder']
        for show_id, show in shows.items():
            # An explicit link/unlink wins over a folder learned from playback.
            show['pm'] = self.links[show_id] if show_id in self.links else show.pop('pm_auto', None)
            show.pop('pm_auto', None)
        return shows

    def movies(self):
        return sorted((i for i in self._visible() if i.kind == 'movie' and i.watched),
                      key=lambda i: i.watched_ts, reverse=True)

    def movies_to_watch(self):
        """Watch-list movies not watched yet, oldest addition first."""
        out = [w for k, w in self.watchlist.items() if not self.is_watched(k)]
        return sorted(out, key=lambda w: w['ts'])

    def recent(self, limit=100):
        items = [i for i in self._visible() if i.watched]
        return sorted(items, key=lambda i: i.watched_ts, reverse=True)[:limit]

    def in_progress(self):
        return sorted((i for i in self._visible() if i.in_progress), key=lambda i: i.progress_ts, reverse=True)

    def unidentified(self):
        return sorted((i for i in self._visible() if i.media is None), key=lambda i: i.last_ts, reverse=True)
