"""Guess show/season/episode or movie/year from a file name or URL.

Used for Premiumize cloud files and anything else that arrives without
proper metadata, e.g. "The.Bear.S02E05.1080p.WEB.x264.mkv".
"""
import re
from urllib.parse import unquote, urlsplit

VIDEO_EXTS = {'mkv', 'mp4', 'avi', 'm4v', 'mov', 'wmv', 'ts', 'm2ts', 'webm', 'mpg', 'mpeg', 'flv', 'iso', 'strm'}

_SEP = r'[\s._\-]*'
_NB = r'(?<![A-Za-z0-9])'  # "not preceded by a letter/digit" (\b treats _ as a word char)

_EPISODE_PATTERNS = [
    # S01E02, s1e2, S01.E02, S01E01E02, S01E01-E02
    re.compile(r'^(?P<pre>.*?)' + _NB + r'S(?P<s>\d{1,2})' + _SEP + r'E(?P<e>\d{1,3})'
               r'(?:' + _SEP + r'E(?P<e2>\d{1,3}))?(?!\d)', re.I),
    # 1x02 (but not 1920x1080 or x264)
    re.compile(r'^(?P<pre>.*?)(?<!\d)(?P<s>\d{1,2})x(?P<e>\d{2,3})(?!\d)', re.I),
    # Season 1 Episode 2
    re.compile(r'^(?P<pre>.*?)' + _NB + r'season' + _SEP + r'(?P<s>\d{1,2})' + _SEP +
               r'(?:episode|ep|e)' + _SEP + r'(?P<e>\d{1,3})(?!\d)', re.I),
]
# "E05.mkv" / "Episode 5.mkv" inside a season folder
_EPISODE_ONLY = re.compile(r'^(?:e|ep|episode)' + _SEP + r'(?P<e>\d{1,3})(?!\d)', re.I)
_SEASON_IN_FOLDER = re.compile(_NB + r'(?:S|season' + _SEP + r')(?P<s>\d{1,2})(?!\d)', re.I)
# Greedy title so "Blade.Runner.2049.2017" picks 2017 and "2001.A.Space.Odyssey.1968" picks 1968
_MOVIE_YEAR = re.compile(r'^(?P<title>.+)[\s._(\[]+(?P<year>(?:19|20)\d{2})(?=[\s._)\]]|$)')
_TRAILING_YEAR = re.compile(r'^(?P<title>.+?)\s+(?P<year>(?:19|20)\d{2})$')
_QUALITY = re.compile(r'\b(?:2160p|1080p|720p|480p|4k|uhd|hdr|web[\s-]?dl|webrip|web|bluray|blu-ray|brrip|'
                      r'hdtv|dvdrip|x264|x265|h264|h265|hevc|remux|proper|repack)\b.*$', re.I)


def _split(path):
    path = (path or '').split('|')[0]
    if '://' in path:
        path = urlsplit(path).path
    path = unquote(path).rstrip('/\\')
    parts = [p for p in re.split(r'[\\/]+', path) if p]
    if not parts:
        return '', ''
    return parts[-1], parts[-2] if len(parts) > 1 else ''


def _stem(name):
    if '.' in name and name.rsplit('.', 1)[1].lower() in VIDEO_EXTS:
        return name.rsplit('.', 1)[0]
    return name


def clean(text):
    text = re.sub(r'\[[^\]]*\]', ' ', text or '')    # [release group]
    text = re.sub(r'[._()]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip(' -')


def _title_year(text):
    m = _TRAILING_YEAR.match(text)
    if m:
        return m.group('title').strip(' -'), int(m.group('year'))
    return text, None


def _episode(title, year, season, first, last=None):
    episodes = list(range(first, last + 1)) if last and last > first else [first]
    return {'kind': 'episode', 'title': title, 'year': year, 'season': season,
            'episode': first, 'episodes': episodes}


def season_of(name):
    """Season number from a folder name like "Show.S02.1080p" or "Season 2", else None."""
    m = _SEASON_IN_FOLDER.search(name or '')
    return int(m.group('s')) if m else None


def is_video(name):
    return '.' in (name or '') and name.rsplit('.', 1)[1].lower() in VIDEO_EXTS


def parse(path):
    """Return a dict with kind 'episode', 'movie' or None (title only), or None for empty input."""
    name, parent = _split(path)
    if not name:
        return None
    stem = _stem(name)

    for pattern in _EPISODE_PATTERNS:
        m = pattern.match(stem)
        if not m:
            continue
        title, year = _title_year(clean(m.group('pre')))
        if not title and parent:
            fm = _SEASON_IN_FOLDER.search(parent)
            title, year = _title_year(clean(parent[:fm.start()] if fm else parent))
        e2 = m.groupdict().get('e2')
        return _episode(title, year, int(m.group('s')), int(m.group('e')), int(e2) if e2 else None)

    m = _EPISODE_ONLY.match(stem)
    if m and parent:
        fm = _SEASON_IN_FOLDER.search(parent)
        if fm:
            title, year = _title_year(clean(parent[:fm.start()]))
            return _episode(title, year, int(fm.group('s')), int(m.group('e')))

    for candidate in (stem, parent):
        m = _MOVIE_YEAR.match(candidate or '')
        if m:
            title = clean(m.group('title'))
            if title:
                return {'kind': 'movie', 'title': title, 'year': int(m.group('year'))}

    return {'kind': None, 'title': clean(_QUALITY.sub('', clean(stem)))}
