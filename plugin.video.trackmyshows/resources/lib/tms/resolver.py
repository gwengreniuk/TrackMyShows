"""Turn what Kodi tells us about the playing video into a TMDb-identified episode or movie.

Order of attempts:
  1. IMDb / TVDB ids (Seren sets these)  -> TMDb /find
  2. TMDb id + title sanity check
  3. Search TMDb by show title / movie title from the metadata
  4. Parse the file name (Premiumize cloud files) and search
"""
import re

from . import model, parser


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _norm(text):
    text = (text or '').lower()
    text = re.sub(r'^the\s+', '', text)
    return re.sub(r'[^a-z0-9]+', '', text)


def titles_match(a, b):
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return True
    return na == nb or na in nb or nb in na


def _year(date):
    return int(date[:4]) if date and len(date) >= 4 and date[:4].isdigit() else None


def _episode(show, season, episode, title=None):
    return model.episode_media(show['id'], show.get('name') or show.get('original_name'), season, episode,
                               title=title, year=_year(show.get('first_air_date')))


def _movie(m):
    return model.movie_media(m['id'], m.get('title') or m.get('original_title'), _year(m.get('release_date')))


def _best(results, query, field):
    for r in results[:5]:
        if _norm(r.get(field)) == _norm(query):
            return r
    return results[0] if results else None


def _search_show(tmdb, query, year=None):
    results = tmdb.search_tv(query, year) if year else []
    if not results:
        results = tmdb.search_tv(query)
    return _best(results, query, 'name')


def _search_movie(tmdb, query, year=None):
    results = tmdb.search_movie(query, year) if year else []
    if not results:
        results = tmdb.search_movie(query)
    return _best(results, query, 'title')


def _from_find(found, tmdb, is_episode, has_se, season, episode, title):
    episodes = found.get('tv_episode_results') or []
    if episodes:
        ep = episodes[0]
        show = tmdb.tv(ep['show_id'])
        if show:
            return _episode(show, ep.get('season_number'), ep.get('episode_number'), ep.get('name') or title)
    shows = found.get('tv_results') or []
    if shows and has_se:
        return _episode(shows[0], season, episode, title)
    movies = found.get('movie_results') or []
    if movies and not is_episode:
        return _movie(movies[0])
    return None


def resolve(raw, tmdb):
    """Return episode/movie media for a raw player description, or None."""
    if not tmdb or not tmdb.available:
        return None
    media_type = (raw.get('mediatype') or '').lower()
    ids = {str(k).lower(): str(v) for k, v in (raw.get('uniqueid') or {}).items() if v}
    imdb_number = str(raw.get('imdbnumber') or '')
    imdb = ids.get('imdb') or (imdb_number if imdb_number.startswith('tt') else None)
    tvdb = ids.get('tvdb')
    tmdb_id = ids.get('tmdb')
    season, episode = _int(raw.get('season')), _int(raw.get('episode'))
    show_title, title = raw.get('showtitle'), raw.get('title')
    has_se = season is not None and season >= 0 and episode is not None and episode > 0
    is_episode = media_type == 'episode' or (has_se and bool(show_title))

    show_tmdb = ids.get('tvshow.tmdb') or str((raw.get('seren_ids') or {}).get('tmdb') or '')
    if is_episode and has_se and show_tmdb.isdigit():  # Seren tags episodes with the show's TMDb id
        show = tmdb.tv(int(show_tmdb))
        if show and titles_match(show.get('name'), show_title):
            return _episode(show, season, episode, title)

    for value, source in ((imdb, 'imdb_id'), (tvdb, 'tvdb_id')):
        if value:
            media = _from_find(tmdb.find(value, source) or {}, tmdb, is_episode, has_se, season, episode, title)
            if media:
                return media

    if tmdb_id and tmdb_id.isdigit():
        if is_episode and has_se:
            show = tmdb.tv(int(tmdb_id))
            if show and titles_match(show.get('name'), show_title):
                return _episode(show, season, episode, title)
        elif not is_episode:
            movie = tmdb.movie(int(tmdb_id))
            if movie and titles_match(movie.get('title'), title):
                return _movie(movie)

    if is_episode and has_se and show_title:
        show = _search_show(tmdb, show_title)
        if show:
            return _episode(show, season, episode, title)
    if media_type == 'movie' and title:
        movie = _search_movie(tmdb, title, _int(raw.get('year')))
        if movie:
            return _movie(movie)

    parsed = parser.parse(raw.get('file'))
    if (not parsed or not parsed.get('kind')) and raw.get('label'):
        from_label = parser.parse(raw['label'])
        if from_label and from_label.get('kind'):
            parsed = from_label
    if parsed and parsed.get('title'):
        if parsed['kind'] == 'episode':
            show = _search_show(tmdb, parsed['title'], parsed.get('year'))
            if show:
                return _episode(show, parsed['season'], parsed['episode'])
        elif parsed['kind'] == 'movie':
            movie = _search_movie(tmdb, parsed['title'], parsed.get('year'))
            if movie:
                return _movie(movie)
    return None
