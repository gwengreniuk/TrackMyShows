"""Identify a playing video and write watched/progress events."""
from . import model, resolver, seren


def attach_trakt(media, raw):
    """Remember the Trakt IDs Seren gave us, so we can send you back to Seren later."""
    show_trakt, show_tmdb, item_trakt = seren.ids_from_raw(raw)
    if media['kind'] == 'episode':
        if show_trakt and show_tmdb in (None, media['show_tmdb']):
            media['show_trakt'] = show_trakt
        if raw.get('pm_folder'):
            media['pm_folder'] = raw['pm_folder']
    elif item_trakt and (raw.get('mediatype') or '').lower() == 'movie':
        media['trakt'] = item_trakt
    return media


class Recorder:
    def __init__(self, store, tmdb, log=None):
        self.store = store
        self.tmdb = tmdb
        self.log = log or (lambda msg: None)
        self._memo = {}

    def identify(self, raw):
        rk = model.raw_key(raw)
        if rk in self._memo:
            return self._memo[rk]
        media = None
        try:
            media = resolver.resolve(raw, self.tmdb)
        except Exception as e:  # network trouble: record as unidentified, retried later
            self.log('identify failed: %s' % e)
        if not media:
            return rk, None
        attach_trakt(media, raw)
        result = (model.media_key(media), media)
        self._memo[rk] = result
        return result

    def record(self, type_, raw, progress=None):
        key, media = self.identify(raw)
        event = self.store.add_event(type_, key, media=media, raw=None if media else raw, progress=progress)
        self.log('recorded %s: %s' % (type_, model.describe(media, raw)))
        return event

    def retry_unidentified(self, state):
        """Try again to identify items that failed before (e.g. TMDb was unreachable)."""
        fixed = 0
        for item in state.unidentified():
            if not item.raw:
                continue
            try:
                media = resolver.resolve(item.raw, self.tmdb)
            except Exception as e:
                self.log('retry stopped: %s' % e)
                break
            if media:
                self.store.add_event('resolve', item.key, media=media)
                fixed += 1
        return fixed
