"""The in-Kodi menu: Where was I?, history browsing, manual marking, Google sign-in."""
import datetime
import time
from urllib.parse import parse_qsl, urlencode

import xbmc
import xbmcgui
import xbmcplugin
import xbmcvfs

from . import kodi, model, parser, seren
from .gdrive import AuthError
from .nextup import compute as compute_nextup
from .premiumize import PremiumizeError
from .state import State
from .sync import sync
from .tmdb import image


PLAY_SMART = 'Play (Premiumize if available, otherwise Seren)'
PLAY_PM = 'Play from Premiumize'
PLAY_SEREN = 'Play in Seren'
PLAY_SEREN_PICK = 'Choose source in Seren'
PLAY_OPTIONS = (PLAY_SMART, PLAY_PM, PLAY_SEREN, PLAY_SEREN_PICK)
PM_NOMATCH_RECHECK = 12 * 3600


def _date(ts):
    return datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d') if ts else ''


def _grey(text):
    return '[COLOR grey]%s[/COLOR]' % text


def _art(poster=None, still=None):
    art = {}
    if poster:
        art['poster'] = art['thumb'] = image(poster)
    if still:
        art['thumb'] = image(still, 'w300')
    return art


def _year(date):
    year = (date or '')[:4]
    return int(year) if year.isdigit() else None


class Plugin:
    def __init__(self, argv):
        self.base = argv[0]
        self.handle = int(argv[1]) if len(argv) > 1 and argv[1].lstrip('-').isdigit() else -1
        self.params = dict(parse_qsl(argv[2].lstrip('?'))) if len(argv) > 2 else {}
        self.store = kodi.open_store()
        self.tmdb = kodi.make_tmdb(self.store)
        self._state = None
        self._shows = None
        self._seren = None
        self.dialog = xbmcgui.Dialog()

    @property
    def state(self):
        if self._state is None:
            self._state = State.build(self.store.events())
        return self._state

    @property
    def shows(self):
        if self._shows is None:
            self._shows = self.state.shows()
        return self._shows

    # ---- Seren ----------------------------------------------------------
    @property
    def seren(self):
        if self._seren is None:
            self._seren = bool(xbmc.getCondVisibility('System.HasAddon(%s)' % seren.ADDON_ID))
        return self._seren

    def show_trakt(self, sid):
        trakt = (self.shows.get(int(sid)) or {}).get('trakt')
        if not trakt and self.seren:
            trakt = seren.lookup_show_trakt(xbmcvfs.translatePath(seren.SEREN_DB), sid)
        return trakt

    def seren_episode_url(self, trakt, s, e, choose_source=False):
        """Seren link for an episode, first making sure Seren's copy of the show includes it."""
        db = xbmcvfs.translatePath(seren.SEREN_DB)
        if seren.episode_known(db, trakt, s, e) is False:
            kodi.log('Seren does not know S%sE%s of show %s yet; asking it to refresh' % (s, e, trakt))
            seren.request_refresh(db, trakt)
            kodi.notify('Updating Seren\'s episode list for this show...', 4000)
        return seren.episode_url(trakt, s, e, choose_source)

    def seren_show_url(self, sid, title):
        trakt = self.show_trakt(sid)
        return seren.show_url(trakt) if trakt else seren.show_search_url(title)

    def seren_play(self, sid, s, e, title, choose_source=False):
        """Start an episode in Seren, or fall back to Seren's search if we don't know its Trakt ID yet."""
        trakt = self.show_trakt(sid)
        if trakt:
            xbmc.executebuiltin('PlayMedia(%s)' % self.seren_episode_url(trakt, s, e, choose_source))
        else:
            kodi.notify('Pick the show in Seren - next time it will play directly')
            xbmc.executebuiltin('Container.Update(%s)' % seren.show_search_url(title))

    # ---- Premiumize -----------------------------------------------------
    def pm_link(self, sid):
        return (self.shows.get(int(sid)) or {}).get('pm')

    def pm_play_url(self, sid, s, e, title):
        return self.url(action='pmplay', show=sid, s=s, e=e, title=title)

    def smart_url(self, sid, s, e, title, mode='auto'):
        return self.url(action='smartplay', show=sid, s=s, e=e, title=title, mode=mode)

    def url(self, **query):
        return self.base + '?' + urlencode(query)

    def run_url(self, **query):
        return 'RunPlugin(%s)' % self.url(**query)

    def run(self):
        action = self.params.get('action', 'root')
        handler = getattr(self, 'do_' + action, None) or self.do_root
        try:
            handler()
        finally:
            self.store.close()

    # ---- listing helpers ----------------------------------------------
    def add(self, label, query, folder=True, art=None, plot=None, context=None, info=None, url=None, playable=False):
        li = xbmcgui.ListItem(label=label, offscreen=True)
        if playable:
            li.setProperty('IsPlayable', 'true')
        if art:
            li.setArt(art)
        tag = li.getVideoInfoTag()
        if plot:
            tag.setPlot(plot)
        if info:
            info(tag)
        if context:
            li.addContextMenuItems(context)
        xbmcplugin.addDirectoryItem(self.handle, url or self.url(**query), li, isFolder=folder)

    def end(self, content=''):
        if content:
            xbmcplugin.setContent(self.handle, content)
        xbmcplugin.endOfDirectory(self.handle, cacheToDisc=False)

    def done_action(self, refresh=True):
        """Finish a click/context action without leaving the current listing."""
        if self.handle >= 0:
            xbmcplugin.endOfDirectory(self.handle, succeeded=False)
        if refresh:
            xbmc.executebuiltin('Container.Refresh')

    def changed(self):
        kodi.request_sync()
        self.done_action()

    def need_tmdb(self):
        if self.tmdb.available:
            return True
        self.dialog.ok(kodi.NAME, 'No TMDb API key is set. Rebuild the add-on with secrets/tmdb_api_key.txt '
                                  'or enter a key under Settings > Advanced.')
        return False

    # ---- root ---------------------------------------------------------
    def do_root(self):
        self.add('Where was I?', {'action': 'nextup'}, plot='The next episode to watch in each show.')
        self.add('In progress', {'action': 'inprogress'}, plot='Things you started but did not finish.')
        self.add('Recently watched', {'action': 'recent'})
        self.add('TV shows', {'action': 'shows'})
        to_watch = len(self.state.movies_to_watch())
        self.add('Movies to watch%s' % (' (%d)' % to_watch if to_watch else ''), {'action': 'watchlist'},
                 plot='Your movie list from the phone app. Select one to play it.')
        self.add('Movies watched', {'action': 'movies'})
        unknown = len(self.state.unidentified())
        if unknown:
            self.add('[COLOR orange]Needs attention (%d)[/COLOR]' % unknown, {'action': 'unidentified'},
                     plot='Videos that could not be identified automatically. Pick one to fix it.')
        self.add('Search & mark watched...', {'action': 'search'},
                 plot='Find a show or movie and mark episodes as watched (handy for filling in your history).')
        drive = kodi.make_drive(self.store)
        if not drive.configured:
            self.add(_grey('Google sync: not set up'), {'action': 'signin'})
        elif drive.signed_in:
            last = self.store.meta_get('last_sync_ts')
            when = datetime.datetime.fromtimestamp(last).strftime('%Y-%m-%d %H:%M') if last else 'never'
            self.add('Sync now', {'action': 'syncnow'}, plot='Last synced: %s' % when)
        else:
            self.add('Sign in to Google...', {'action': 'signin'}, plot='Sync your history across all your Kodi boxes.')
        self.add('Settings', {'action': 'settings'})
        self.end()

    # ---- where was I? -------------------------------------------------
    def do_nextup(self):
        # 0 play next (Premiumize folder if linked, else Seren), 1 Seren with source select,
        # 2 open the show in Seren, 3 browse episodes here
        click = kodi.setting_int('nextup_click', 0)
        for en in compute_nextup(self.state, self.tmdb):
            sid, status, nxt = en['show_tmdb'], en['status'], en['next']
            if status == 'available':
                sub = 'Next: ' + model.se(*nxt) + (' - %s' % en['next_title'] if en['next_title'] else '')
            elif status == 'caught_up':
                sub = 'Caught up' + (' - next episode %s' % en['next_air_date'] if en['next_air_date'] else '')
            elif status == 'finished':
                sub = 'Finished'
            elif en['last']:
                sub = 'Last watched ' + model.se(*en['last'])
            else:
                sub = 'Not started'
            plot = sub
            if en['last']:
                plot += '\nLast watched %s on %s' % (model.se(*en['last']), _date(en['last_ts']))
            plot += '\n%d episodes watched' % en['watched_count']
            title = en['show_title']
            browse = self.url(action='show', show=sid)
            target = dict(url=browse, folder=True, playable=False)
            context = []
            linked = self.pm_link(sid)
            if status == 'available' and click == 0:
                target = dict(url=self.smart_url(sid, nxt[0], nxt[1], title), folder=False, playable=True)
            if status == 'available' and linked:
                pm_play = self.pm_play_url(sid, nxt[0], nxt[1], title)
                context.append(('Play %s from Premiumize' % model.se(*nxt), 'PlayMedia(%s)' % pm_play))
            if self.seren:
                trakt = self.show_trakt(sid)
                if status == 'available' and trakt:
                    play = self.smart_url(sid, nxt[0], nxt[1], title, 'seren')
                    pick = self.smart_url(sid, nxt[0], nxt[1], title, 'pick')
                    context.append(('Play %s in Seren' % model.se(*nxt), 'PlayMedia(%s)' % play))
                    context.append(('Choose source in Seren', 'PlayMedia(%s)' % pick))
                    if click == 1:
                        target = dict(url=pick, folder=False, playable=True)
                if click in (0, 1, 2) and target['url'] == browse:
                    target = dict(url=self.seren_show_url(sid, title), folder=True, playable=False)
                context.append(('Open in Seren', 'Container.Update(%s)' % self.seren_show_url(sid, title)))
            context.append(('Premiumize folder...', self.run_url(
                action='pmmenu', show=sid, title=title, s=nxt[0] if status == 'available' else 0,
                e=nxt[1] if status == 'available' else 0)))
            if self.seren:
                context.append(('Browse episodes', 'Container.Update(%s)' % browse))
            if status == 'available':
                context.append(('Mark %s watched' % model.se(*nxt), self.run_url(
                    action='mark', state='watched', show=sid, s=nxt[0], e=nxt[1], title=title)))
            context.append(('Hide from Where was I?', self.run_url(action='hide', show=sid)))

            def info(tag, nxt=nxt, title=title, status=status):
                if status == 'available':
                    tag.setMediaType('episode')
                    tag.setTvShowTitle(title)
                    tag.setSeason(nxt[0])
                    tag.setEpisode(nxt[1])

            self.add('%s  %s' % (title, _grey(sub)), {}, art=_art(en['poster']), plot=plot, context=context,
                     info=info, **target)
        self.end('tvshows')

    # ---- browsing -----------------------------------------------------
    def do_shows(self):
        shows = sorted(self.state.shows().values(), key=lambda s: s['last_ts'], reverse=True)
        for show in shows:
            watched = [se for se, it in show['episodes'].items() if it.watched]
            if not watched and not show['followed'] and not any(it.in_progress for it in show['episodes'].values()):
                continue
            hidden = show['id'] in self.state.hidden_shows
            info = self.tmdb.tv(show['id'], cached_only=True) or {}
            sub = '%d watched' % len(watched)
            if watched:
                sub += ', last %s' % model.se(*max(watched))
            if hidden:
                sub += ', hidden'
                toggle = ('Show in Where was I?', self.run_url(action='unhide', show=show['id']))
            else:
                toggle = ('Hide from Where was I?', self.run_url(action='hide', show=show['id']))
            self.add('%s  %s' % (info.get('name') or show['title'], _grey(sub)), {'action': 'show', 'show': show['id']},
                     art=_art(info.get('poster_path')), context=[toggle])
        self.end('tvshows')

    def do_show(self):
        sid = int(self.params['show'])
        info = self.tmdb.tv(sid) if self.tmdb.available else None
        episodes = (self.state.shows().get(sid) or {}).get('episodes', {})
        if not info:
            for (s, e), item in sorted(episodes.items()):
                self.add('%s  %s' % (model.se(s, e), _grey('watched' if item.watched else '')),
                         {'action': 'itemmenu', 'key': item.key})
            return self.end('episodes')
        seasons = sorted(info.get('seasons') or [],
                         key=lambda x: (x.get('season_number') == 0, x.get('season_number') or 0))
        for season in seasons:
            sn, count = season.get('season_number') or 0, season.get('episode_count') or 0
            if not count:
                continue
            seen = sum(1 for (s, _), it in episodes.items() if s == sn and it.watched)
            name = season.get('name') or 'Season %d' % sn
            self.add('%s  %s' % (name, _grey('%d/%d watched' % (seen, count))),
                     {'action': 'season', 'show': sid, 's': sn},
                     art=_art(season.get('poster_path') or info.get('poster_path')),
                     context=[('Mark whole season watched', self.run_url(action='markseason', show=sid, s=sn))])
        self.end('seasons')

    def do_season(self):
        sid, sn = int(self.params['show']), int(self.params['s'])
        if not self.need_tmdb():
            return self.done_action(False)
        title = (self.tmdb.tv(sid) or {}).get('name', '')
        season = self.tmdb.season(sid, sn) or {}
        for ep in season.get('episodes') or []:
            en = ep.get('episode_number')
            key = 'tv:%d:%d:%d' % (sid, sn, en)
            watched = self.state.is_watched(key)
            item = self.state.get(key)

            def info(tag, ep=ep, en=en, watched=watched, item=item):
                tag.setMediaType('episode')
                tag.setTitle(ep.get('name') or '')
                tag.setTvShowTitle(title)
                tag.setSeason(sn)
                tag.setEpisode(en)
                tag.setPlaycount(1 if watched else 0)
                if ep.get('air_date'):
                    tag.setPremiered(ep['air_date'])
                if item and item.in_progress and (item.progress or {}).get('total'):
                    tag.setResumePoint(item.progress['position'], item.progress['total'])

            q = dict(show=sid, s=sn, e=en, title=title)
            if watched:
                toggle = ('Mark unwatched', self.run_url(action='mark', state='unwatched', **q))
            else:
                toggle = ('Mark watched', self.run_url(action='mark', state='watched', **q))
            context = [toggle, ('Mark watched up to here', self.run_url(action='markupto', show=sid, s=sn, e=en))]
            if self.seren:
                context.insert(0, ('Choose source in Seren', self.run_url(action='serenplay', pick=1, **q)))
                context.insert(0, ('Play in Seren', self.run_url(action='serenplay', **q)))
            if self.pm_link(sid):
                context.insert(0, ('Play from Premiumize', 'PlayMedia(%s)' % self.pm_play_url(sid, sn, en, title)))
            context.insert(0, ('Play', 'PlayMedia(%s)' % self.smart_url(sid, sn, en, title)))
            self.add('%d. %s' % (en, ep.get('name') or ''), dict(action='epmenu', **q),
                     art=_art(still=ep.get('still_path')), plot=ep.get('overview'), context=context, info=info)
        self.end('episodes')

    def do_movies(self):
        for item in self.state.movies():
            m = item.media
            info = self.tmdb.movie(m['tmdb'], cached_only=True) or {}

            def tag_info(tag, m=m):
                tag.setMediaType('movie')
                tag.setTitle(m.get('title') or '')
                tag.setPlaycount(1)
                if m.get('year'):
                    tag.setYear(m['year'])

            self.add('%s  %s' % (model.describe(m), _grey(_date(item.watched_ts))),
                     {'action': 'itemmenu', 'key': item.key}, art=_art(info.get('poster_path')), info=tag_info,
                     context=[('Mark unwatched', self.run_url(action='markkey', key=item.key, state='unwatched'))])
        self.end('movies')

    def movie_url(self, m, mode='auto'):
        return self.url(action='smartmovie', movie=m['tmdb'], title=m.get('title', ''), year=m.get('year') or '',
                        mode=mode)

    def do_watchlist(self):
        for entry in self.state.movies_to_watch():
            m = entry['media']
            info = self.tmdb.movie(m['tmdb'], cached_only=True) or {}

            def tag_info(tag, m=m, info=info):
                tag.setMediaType('movie')
                tag.setTitle(m.get('title') or '')
                if m.get('year'):
                    tag.setYear(m['year'])
                if info.get('overview'):
                    tag.setPlot(info['overview'])

            key = model.media_key(m)
            context = [('Mark watched', self.run_url(action='markkey', key=key, state='watched'))]
            if self.seren:
                context += [('Play in Seren', 'PlayMedia(%s)' % self.movie_url(m, 'seren')),
                            ('Choose source in Seren', 'PlayMedia(%s)' % self.movie_url(m, 'pick'))]
            context.append(('Remove from list', self.run_url(action='unwatchlist', key=key)))
            self.add(model.describe(m), {}, url=self.movie_url(m), folder=False, playable=True,
                     art=_art(info.get('poster_path')), info=tag_info, context=context)
        if not self.state.movies_to_watch():
            self.add(_grey('Add movies from the phone app (Movies tab)'), {'action': 'watchlist'})
        self.end('movies')

    def do_unwatchlist(self):
        self.store.add_event('unwatchlist', self.params['key'])
        self.changed()

    def do_smartmovie(self):
        """Movie: Premiumize first (search the cloud by title and year), otherwise Seren."""
        p = self.params
        tmdb_id, title, mode = int(p['movie']), p.get('title', ''), p.get('mode', 'auto')
        year = int(p['year']) if (p.get('year') or '').isdigit() else None
        found = None
        if mode == 'auto':
            pm = kodi.make_premiumize()
            if pm.available:
                try:
                    found = pm.find_movie(title, year)
                except Exception as err:
                    kodi.log('premiumize movie lookup failed: %s' % err, xbmc.LOGWARNING)
        if found:
            li = xbmcgui.ListItem(label=found.get('name'), path=found['link'], offscreen=True)
            tag = li.getVideoInfoTag()
            tag.setMediaType('movie')
            tag.setTitle(title)
            if year:
                tag.setYear(year)
            tag.setUniqueIDs({'tmdb': str(tmdb_id)}, 'tmdb')  # lets the tracker identify it exactly
            if self.handle >= 0:
                return xbmcplugin.setResolvedUrl(self.handle, True, li)
            return xbmc.Player().play(found['link'], li)
        item = self.state.get('movie:%d' % tmdb_id)
        trakt = (item.media or {}).get('trakt') if item and item.media else None
        if not trakt and self.seren:
            trakt = seren.lookup_movie_trakt(xbmcvfs.translatePath(seren.SEREN_DB), tmdb_id)
        if trakt and self.seren:
            url = seren.movie_url(trakt, choose_source=mode == 'pick')
            if self.handle >= 0:
                return xbmcplugin.setResolvedUrl(self.handle, True, xbmcgui.ListItem(path=url, offscreen=True))
            return xbmc.executebuiltin('PlayMedia(%s)' % url)
        if self.handle >= 0:
            xbmcplugin.setResolvedUrl(self.handle, False, xbmcgui.ListItem())
        if self.seren:
            kodi.notify('Pick the movie in Seren')
            xbmc.executebuiltin('ActivateWindow(Videos,%s,return)' % seren.movie_search_url(title))
        else:
            kodi.notify('%s is not in Premiumize, and Seren is not installed' % title)

    def do_recent(self):
        for item in self.state.recent():
            where = ' on %s' % item.last_device if item.last_device else ''
            self.add('%s  %s' % (item.describe(), _grey(_date(item.watched_ts) + where)),
                     {'action': 'itemmenu', 'key': item.key})
        self.end('videos')

    def do_inprogress(self):
        for item in self.state.in_progress():
            p = item.progress or {}
            where = ' on %s' % item.last_device if item.last_device else ''
            self.add('%s  %s' % (item.describe(), _grey('%d%%%s' % (p.get('pct', 0), where))),
                     {'action': 'itemmenu', 'key': item.key})
        self.end('videos')

    def do_unidentified(self):
        for item in self.state.unidentified():
            raw = item.raw or {}
            plot = 'File: %s\nTitle: %s\nSeen: %s' % (raw.get('file', '?'), raw.get('title') or raw.get('label') or '?',
                                                     _date(item.last_ts))
            self.add('%s  %s' % (item.describe(), _grey(_date(item.last_ts))), {'action': 'fix', 'key': item.key},
                     plot=plot, context=[('Delete this entry', self.run_url(action='ignore', key=item.key))])
        self.end('videos')

    # ---- marking ------------------------------------------------------
    def _episode_media(self):
        p = self.params
        return model.episode_media(p['show'], p.get('title', ''), p['s'], p['e'])

    def do_mark(self):
        media = self._episode_media()
        self.store.add_event(self.params['state'], model.media_key(media), media=media)
        self.changed()

    def do_markkey(self):
        key = self.params['key']
        item = self.state.get(key)
        if item:
            self.store.add_event(self.params['state'], item.key, media=item.media,
                                 raw=None if item.media else item.raw)
        elif key in self.state.watchlist:  # a listed movie that has never been played
            self.store.add_event(self.params['state'], key, media=self.state.watchlist[key]['media'])
        self.changed()

    def do_epmenu(self):
        media = self._episode_media()
        key = model.media_key(media)
        watched = self.state.is_watched(key)
        options = ['Mark unwatched' if watched else 'Mark watched', 'Mark watched up to here',
                   'Mark whole season watched']
        options += self._play_options(media['show_tmdb'])
        choice = self.dialog.select(model.describe(media), options)
        picked = options[choice] if choice >= 0 else None
        if picked in PLAY_OPTIONS:
            self.done_action(False)
            return self._play(picked, media)
        if choice == 0:
            self.store.add_event('unwatched' if watched else 'watched', key, media=media)
            return self.changed()
        if choice == 1:
            return self.do_markupto()
        if choice == 2:
            return self.do_markseason()
        self.done_action(False)

    def _play_options(self, sid):
        options = [PLAY_SMART]
        if self.pm_link(sid):
            options.append(PLAY_PM)
        if self.seren:
            options += [PLAY_SEREN, PLAY_SEREN_PICK]
        return options

    def _play(self, picked, media):
        sid, s, e, title = media['show_tmdb'], media['season'], media['episode'], media.get('show_title', '')
        if picked == PLAY_SMART:
            return xbmc.executebuiltin('PlayMedia(%s)' % self.smart_url(sid, s, e, title))
        if picked == PLAY_PM:
            return xbmc.executebuiltin('PlayMedia(%s)' % self.pm_play_url(sid, s, e, title))
        return self.seren_play(sid, s, e, title, choose_source=picked == PLAY_SEREN_PICK)

    def _mark_many(self, sid, episodes):
        show = self.tmdb.tv(sid) or {}
        todo = [(s, e) for s, e in episodes if not self.state.is_watched('tv:%d:%d:%d' % (sid, s, e))]
        if not todo:
            kodi.notify('Already marked as watched')
            return self.done_action(False)
        if len(todo) > 1 and not self.dialog.yesno(
                kodi.NAME, 'Mark %d episodes of %s as watched?' % (len(todo), show.get('name', 'this show'))):
            return self.done_action(False)
        events = []
        for s, e in todo:
            media = model.episode_media(sid, show.get('name', ''), s, e)
            events.append(self.store.new_event('watched', model.media_key(media), media=media))
        self.store.add_events(events)
        kodi.notify('Marked %d episode(s) watched' % len(todo))
        self.changed()

    def do_markupto(self):
        sid, sn, en = int(self.params['show']), int(self.params['s']), int(self.params['e'])
        if not self.need_tmdb():
            return self.done_action(False)
        episodes = []
        for season in (self.tmdb.tv(sid) or {}).get('seasons') or []:
            s, count = season.get('season_number') or 0, season.get('episode_count') or 0
            if 0 < s <= sn:
                episodes += [(s, e) for e in range(1, count + 1) if s < sn or e <= en]
        self._mark_many(sid, episodes)

    def do_markseason(self):
        sid, sn = int(self.params['show']), int(self.params['s'])
        if not self.need_tmdb():
            return self.done_action(False)
        season = self.tmdb.season(sid, sn) or {}
        self._mark_many(sid, [(sn, ep['episode_number']) for ep in season.get('episodes') or []])

    def do_serenplay(self):
        p = self.params
        self.done_action(False)
        self.seren_play(p['show'], p['s'], p['e'], p.get('title', ''), choose_source=p.get('pick') == '1')

    # ---- Premiumize actions -------------------------------------------
    def _resolve_file(self, found, sid, s, e, title):
        li = xbmcgui.ListItem(label=found.get('name'), path=found['link'], offscreen=True)
        tag = li.getVideoInfoTag()
        tag.setMediaType('episode')
        tag.setTvShowTitle(title)
        tag.setSeason(s)
        tag.setEpisode(e)
        tag.setUniqueIDs({'tvshow.tmdb': str(sid)})  # lets the tracker identify it exactly
        if self.handle >= 0:
            xbmcplugin.setResolvedUrl(self.handle, True, li)
        else:
            xbmc.Player().play(found['link'], li)

    def do_smartplay(self):
        """Premiumize first (linked folder, or one found by searching the cloud), otherwise Seren."""
        p = self.params
        sid, s, e, title = int(p['show']), int(p['s']), int(p['e']), p.get('title', '')
        mode = p.get('mode', 'auto')  # auto: Premiumize then Seren; seren: Seren autoplay; pick: Seren source select
        found = None
        pm = kodi.make_premiumize() if mode == 'auto' else None
        if pm and pm.available:
            link = self.pm_link(sid)
            try:
                if link:
                    found = pm.find_episode(link['id'], s, e, link.get('name', ''))
                else:
                    nomatch = self.store.meta_get('pm_nomatch') or {}
                    if time.time() - nomatch.get(str(sid), 0) > PM_NOMATCH_RECHECK:
                        year = _year((self.tmdb.tv(sid) or {}).get('first_air_date')) if self.tmdb.available else None
                        hit = pm.discover(title, year, s, e)
                        if hit:
                            folder, found = hit
                            self.store.add_event('link', model.show_key(sid),
                                                 pm={'id': str(folder['id']), 'name': folder.get('name', '')})
                            kodi.request_sync()
                            kodi.notify('Found %s in Premiumize: "%s"' % (title, folder.get('name', '')), 6000)
                        else:
                            nomatch[str(sid)] = time.time()
                            self.store.meta_set('pm_nomatch', nomatch)
            except Exception as err:
                kodi.log('premiumize lookup failed, falling back to Seren: %s' % err, xbmc.LOGWARNING)
        if found:
            return self._resolve_file(found, sid, s, e, title)
        trakt = self.show_trakt(sid) if self.seren else None
        if trakt:
            url = self.seren_episode_url(trakt, s, e, choose_source=mode == 'pick')
            if self.handle >= 0:
                return xbmcplugin.setResolvedUrl(self.handle, True, xbmcgui.ListItem(path=url, offscreen=True))
            return xbmc.executebuiltin('PlayMedia(%s)' % url)
        if self.handle >= 0:
            xbmcplugin.setResolvedUrl(self.handle, False, xbmcgui.ListItem())
        if self.seren:
            kodi.notify('Pick the show in Seren - next time it will play directly')
            xbmc.executebuiltin('ActivateWindow(Videos,%s,return)' % seren.show_search_url(title))
        else:
            kodi.notify('%s %s is not in Premiumize, and Seren is not installed' % (title, model.se(s, e)))

    def do_pmplay(self):
        """Resolve SxxEyy to a file in the show's linked Premiumize folder (playable item)."""
        p = self.params
        sid, s, e, title = int(p['show']), int(p['s']), int(p['e']), p.get('title', '')
        link = self.pm_link(sid)
        found = None
        try:
            if not link:
                raise PremiumizeError('%s is not linked to a Premiumize folder' % (title or 'This show'))
            found = kodi.make_premiumize().find_episode(link['id'], s, e, link.get('name', ''))
            if not found:
                raise PremiumizeError('%s %s is not in your Premiumize folder "%s"'
                                      % (title, model.se(s, e), link.get('name') or '?'))
        except PremiumizeError as err:
            kodi.notify(str(err), 6000)
        except Exception as err:  # network
            kodi.log('premiumize play failed: %s' % err, xbmc.LOGWARNING)
            kodi.notify('Premiumize unavailable: %s' % err, 6000)
        if not found:
            if self.handle >= 0:
                xbmcplugin.setResolvedUrl(self.handle, False, xbmcgui.ListItem())
            return
        self._resolve_file(found, sid, s, e, title)

    def do_pmmenu(self):
        p = self.params
        sid, title = int(p['show']), p.get('title', '')
        s, e = int(p.get('s') or 0), int(p.get('e') or 0)
        link = self.pm_link(sid)
        options = []
        if link:
            if s:
                options.append('Play %s from Premiumize' % model.se(s, e))
            if self.seren:
                options.append('Open folder "%s"' % (link.get('name') or 'linked folder'))
            options += ['Link a different folder...', 'Unlink']
        else:
            options.append('Link a Premiumize folder...')
        choice = self.dialog.select('%s - Premiumize' % title, options)
        self.done_action(False)
        if choice < 0:
            return
        picked = options[choice]
        if picked.startswith('Play'):
            xbmc.executebuiltin('PlayMedia(%s)' % self.pm_play_url(sid, s, e, title))
        elif picked.startswith('Open folder'):
            xbmc.executebuiltin('Container.Update(%s)' % seren.premiumize_folder_url(link['id']))
        elif picked == 'Unlink':
            self.store.add_event('unlink', model.show_key(sid))
            kodi.request_sync()
            kodi.notify('Unlinked from Premiumize')
            xbmc.executebuiltin('Container.Refresh')
        elif self._pm_link(sid, title):
            xbmc.executebuiltin('Container.Refresh')

    def _pm_link(self, sid, title):
        pm = kodi.make_premiumize()
        if not pm.available:
            self.dialog.ok(kodi.NAME, 'Premiumize is not set up. Sign in to Premiumize in Seren first '
                                      '(or enter a Premiumize API key under Settings > Advanced).')
            return False
        query = self.dialog.input('Search your Premiumize cloud', title)
        if query is None or query == '' and not self.dialog.yesno(kodi.NAME, 'Browse your top-level folders instead?'):
            return False
        try:
            folders = [c for c in (pm.search(query) if query else []) if c.get('type') == 'folder']
            if not folders:
                if query:
                    kodi.notify('No folders matching "%s" - showing your top-level folders' % query)
                folders = [c for c in pm.list_folder().get('content') or [] if c.get('type') == 'folder']
        except Exception as err:
            self.dialog.ok(kodi.NAME, 'Could not reach Premiumize:\n%s' % err)
            return False
        if not folders:
            self.dialog.ok(kodi.NAME, 'No folders found in your Premiumize cloud.')
            return False
        choice = self.dialog.select('Pick the folder for %s' % title, [f.get('name', '?') for f in folders[:50]])
        if choice < 0:
            return False
        folder = folders[choice]
        self.store.add_event('link', model.show_key(sid), pm={'id': str(folder['id']), 'name': folder.get('name', '')})
        kodi.request_sync()
        kodi.notify('Linked %s to "%s"' % (title, folder.get('name', '')))
        return True

    def do_hide(self):
        self.store.add_event('hide', model.show_key(self.params['show']))
        self.changed()

    def do_unhide(self):
        self.store.add_event('unhide', model.show_key(self.params['show']))
        self.changed()

    def do_ignore(self):
        self.store.add_event('ignore', self.params['key'])
        self.changed()

    def do_itemmenu(self):
        item = self.state.get(self.params['key'])
        if not item:
            return self.done_action(False)
        options = ['Mark unwatched' if item.watched else 'Mark watched']
        if item.kind == 'episode':
            options = self._play_options(item.media['show_tmdb']) + options
        elif self.seren and item.kind == 'movie':
            options = [PLAY_SEREN, PLAY_SEREN_PICK] + options
        if item.kind == 'episode':
            options.append('Go to %s' % (item.media.get('show_title') or 'show'))
        if item.media is None:
            options += ['Identify...', 'Delete this entry']
        choice = self.dialog.select(item.describe(), options)
        if choice < 0:
            return self.done_action(False)
        picked = options[choice]
        if picked in PLAY_OPTIONS:
            self.done_action(False)
            m = item.media
            if item.kind == 'episode':
                return self._play(picked, m)
            if m.get('trakt'):
                return xbmc.executebuiltin('PlayMedia(%s)' % seren.movie_url(m['trakt'], picked == PLAY_SEREN_PICK))
            return xbmc.executebuiltin('Container.Update(%s)' % seren.movie_search_url(m.get('title', '')))
        if picked == 'Identify...':
            return self.do_fix()
        if picked.startswith('Go to'):
            self.done_action(False)
            return xbmc.executebuiltin('Container.Update(%s)' % self.url(
                action='season', show=item.media['show_tmdb'], s=item.media['season']))
        if picked == 'Delete this entry':
            self.store.add_event('ignore', item.key)
        else:
            self.store.add_event('unwatched' if item.watched else 'watched', item.key, media=item.media,
                                 raw=None if item.media else item.raw)
        self.changed()

    # ---- identification -----------------------------------------------
    def _pick_tmdb(self, default=''):
        query = self.dialog.input('Search TMDb', default)
        if not query:
            return None
        results = self.tmdb.search_multi(query)[:20]
        if not results:
            kodi.notify('Nothing found for "%s"' % query)
            return None
        labels = []
        for r in results:
            year = _year(r.get('first_air_date') or r.get('release_date'))
            labels.append('%s%s - %s' % (r.get('name') or r.get('title'), ' (%s)' % year if year else '',
                                         'TV' if r['media_type'] == 'tv' else 'Movie'))
        choice = self.dialog.select('Pick the right one', labels)
        return results[choice] if choice >= 0 else None

    def do_fix(self):
        key = self.params['key']
        item = self.state.get(key)
        if not item or not self.need_tmdb():
            return self.done_action(False)
        raw = item.raw or {}
        parsed = parser.parse(raw.get('file') or raw.get('label') or '') or {}
        pick = self._pick_tmdb(raw.get('showtitle') or parsed.get('title') or raw.get('title') or '')
        if not pick:
            return self.done_action(False)
        if pick['media_type'] == 'tv':
            season = self.dialog.input('Season number', str(raw.get('season') or parsed.get('season') or 1),
                                       type=xbmcgui.INPUT_NUMERIC)
            episode = self.dialog.input('Episode number', str(raw.get('episode') or parsed.get('episode') or 1),
                                        type=xbmcgui.INPUT_NUMERIC)
            if not season or not episode:
                return self.done_action(False)
            media = model.episode_media(pick['id'], pick.get('name'), season, episode,
                                        year=_year(pick.get('first_air_date')))
        else:
            media = model.movie_media(pick['id'], pick.get('title'), _year(pick.get('release_date')))
        self.store.add_event('resolve', key, media=media)
        kodi.notify('Saved as %s' % model.describe(media))
        self.changed()

    def do_search(self):
        if not self.need_tmdb():
            return self.done_action(False)
        pick = self._pick_tmdb()
        if not pick:
            return self.done_action(False)
        if pick['media_type'] == 'tv':
            self.done_action(False)
            return xbmc.executebuiltin('Container.Update(%s)' % self.url(action='show', show=pick['id']))
        media = model.movie_media(pick['id'], pick.get('title'), _year(pick.get('release_date')))
        key = model.media_key(media)
        watched = self.state.is_watched(key)
        if self.dialog.yesno(model.describe(media), 'Mark as %s?' % ('unwatched' if watched else 'watched')):
            self.store.add_event('unwatched' if watched else 'watched', key, media=media)
            kodi.notify('Saved')
            return self.changed()
        self.done_action(False)

    # ---- google -------------------------------------------------------
    def do_signin(self):
        self.done_action(False)
        drive = kodi.make_drive(self.store)
        if not drive.configured:
            self.dialog.ok(kodi.NAME, 'Google sync is not set up yet. Put your Google client file in secrets/ and '
                                      'rebuild the add-on (see README), or enter the client ID and secret under '
                                      'Settings > Advanced.')
            return
        try:
            flow = drive.start_device_flow()
        except Exception as e:
            self.dialog.ok(kodi.NAME, 'Could not start Google sign-in:\n%s' % e)
            return
        message = ('On your phone or computer, go to:\n[B]%s[/B]\nand enter the code:  [B]%s[/B]'
                   % (flow.get('verification_url') or flow.get('verification_uri'), flow['user_code']))
        expires = int(flow.get('expires_in', 1800))
        interval = int(flow.get('interval', 5))
        progress = xbmcgui.DialogProgress()
        progress.create('Sign in to Google', message)
        monitor = xbmc.Monitor()
        waited, next_poll, result = 0.0, interval, None
        try:
            while waited < expires and not progress.iscanceled() and not monitor.abortRequested():
                monitor.waitForAbort(0.5)
                waited += 0.5
                progress.update(int(100 - waited * 100 / expires), message)
                if waited < next_poll:
                    continue
                status = drive.poll_device_flow(flow['device_code'])
                if status == 'ok':
                    result = 'ok'
                    break
                if status == 'slow_down':
                    interval += 5
                next_poll = waited + interval
        except AuthError as e:
            result = str(e)
        finally:
            progress.close()
        if result == 'ok':
            kodi.notify('Signed in to Google. Syncing...')
            kodi.request_sync()
        elif result:
            self.dialog.ok(kodi.NAME, result)

    def do_signout(self):
        self.done_action(False)
        drive = kodi.make_drive(self.store)
        if drive.signed_in and self.dialog.yesno(
                kodi.NAME, 'Sign out of Google on this device? Your history stays on this box and in your Drive.'):
            drive.sign_out()
            kodi.notify('Signed out')

    def do_syncnow(self):
        drive = kodi.make_drive(self.store)
        if not drive.signed_in:
            return self.do_signin()
        try:
            result = sync(self.store, drive, log=kodi.debug)
            kodi.notify('Synced. %d new entries from %d other device(s)' % (result['imported'], result['other_devices']))
        except AuthError as e:
            self.dialog.ok(kodi.NAME, str(e))
        except Exception as e:
            kodi.log('sync failed: %s' % e, xbmc.LOGWARNING)
            kodi.notify('Sync failed: %s' % e, 8000)
        self.done_action()

    def do_settings(self):
        self.done_action(False)
        kodi.addon().openSettings()


def run(argv):
    Plugin(argv).run()
