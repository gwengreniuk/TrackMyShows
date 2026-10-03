"""Play on TV: commands sent from the phone app through Google Drive.

The phone writes remote-<phone id>.json: {"id", "ts", "target", "action": "play",
"show_tmdb", "season", "episode", "title", "mode"}. Each Kodi box polls those
files, runs commands addressed to it, and reports back in its own
device-<id>.json (which doubles as an "I'm online" heartbeat).
"""
import json
import time
from urllib.parse import urlencode


COMMAND_MAX_AGE = 180  # seconds; older commands are ignored (e.g. Kodi was closed when it was sent)
MODES = ('auto', 'seren', 'pick')


def parse_command(data):
    try:
        cmd = json.loads(data.decode('utf-8') if isinstance(data, bytes) else data)
    except ValueError:
        return None
    if not isinstance(cmd, dict) or cmd.get('action') != 'play' or not cmd.get('id'):
        return None
    try:
        if cmd.get('kind') == 'movie':
            cmd['movie_tmdb'] = int(cmd['movie_tmdb'])
        else:
            cmd['kind'] = 'episode'
            cmd['show_tmdb'], cmd['season'], cmd['episode'] = int(cmd['show_tmdb']), int(cmd['season']), int(cmd['episode'])
        cmd['ts'] = float(cmd['ts'])
    except (KeyError, TypeError, ValueError):
        return None
    if cmd.get('mode') not in MODES:
        cmd['mode'] = 'auto'
    return cmd


def should_run(cmd, device_id, handled, now=None):
    now = time.time() if now is None else now
    return (cmd['id'] not in handled and cmd.get('target') in (device_id, 'any')
            and -60 <= now - cmd['ts'] <= COMMAND_MAX_AGE)


def plugin_url(**query):
    return 'plugin://plugin.video.trackmyshows/?' + urlencode(query)


def launch_target(show, cmd, seren_installed, trakt):
    """Decide how to play: ('smart'|'seren', play url), ('search', folder url) or (None, reason).

    'auto' hands over to TrackMyShows' smartplay: Premiumize first (linked or found), then Seren.
    """
    if cmd['mode'] != 'auto' and not seren_installed:
        return None, 'Seren is not installed on this box'
    if cmd['kind'] == 'movie':
        return 'smart', plugin_url(action='smartmovie', movie=cmd['movie_tmdb'], title=cmd.get('title') or '',
                                   year=cmd.get('year') or '', mode=cmd['mode'])
    sid, s, e, title, mode = cmd['show_tmdb'], cmd['season'], cmd['episode'], cmd.get('title') or '', cmd['mode']
    # smartplay checks Seren's copy of the show and handles Premiumize/Seren/search itself
    return 'smart', plugin_url(action='smartplay', show=sid, s=s, e=e, title=title, mode=mode)


def heartbeat(device_id, name, version, ack=None, now=None):
    return {'device': device_id, 'name': name, 'version': version,
            'last_seen': time.time() if now is None else now, 'ack': ack}
