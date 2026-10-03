"""Minimal TMDb client with a persistent cache (the Store's cache table)."""
from urllib.parse import urlencode

from . import net
from .net import HttpError

DAY = 86400


class Tmdb:
    BASE = 'https://api.themoviedb.org/3'

    def __init__(self, key, cache=None, language='en-US', http=net.request):
        self.key = (key or '').strip()
        self.cache = cache
        self.language = language
        self.http = http

    @property
    def available(self):
        return bool(self.key)

    def _get(self, path, params=None, ttl=7 * DAY, cached_only=False):
        if not self.key:
            return None
        params = dict(params or {})
        params['language'] = self.language
        cache_key = 'tmdb:%s?%s' % (path, urlencode(sorted(params.items())))
        if self.cache:
            hit = self.cache.cache_get(cache_key, ttl)
            if hit is not None:
                return hit or None
        if cached_only:
            stale = self.cache.cache_get(cache_key) if self.cache else None
            return stale or None
        headers = {'Accept': 'application/json'}
        if self.key.startswith('eyJ'):  # v4 read access token
            headers['Authorization'] = 'Bearer ' + self.key
        else:
            params['api_key'] = self.key
        try:
            data = self.http('GET', self.BASE + path, params=params, headers=headers)
        except HttpError as e:
            if e.status != 404:
                raise
            data = {}
        except Exception:
            stale = self.cache.cache_get(cache_key) if self.cache else None
            if stale is not None:
                return stale or None
            raise
        if self.cache:
            self.cache.cache_set(cache_key, data or {})
        return data or None

    def find(self, external_id, source):
        return self._get('/find/%s' % external_id, {'external_source': source}, ttl=30 * DAY)

    def movie(self, movie_id, cached_only=False):
        return self._get('/movie/%d' % int(movie_id), ttl=30 * DAY, cached_only=cached_only)

    def tv(self, show_id, cached_only=False):
        # Short TTL: last/next_episode_to_air drive "Where was I?"
        return self._get('/tv/%d' % int(show_id), ttl=DAY // 2, cached_only=cached_only)

    def season(self, show_id, season):
        return self._get('/tv/%d/season/%d' % (int(show_id), int(season)), ttl=DAY // 2)

    def _search(self, kind, query, year=None, year_param=None):
        params = {'query': query, 'include_adult': 'false'}
        if year and year_param:
            params[year_param] = year
        data = self._get('/search/%s' % kind, params, ttl=7 * DAY)
        return (data or {}).get('results') or []

    def search_tv(self, query, year=None):
        return self._search('tv', query, year, 'first_air_date_year')

    def search_movie(self, query, year=None):
        return self._search('movie', query, year, 'year')

    def search_multi(self, query):
        return [r for r in self._search('multi', query) if r.get('media_type') in ('tv', 'movie')]


def image(path, size='w500'):
    return 'https://image.tmdb.org/t/p/%s%s' % (size, path) if path else ''
