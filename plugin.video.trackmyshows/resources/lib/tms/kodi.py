"""Kodi glue: settings, logging, notifications, and factories for the core objects."""
import json
import os

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from . import credentials
from .gdrive import GoogleDrive
from .premiumize import Premiumize
from .store import Store
from .tmdb import Tmdb

ADDON_ID = 'plugin.video.trackmyshows'
NAME = 'TrackMyShows'


def addon():
    return xbmcaddon.Addon(ADDON_ID)


def setting(key):
    return addon().getSetting(key) or ''


def setting_int(key, default):
    try:
        return int(float(setting(key)))
    except ValueError:
        return default


def setting_bool(key):
    return setting(key).lower() == 'true'


def log(msg, level=xbmc.LOGINFO):
    xbmc.log('[%s] %s' % (ADDON_ID, msg), level)


def debug(msg):
    log(msg, xbmc.LOGINFO if setting_bool('debug') else xbmc.LOGDEBUG)


def notify(msg, ms=4000):
    xbmcgui.Dialog().notification(NAME, msg, xbmcgui.NOTIFICATION_INFO, ms)


def profile_dir():
    path = xbmcvfs.translatePath(addon().getAddonInfo('profile'))
    if not xbmcvfs.exists(path):
        xbmcvfs.mkdirs(path)
    return path


def open_store():
    name = setting('device_name').strip() or xbmc.getInfoLabel('System.FriendlyName') or 'Kodi'
    return Store(os.path.join(profile_dir(), 'trackmyshows.db'), device_name=name)


def make_tmdb(store):
    return Tmdb(setting('tmdb_api_key').strip() or credentials.TMDB_API_KEY, cache=store)


class _Tokens:
    def __init__(self, store):
        self.store = store

    def get(self):
        return self.store.meta_get('google_tokens')

    def set(self, value):
        self.store.meta_set('google_tokens', value)

    def clear(self):
        self.store.meta_delete('google_tokens')


def make_drive(store):
    client_id = setting('google_client_id').strip() or credentials.GOOGLE_CLIENT_ID
    client_secret = setting('google_client_secret').strip() or credentials.GOOGLE_CLIENT_SECRET
    return GoogleDrive(client_id, client_secret, _Tokens(store))


def _other_setting(addon_id, key):
    try:
        return xbmcaddon.Addon(addon_id).getSetting(key) or ''
    except RuntimeError:  # add-on not installed
        return ''


def make_premiumize():
    """Premiumize login: our own setting, else Seren's, else the official Premiumize add-on's API key."""
    token = setting('premiumize_token').strip() or _other_setting('plugin.video.seren', 'premiumize.token')
    apikey = '' if token else _other_setting('plugin.video.premiumizemetv', 'pin')
    return Premiumize(token, apikey=apikey)


def jsonrpc(method, params=None):
    request = {'jsonrpc': '2.0', 'id': 1, 'method': method}
    if params:
        request['params'] = params
    response = json.loads(xbmc.executeJSONRPC(json.dumps(request)))
    return response.get('result')


def request_sync():
    """Ask the background service to sync soon."""
    xbmc.executebuiltin('NotifyAll(%s,sync)' % ADDON_ID)
