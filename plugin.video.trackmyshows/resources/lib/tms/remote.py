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

# Streaming apps the phone can open on the TV. Several package names per service because Fire TV and
# Google/Android TV builds differ; the first one installed is used.
STREAMING_APPS = {
    'netflix': {'label': 'Netflix', 'packages': ['com.netflix.ninja', 'com.netflix.mediaclient'],
                'link': 'https://www.netflix.com/title/{id}'},
    'prime': {'label': 'Prime Video', 'packages': ['com.amazon.firebat', 'com.amazon.amazonvideo.livingroom',
                                                   'com.amazon.avod.thirdpartyclient'],
              'link': 'https://watch.amazon.com/detail?asin={id}'},
    'crave': {'label': 'Crave', 'packages': ['ca.bellmedia.cravetv', 'ca.bellmedia.crave'], 'link': None},
    'britbox': {'label': 'BritBox', 'packages': ['com.britbox.tv', 'com.britbox.us.firetv', 'com.britbox.ca',
                                                 'com.britbox.us', 'com.britbox.firetv'], 'link': None},
}
KNOWN_PACKAGES = {p for app in STREAMING_APPS.values() for p in app['packages']}


def parse_command(data):
    try:
        cmd = json.loads(data.decode('utf-8') if isinstance(data, bytes) else data)
    except ValueError:
        return None
    if not isinstance(cmd, dict) or not cmd.get('id'):
        return None
    if cmd.get('action') == 'launch':
        if cmd.get('app') not in STREAMING_APPS:
            return None
        try:
            cmd['ts'] = float(cmd['ts'])
        except (KeyError, TypeError, ValueError):
            return None
        return cmd
    if cmd.get('action') != 'play':
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


def launch_app(cmd, installed_packages):
    """For an 'open in Netflix' style command: (package, uri or None) or (None, reason)."""
    app = STREAMING_APPS[cmd['app']]
    package = next((p for p in app['packages'] if p in installed_packages), None)
    if not package:
        return None, '%s is not installed on this TV' % app['label']
    service_id = str(cmd.get('service_id') or '').strip()
    uri = app['link'].format(id=service_id) if app['link'] and service_id else None
    return package, uri


def android_builtin(package, uri=None):
    """Kodi builtin that opens an Android app, optionally at a deep link."""
    if uri:
        return 'StartAndroidActivity("%s","android.intent.action.VIEW","","%s")' % (package, uri)
    return 'StartAndroidActivity("%s")' % package


def packages_from_listing(files):
    """Package names from Kodi's androidapp://sources/apps/ directory listing."""
    out = []
    for f in files or []:
        path = (f.get('file') or '').rstrip('/')
        name = path.rsplit('/', 1)[-1]
        if name.endswith('.png'):
            name = name[:-4]
        if name:
            out.append(name)
    return out


def heartbeat(device_id, name, version, ack=None, now=None, apps=None):
    return {'device': device_id, 'name': name, 'version': version,
            'last_seen': time.time() if now is None else now, 'ack': ack, 'apps': apps or []}
