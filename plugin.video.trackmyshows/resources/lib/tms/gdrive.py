"""Google Drive access using the OAuth device flow (sign in with a code on your phone).

Uses the narrow drive.file scope: the add-on can only see files it created,
never the rest of your Drive. Files live in a "TrackMyShows" folder.
"""
import json
import time
import uuid

from . import net
from .net import HttpError

SCOPE = 'https://www.googleapis.com/auth/drive.file'
DEVICE_URL = 'https://oauth2.googleapis.com/device/code'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
REVOKE_URL = 'https://oauth2.googleapis.com/revoke'
API = 'https://www.googleapis.com/drive/v3'
UPLOAD = 'https://www.googleapis.com/upload/drive/v3'
FOLDER_MIME = 'application/vnd.google-apps.folder'
DEVICE_GRANT = 'urn:ietf:params:oauth:grant-type:device_code'


class AuthError(Exception):
    pass


class GoogleDrive:
    def __init__(self, client_id, client_secret, tokens, http=net.request):
        """tokens: object with get() -> dict|None, set(dict), clear()."""
        self.client_id = (client_id or '').strip()
        self.client_secret = (client_secret or '').strip()
        self.tokens = tokens
        self.http = http

    @property
    def configured(self):
        return bool(self.client_id and self.client_secret)

    @property
    def signed_in(self):
        return self.configured and bool((self.tokens.get() or {}).get('refresh_token'))

    # ---- sign-in ------------------------------------------------------
    def start_device_flow(self):
        """Returns {device_code, user_code, verification_url, expires_in, interval}."""
        try:
            return self.http('POST', DEVICE_URL, data={'client_id': self.client_id, 'scope': SCOPE})
        except HttpError as e:
            raise AuthError('Google refused the sign-in request (%s). Check the client ID/secret.'
                            % (e.json().get('error') or e.status))

    def poll_device_flow(self, device_code):
        """Returns 'ok', 'pending' or 'slow_down'; raises AuthError on failure."""
        try:
            token = self.http('POST', TOKEN_URL, data={
                'client_id': self.client_id, 'client_secret': self.client_secret,
                'device_code': device_code, 'grant_type': DEVICE_GRANT})
        except HttpError as e:
            error = e.json().get('error')
            if error == 'authorization_pending':
                return 'pending'
            if error == 'slow_down':
                return 'slow_down'
            if error == 'access_denied':
                raise AuthError('Sign-in was declined.')
            if error == 'expired_token':
                raise AuthError('The code expired. Please try again.')
            raise AuthError('Google sign-in failed: %s' % (error or e))
        self._save(token)
        return 'ok'

    def sign_out(self):
        tokens = self.tokens.get() or {}
        if tokens.get('refresh_token'):
            try:
                self.http('POST', REVOKE_URL, data={'token': tokens['refresh_token']})
            except Exception:
                pass
        self.tokens.clear()

    def _save(self, token, refresh_token=None):
        self.tokens.set({
            'access_token': token['access_token'],
            'expires_at': time.time() + int(token.get('expires_in', 3600)) - 60,
            'refresh_token': token.get('refresh_token') or refresh_token,
        })

    def _access_token(self, force=False):
        tokens = self.tokens.get() or {}
        if not tokens.get('refresh_token'):
            raise AuthError('Not signed in to Google.')
        if force or not tokens.get('access_token') or time.time() >= tokens.get('expires_at', 0):
            try:
                token = self.http('POST', TOKEN_URL, data={
                    'client_id': self.client_id, 'client_secret': self.client_secret,
                    'refresh_token': tokens['refresh_token'], 'grant_type': 'refresh_token'})
            except HttpError as e:
                if e.json().get('error') in ('invalid_grant', 'invalid_client', 'unauthorized_client'):
                    self.tokens.clear()
                    raise AuthError('Google sign-in expired or was revoked. Sign in again from TrackMyShows.')
                raise
            self._save(token, tokens['refresh_token'])
            tokens = self.tokens.get()
        return tokens['access_token']

    def _api(self, method, url, headers=None, **kwargs):
        for attempt in range(2):
            h = dict(headers or {})
            h['Authorization'] = 'Bearer ' + self._access_token(force=attempt > 0)
            try:
                return self.http(method, url, headers=h, **kwargs)
            except HttpError as e:
                if e.status == 401 and attempt == 0:
                    continue
                raise

    # ---- files --------------------------------------------------------
    def _folder_id(self):
        found = self._api('GET', API + '/files', params={
            'q': "appProperties has { key='tms' and value='folder' } and trashed = false",
            'fields': 'files(id)', 'orderBy': 'createdTime', 'spaces': 'drive'})
        files = (found or {}).get('files') or []
        if files:
            return files[0]['id']
        created = self._api('POST', API + '/files', params={'fields': 'id'}, json_body={
            'name': 'TrackMyShows', 'mimeType': FOLDER_MIME, 'appProperties': {'tms': 'folder'}})
        return created['id']

    def list_event_files(self):
        return self.list_files('events')

    def list_files(self, kind):
        """Files this app created with appProperties tms=<kind> ('events', 'device', 'remote')."""
        files, page = [], None
        while True:
            params = {'q': "appProperties has { key='tms' and value='%s' } and trashed = false" % kind,
                      'fields': 'nextPageToken,files(id,name,modifiedTime,appProperties)',
                      'pageSize': 100, 'spaces': 'drive'}
            if page:
                params['pageToken'] = page
            result = self._api('GET', API + '/files', params=params) or {}
            files.extend(result.get('files') or [])
            page = result.get('nextPageToken')
            if not page:
                return files

    def create_file(self, name, content, app_properties):
        metadata = {'name': name, 'parents': [self._folder_id()], 'appProperties': app_properties,
                    'mimeType': 'text/plain'}
        boundary = 'tms' + uuid.uuid4().hex
        body = ('--%s\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n%s\r\n'
                '--%s\r\nContent-Type: text/plain\r\n\r\n' % (boundary, json.dumps(metadata), boundary)
                ).encode('utf-8') + content + ('\r\n--%s--\r\n' % boundary).encode('utf-8')
        return self._api('POST', UPLOAD + '/files', params={'uploadType': 'multipart', 'fields': 'id,modifiedTime'},
                         data=body, headers={'Content-Type': 'multipart/related; boundary=' + boundary})

    def update_file(self, file_id, content):
        return self._api('PATCH', '%s/files/%s' % (UPLOAD, file_id),
                         params={'uploadType': 'media', 'fields': 'id,modifiedTime'},
                         data=content, headers={'Content-Type': 'text/plain'})

    def download(self, file_id):
        return self._api('GET', '%s/files/%s' % (API, file_id), params={'alt': 'media'}, raw=True)
