"""Shapes and keys shared by every module.

An *item key* identifies one watchable thing:
  tv:<show tmdb id>:<season>:<episode>
  movie:<tmdb id>
  raw:<hash>          something we could not identify yet
"""
import hashlib
import re
from urllib.parse import unquote, urlsplit


def episode_media(show_tmdb, show_title, season, episode, title=None, year=None):
    m = {'kind': 'episode', 'show_tmdb': int(show_tmdb), 'show_title': show_title or '',
         'season': int(season), 'episode': int(episode)}
    if title:
        m['title'] = title
    if year:
        m['year'] = int(year)
    return m


def movie_media(tmdb, title, year=None):
    m = {'kind': 'movie', 'tmdb': int(tmdb), 'title': title or ''}
    if year:
        m['year'] = int(year)
    return m


def media_key(media):
    if media['kind'] == 'episode':
        return 'tv:%d:%d:%d' % (media['show_tmdb'], media['season'], media['episode'])
    return 'movie:%d' % media['tmdb']


def show_key(show_tmdb):
    return 'show:%d' % int(show_tmdb)


def file_basename(path):
    if not path:
        return ''
    path = path.split('|')[0]
    if '://' in path:
        path = urlsplit(path).path
    path = unquote(path).rstrip('/\\')
    return re.split(r'[\\/]', path)[-1]


def raw_key(raw):
    if raw.get('showtitle') and raw.get('episode') not in (None, -1):
        fp = '%s|%s|%s' % (raw['showtitle'], raw.get('season'), raw.get('episode'))
    else:
        fp = file_basename(raw.get('file')) or raw.get('label') or raw.get('title') or repr(sorted(raw.items()))
    return 'raw:' + hashlib.sha1(fp.lower().encode('utf-8')).hexdigest()[:16]


def se(season, episode):
    return 'S%02dE%02d' % (season, episode)


def describe(media=None, raw=None):
    if media:
        if media['kind'] == 'episode':
            return '%s %s' % (media.get('show_title') or 'Unknown show', se(media['season'], media['episode']))
        year = media.get('year')
        return '%s (%s)' % (media.get('title'), year) if year else media.get('title') or 'Unknown movie'
    raw = raw or {}
    if raw.get('showtitle'):
        return '%s S%sE%s' % (raw['showtitle'], raw.get('season', '?'), raw.get('episode', '?'))
    return file_basename(raw.get('file')) or raw.get('label') or raw.get('title') or 'Unknown video'
