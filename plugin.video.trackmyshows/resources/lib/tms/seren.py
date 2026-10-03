"""Links into Seren (plugin.video.seren), which identifies everything by Trakt ID.

Seren accepts a legacy action_args form for episodes ({"item_type": "episode",
"trakt_id": <show trakt id>, "season": n, "episode": n}) and looks up the
episode itself, so we only ever need the show's Trakt ID.
"""
import json
import sqlite3
from urllib.parse import parse_qsl, quote, unquote, urlsplit

ADDON_ID = 'plugin.video.seren'
BASE = 'plugin://%s/' % ADDON_ID
SEREN_DB = 'special://profile/addon_data/plugin.video.seren/traktSync.db'


def _url(action, args):
    # Seren JSON-encodes action_args and url-quotes the whole query; it unquotes
    # once more when parsing, so double quoting matches what Seren itself produces.
    if not isinstance(args, str):
        args = json.dumps(args, sort_keys=True)
    return '%s?action=%s&action_args=%s' % (BASE, action, quote(quote(args, safe=''), safe=''))


def episode_url(show_trakt, season, episode, choose_source=False):
    url = _url('getSources', {'item_type': 'episode', 'trakt_id': int(show_trakt),
                              'season': int(season), 'episode': int(episode)})
    return url + '&source_select=true' if choose_source else url


def movie_url(movie_trakt, choose_source=False):
    url = _url('getSources', {'mediatype': 'movie', 'trakt_id': int(movie_trakt)})
    return url + '&source_select=true' if choose_source else url


def premiumize_folder_url(folder_id):
    return _url('myFilesFolder', {'debrid_provider': 'premiumize', 'id': folder_id})


def pm_folder_from_path(path):
    """If path is a Seren "My Files" Premiumize folder, return {'id', 'name'}."""
    if not path or not path.startswith(BASE):
        return None
    params = dict(parse_qsl(urlsplit(path).query))
    if params.get('action') != 'myFilesFolder' or not params.get('action_args'):
        return None
    try:
        args = json.loads(unquote(params['action_args']))
    except ValueError:
        return None
    if not isinstance(args, dict) or args.get('debrid_provider') != 'premiumize' or not args.get('id'):
        return None
    return {'id': str(args['id']), 'name': args.get('name') or ''}


def show_url(show_trakt):
    return _url('showSeasons', {'mediatype': 'tvshow', 'trakt_id': int(show_trakt)})


def show_search_url(title):
    return _url('showsSearchResults', title)


def movie_search_url(title):
    return _url('moviesSearchResults', title)


def lookup_show_trakt(db_path, tmdb_id):
    """Find a show's Trakt ID in Seren's local database (read-only). None if unknown."""
    try:
        conn = sqlite3.connect('file:%s?mode=ro' % db_path, uri=True, timeout=2)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute('SELECT trakt_id FROM shows WHERE tmdb_id=? LIMIT 1', (int(tmdb_id),)).fetchone()
        return int(row[0]) if row else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def lookup_movie_trakt(db_path, tmdb_id):
    """A movie's Trakt ID from Seren's local database (read-only). None if unknown."""
    try:
        conn = sqlite3.connect('file:%s?mode=ro' % db_path, uri=True, timeout=2)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute('SELECT trakt_id FROM movies WHERE tmdb_id=? LIMIT 1', (int(tmdb_id),)).fetchone()
        return int(row[0]) if row else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def episode_known(db_path, show_trakt, season, episode):
    """True/False if Seren's database has this episode for the show; None if we can't tell."""
    try:
        conn = sqlite3.connect('file:%s?mode=ro' % db_path, uri=True, timeout=2)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute('SELECT 1 FROM episodes WHERE trakt_show_id=? AND season=? AND number=? LIMIT 1',
                           (int(show_trakt), int(season), int(episode))).fetchone()
        return row is not None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def request_refresh(db_path, show_trakt):
    """Set Seren's own needs_milling flag so it re-downloads the show's seasons and episodes from Trakt.

    Seren crashes on an episode it doesn't know yet (its copy of a show can go stale when
    Trakt syncing isn't working), so we nudge it before handing over.
    """
    try:
        conn = sqlite3.connect(db_path, timeout=5)
    except sqlite3.Error:
        return False
    try:
        conn.execute('UPDATE shows SET needs_milling=1 WHERE trakt_id=?', (int(show_trakt),))
        conn.commit()
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def ids_from_raw(raw):
    """Trakt IDs Seren attached to the playing item: (show_trakt, show_tmdb, item_trakt)."""
    ids = {str(k).lower(): v for k, v in (raw.get('uniqueid') or {}).items()}
    window = raw.get('seren_ids') or {}  # Seren's script.trakt.ids window property (show ids for episodes)

    def as_int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    show_trakt = as_int(ids.get('tvshow.trakt'))
    show_tmdb = as_int(ids.get('tvshow.tmdb'))
    if not show_trakt and window.get('trakt'):
        # Only trust the window property if it belongs to this item (it can be stale).
        if show_tmdb is None or as_int(window.get('tmdb')) == show_tmdb:
            show_trakt = as_int(window.get('trakt'))
            show_tmdb = show_tmdb or as_int(window.get('tmdb'))
    return show_trakt, show_tmdb, as_int(ids.get('trakt'))
