"""Background service: watches playback, records history, syncs with Google Drive."""
import collections
import time

import json

import xbmc
import xbmcgui

from . import kodi, premiumize, remote, seren
from .gdrive import AuthError
from .net import HttpError
from .recorder import Recorder
from .state import State
from .sync import sync
from .tracker import Tracker

RETRY_UNIDENTIFIED_EVERY = 6 * 3600
MIN_SECONDS_BETWEEN_SYNCS = 20
REMOTE_POLL_SECONDS = 6
HEARTBEAT_SECONDS = 120


class Player(xbmc.Player):
    """Kodi callbacks only queue events; the main loop does the work."""

    def __init__(self, queue):
        super().__init__()
        self.queue = queue

    def onAVStarted(self):
        self.queue.append('start')

    def onPlayBackStopped(self):
        self.queue.append('stop')

    def onPlayBackEnded(self):
        self.queue.append('stop')

    def onPlayBackError(self):
        self.queue.append('stop')


class Monitor(xbmc.Monitor):
    def __init__(self, queue):
        super().__init__()
        self.queue = queue

    def onSettingsChanged(self):
        self.queue.append('settings')

    def onNotification(self, sender, method, data):
        if sender == kodi.ADDON_ID and method.endswith('sync'):
            self.queue.append('sync')


def capture_raw(player):
    """Everything Kodi knows about the playing video (works for Seren, Premiumize and local files)."""
    raw = {'uniqueid': {}}
    players = kodi.jsonrpc('Player.GetActivePlayers') or []
    player_id = next((p['playerid'] for p in players if p.get('type') == 'video'), None)
    if player_id is not None:
        result = kodi.jsonrpc('Player.GetItem', {'playerid': player_id, 'properties': [
            'title', 'showtitle', 'season', 'episode', 'year', 'uniqueid', 'imdbnumber', 'file']}) or {}
        item = result.get('item') or {}
        raw.update({'mediatype': item.get('type'), 'title': item.get('title'), 'showtitle': item.get('showtitle'),
                    'season': item.get('season'), 'episode': item.get('episode'), 'year': item.get('year'),
                    'imdbnumber': item.get('imdbnumber'), 'file': item.get('file'), 'label': item.get('label')})
        raw['uniqueid'].update(item.get('uniqueid') or {})
    try:
        tag = player.getVideoInfoTag()
        fallback = {'mediatype': tag.getMediaType(), 'title': tag.getTitle(), 'showtitle': tag.getTVShowTitle(),
                    'season': tag.getSeason(), 'episode': tag.getEpisode(), 'year': tag.getYear(),
                    'imdbnumber': tag.getIMDBNumber()}
        for key, value in fallback.items():
            if raw.get(key) in (None, '', -1, 'unknown'):
                raw[key] = value
        for key in ('imdb', 'tmdb', 'tvdb'):
            value = tag.getUniqueID(key)
            if value:
                raw['uniqueid'].setdefault(key, value)
    except RuntimeError:
        pass
    if not raw.get('file'):
        try:
            raw['file'] = player.getPlayingFile()
        except RuntimeError:
            pass
    pm_folder = premiumize.folder_from_path(xbmc.getInfoLabel('Container.FolderPath'),
                                            xbmc.getInfoLabel('Container.FolderName'))
    if pm_folder:  # played from a Premiumize folder (Seren My Files or the Premiumize add-on): remember it
        raw['pm_folder'] = pm_folder
    try:  # Seren publishes the show's ids here for Trakt scrobblers
        seren_ids = json.loads(xbmcgui.Window(10000).getProperty('script.trakt.ids') or '{}')
        if isinstance(seren_ids, dict) and seren_ids:
            raw['seren_ids'] = seren_ids
    except ValueError:
        pass
    return {k: v for k, v in raw.items() if v not in (None, '', -1, {}, 'unknown')}


class Remote:
    """Play-on-TV commands from the phone, and this box's online heartbeat."""

    def __init__(self, store, monitor):
        self.store = store
        self.monitor = monitor
        self.seen = {}
        self.handled = store.meta_get('remote_handled') or []
        self.last_ack = None
        self.last_poll = 0.0
        self.last_beat = 0.0
        self.version = kodi.addon().getAddonInfo('version')
        self.apps = self.installed_apps()

    @staticmethod
    def installed_apps():
        """Which streaming apps this box has (Android only), so the phone can say what's available."""
        if not xbmc.getCondVisibility('System.Platform.Android'):
            return []
        try:
            listing = kodi.jsonrpc('Files.GetDirectory', {'directory': 'androidapp://sources/apps/', 'media': 'files'})
            names = remote.packages_from_listing((listing or {}).get('files'))
        except Exception as e:
            kodi.debug('could not list Android apps: %s' % e)
            return []
        return sorted(set(names) & remote.KNOWN_PACKAGES)

    def tick(self, drive, now):
        if now - self.last_poll >= REMOTE_POLL_SECONDS:
            self.last_poll = now
            self.poll(drive)
        if now - self.last_beat >= HEARTBEAT_SECONDS:
            self.beat(drive)

    def poll(self, drive):
        me = self.store.device_id()
        for f in drive.list_files('remote'):
            if self.seen.get(f['id']) == f.get('modifiedTime'):
                continue
            self.seen[f['id']] = f.get('modifiedTime')
            cmd = remote.parse_command(drive.download(f['id']))
            if cmd and remote.should_run(cmd, me, self.handled):
                self.run(drive, cmd)

    def run(self, drive, cmd):
        self.handled = (self.handled + [cmd['id']])[-50:]
        self.store.meta_set('remote_handled', self.handled)
        if cmd.get('action') == 'launch':
            return self.launch(drive, cmd)
        installed = bool(xbmc.getCondVisibility('System.HasAddon(%s)' % seren.ADDON_ID))
        if cmd['kind'] == 'movie':
            kind, target = remote.launch_target(None, cmd, installed, None)
            label = cmd.get('title') or 'Movie'
        else:
            show = State.build(self.store.events()).shows().get(cmd['show_tmdb'])
            kind, target = remote.launch_target(show, cmd, installed, None)
            label = '%s S%02dE%02d' % (cmd.get('title') or 'Show', cmd['season'], cmd['episode'])
        kodi.log('phone asked to play %s via %s' % (label, kind))
        if kind and xbmc.getCondVisibility('System.Platform.Android'):
            xbmc.executebuiltin('StartAndroidActivity(org.xbmc.kodi)')  # bring Kodi to the front
            self.monitor.waitForAbort(1.5)
        if kind == 'smart':
            xbmc.executebuiltin('PlayMedia(%s)' % target)
            kodi.notify('Playing %s (from your phone)' % label)
            message = 'Starting playback'
        else:
            message = target
            kodi.notify(message)
        self.last_ack = {'id': cmd['id'], 'ts': time.time(), 'status': kind or 'error', 'message': message}
        self.beat(drive)

    def launch(self, drive, cmd):
        app = remote.STREAMING_APPS[cmd['app']]
        if not self.apps:
            self.apps = self.installed_apps()
        package, uri = remote.launch_app(cmd, self.apps)
        if package:
            if xbmc.Player().isPlaying():
                xbmc.Player().stop()
            xbmc.executebuiltin(remote.android_builtin(package, uri))
            what = cmd.get('title') or 'the app'
            message = 'Opening %s in %s' % (what, app['label']) if uri else 'Opened %s - search for %s there' % (app['label'], what)
            status = 'launched'
            kodi.log('phone opened %s (%s)' % (app['label'], uri or 'app home'))
        else:
            message, status = uri, 'error'  # uri holds the reason here
            kodi.notify(message)
        self.last_ack = {'id': cmd['id'], 'ts': time.time(), 'status': status, 'message': message}
        self.beat(drive)

    def beat(self, drive):
        self.last_beat = time.time()
        me = self.store.device_id()
        body = json.dumps(remote.heartbeat(me, self.store.device_name, self.version, self.last_ack,
                                           apps=self.apps)).encode('utf-8')
        file_id = self.store.meta_get('device_file_id')
        if file_id:
            try:
                drive.update_file(file_id, body)
                return
            except HttpError as e:
                if e.status != 404:
                    raise
        created = drive.create_file('device-%s.json' % me, body, {'tms': 'device', 'device': me})
        self.store.meta_set('device_file_id', created['id'])


def run():
    queue = collections.deque()
    monitor = Monitor(queue)
    player = Player(queue)
    store = kodi.open_store()
    recorder = Recorder(store, None, log=kodi.debug)
    tracker = Tracker(recorder, log=kodi.log)
    phone = Remote(store, monitor)
    ctx = {}

    def apply_settings():
        tracker.configure(threshold=kodi.setting_int('threshold', 85),
                          min_seconds=kodi.setting_int('min_minutes', 5) * 60)
        recorder.tmdb = kodi.make_tmdb(store)
        ctx['drive'] = kodi.make_drive(store)
        ctx['interval'] = max(5, kodi.setting_int('sync_interval', 15)) * 60

    def do_sync():
        try:
            sync(store, ctx['drive'], log=kodi.debug)
        except AuthError as e:
            kodi.log('sync auth error: %s' % e, xbmc.LOGWARNING)
            kodi.notify(str(e), 8000)
        except Exception as e:
            kodi.log('sync failed: %s' % e, xbmc.LOGWARNING)

    apply_settings()
    kodi.log('service started (device %s)' % store.device_id())
    last_sync, last_retry, want_sync = 0.0, 0.0, True

    while not monitor.abortRequested():
        while queue:
            event = queue.popleft()
            if event == 'start':
                if player.isPlayingVideo():
                    raw = capture_raw(player)
                    kodi.debug('playback started: %s' % raw)
                    if raw.get('file', '').startswith('pvr://'):
                        tracker.finish()
                    else:
                        tracker.start(raw)
            elif event == 'stop':
                tracker.finish()
            elif event == 'settings':
                apply_settings()
                want_sync = True
            elif event == 'sync':
                want_sync = True

        if tracker.active and player.isPlayingVideo():
            try:
                tracker.tick(player.getTime(), player.getTotalTime())
            except RuntimeError:
                pass
        if tracker.pop_recorded():
            want_sync = True

        now = time.time()
        if ctx['drive'].signed_in and (now - last_sync >= ctx['interval'] or
                                       (want_sync and now - last_sync >= MIN_SECONDS_BETWEEN_SYNCS)):
            do_sync()
            last_sync, want_sync = now, False
        if ctx['drive'].signed_in:
            try:
                phone.tick(ctx['drive'], now)
            except AuthError:
                pass  # reported by the regular sync
            except Exception as e:
                kodi.debug('remote poll failed: %s' % e)
        if not tracker.active and now - last_retry >= RETRY_UNIDENTIFIED_EVERY and recorder.tmdb.available:
            last_retry = now
            if recorder.retry_unidentified(State.build(store.events())):
                want_sync = True

        if monitor.waitForAbort(2):
            break

    tracker.finish()
    store.close()
    kodi.log('service stopped')
