"""Work out "where was I?" for every show: the next unwatched, already-aired episode."""

ENDED = ('Ended', 'Canceled', 'Cancelled')
_RANK = {'available': 0, 'unknown': 1, 'caught_up': 2, 'finished': 3}
PAUSED_RANK = 10  # paused shows go after everything you're currently watching


def _pair(ep):
    if not ep or ep.get('season_number') is None or ep.get('episode_number') is None:
        return None
    return int(ep['season_number']), int(ep['episode_number'])


def _first_unwatched_after(seasons, last, watched):
    for season, count in seasons:
        if season < last[0]:
            continue
        for ep in range(1, count + 1):
            if (season, ep) > last and (season, ep) not in watched:
                return season, ep
    return None


def compute(state, tmdb, include_hidden=False):
    entries = []
    for show_id, show in state.shows().items():
        if show_id in state.hidden_shows and not include_hidden:
            continue
        watched = {se for se, item in show['episodes'].items() if item.watched and se[0] > 0}
        if not watched and not show.get('followed'):
            continue
        last = max(watched) if watched else None
        entry = {'show_tmdb': show_id, 'show_title': show['title'], 'poster': None, 'last': last,
                 'last_ts': show['last_ts'], 'watched_count': len(watched), 'next': None,
                 'next_title': None, 'next_air_date': None, 'status': 'unknown',
                 'hidden': show_id in state.hidden_shows, 'paused': show_id in getattr(state, 'paused', {})}
        info = None
        if tmdb and tmdb.available:
            try:
                info = tmdb.tv(show_id)
            except Exception:
                info = None
        if not info:
            entry['next'] = (last[0], last[1] + 1) if last else (1, 1)  # best guess without TMDb
            entries.append(entry)
            continue

        entry['show_title'] = info.get('name') or entry['show_title']
        entry['poster'] = info.get('poster_path')
        seasons = sorted((int(s['season_number']), int(s.get('episode_count') or 0))
                         for s in info.get('seasons') or [] if (s.get('season_number') or 0) > 0)
        candidate = _first_unwatched_after(seasons, last or (1, 0), watched)
        last_aired = _pair(info.get('last_episode_to_air'))
        upcoming = info.get('next_episode_to_air')

        if candidate and last_aired and candidate <= last_aired:
            entry['status'] = 'available'
            entry['next'] = candidate
            try:
                season = tmdb.season(show_id, candidate[0]) or {}
                ep = next((x for x in season.get('episodes') or [] if x.get('episode_number') == candidate[1]), None)
                if ep:
                    entry['next_title'] = ep.get('name')
            except Exception:
                pass
        elif info.get('status') in ENDED and not upcoming:
            entry['status'] = 'finished'
        else:
            entry['status'] = 'caught_up'
            if upcoming:
                entry['next'] = _pair(upcoming)
                entry['next_air_date'] = upcoming.get('air_date')
        entries.append(entry)

    entries.sort(key=lambda x: (PAUSED_RANK if x['paused'] else 0, _RANK[x['status']], -x['last_ts']))
    return entries
