"""Unit tests for the Kodi-independent core. Run: python -m unittest discover tests"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'plugin.video.trackmyshows', 'resources', 'lib'))

from tms import model, nextup, parser, resolver  # noqa: E402
from tms.recorder import Recorder  # noqa: E402
from tms.state import State  # noqa: E402
from tms.store import Store  # noqa: E402
from tms.sync import sync  # noqa: E402
from tms.tracker import Tracker  # noqa: E402

BEAR = {'id': 136315, 'name': 'The Bear', 'first_air_date': '2022-06-23', 'status': 'Returning Series',
        'seasons': [{'season_number': 0, 'episode_count': 2}, {'season_number': 1, 'episode_count': 8},
                    {'season_number': 2, 'episode_count': 10}, {'season_number': 3, 'episode_count': 10}],
        'last_episode_to_air': {'season_number': 3, 'episode_number': 10},
        'next_episode_to_air': None}


class FakeTmdb:
    available = True

    def __init__(self):
        self.shows = {BEAR['id']: BEAR}
        self.finds = {
            'tt14452776': {'tv_results': [BEAR]},
            'tt99999999': {'tv_episode_results': [{'show_id': BEAR['id'], 'season_number': 2,
                                                   'episode_number': 6, 'name': 'Fishes'}]},
            'tt15398776': {'movie_results': [{'id': 872585, 'title': 'Oppenheimer', 'release_date': '2023-07-19'}]},
        }

    def find(self, value, source):
        return self.finds.get(value, {})

    def tv(self, show_id, cached_only=False):
        return self.shows.get(int(show_id))

    def movie(self, movie_id, cached_only=False):
        return None

    def season(self, show_id, season):
        return {'episodes': [{'episode_number': e, 'name': 'Ep %d' % e} for e in range(1, 11)]}

    def search_tv(self, query, year=None):
        return [BEAR] if 'bear' in query.lower() else []

    def search_movie(self, query, year=None):
        if 'oppenheimer' in query.lower():
            return [{'id': 872585, 'title': 'Oppenheimer', 'release_date': '2023-07-19'}]
        return []


class ParserTests(unittest.TestCase):
    def check(self, path, **expected):
        result = parser.parse(path)
        for k, v in expected.items():
            self.assertEqual(result.get(k), v, '%s: %s=%r, got %r' % (path, k, v, result))

    def test_standard_episode(self):
        self.check('The.Bear.S02E05.1080p.WEB.x264-GRP.mkv', kind='episode', title='The Bear', season=2, episode=5)

    def test_premiumize_url(self):
        self.check('https://abc.energycdn.com/dl/4f9a8b/The.Bear.S02E05.Honeydew.1080p.mkv?x=1',
                   kind='episode', title='The Bear', season=2, episode=5)

    def test_multi_episode(self):
        self.check('Show.Name.S01E01E02.720p.mkv', season=1, episode=1, episodes=[1, 2])
        self.check('Show Name - S01E01-E03.mkv', episodes=[1, 2, 3])

    def test_x_format_not_resolution(self):
        self.check('Friends 3x07 The Race Car Bed.avi', kind='episode', title='Friends', season=3, episode=7)
        self.check('Some.Movie.2019.1920x1080.x264.mkv', kind='movie', title='Some Movie', year=2019)

    def test_year_in_show_title(self):
        self.check('Doctor.Who.2005.S01E01.Rose.mkv', title='Doctor Who', year=2005, season=1, episode=1)

    def test_episode_in_season_folder(self):
        self.check('/cloud/The.Bear.S02.1080p.WEB/E05.mkv', title='The Bear', season=2, episode=5)
        self.check('/cloud/The Bear Season 2/S02E05.mkv', title='The Bear', season=2, episode=5)

    def test_movies(self):
        self.check('Oppenheimer.2023.2160p.UHD.mkv', kind='movie', title='Oppenheimer', year=2023)
        self.check('Blade.Runner.2049.2017.1080p.mkv', kind='movie', title='Blade Runner 2049', year=2017)
        self.check('2001.A.Space.Odyssey.1968.mkv', kind='movie', title='2001 A Space Odyssey', year=1968)
        self.check('[Group] Movie Name (1999) [1080p].mkv', kind='movie', title='Movie Name', year=1999)

    def test_unknown(self):
        self.check('Random.Video.1080p.WEB.mkv', kind=None, title='Random Video')


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.tmdb = FakeTmdb()

    def test_seren_show_imdb(self):
        raw = {'mediatype': 'episode', 'showtitle': 'The Bear', 'season': 2, 'episode': 5,
               'uniqueid': {'imdb': 'tt14452776'}}
        m = resolver.resolve(raw, self.tmdb)
        self.assertEqual(model.media_key(m), 'tv:136315:2:5')

    def test_episode_imdb(self):
        m = resolver.resolve({'mediatype': 'episode', 'imdbnumber': 'tt99999999'}, self.tmdb)
        self.assertEqual((m['season'], m['episode'], m['title']), (2, 6, 'Fishes'))

    def test_movie_imdb(self):
        m = resolver.resolve({'mediatype': 'movie', 'title': 'Oppenheimer', 'uniqueid': {'imdb': 'tt15398776'}},
                             self.tmdb)
        self.assertEqual(model.media_key(m), 'movie:872585')

    def test_tmdb_id_title_mismatch_falls_through_to_search(self):
        raw = {'mediatype': 'episode', 'showtitle': 'The Bear', 'season': 1, 'episode': 1,
               'uniqueid': {'tmdb': '999'}}
        self.assertEqual(model.media_key(resolver.resolve(raw, self.tmdb)), 'tv:136315:1:1')

    def test_filename_only(self):
        raw = {'file': 'https://x.premiumize.me/dl/abc/The.Bear.S03E02.mkv', 'label': 'The.Bear.S03E02.mkv'}
        self.assertEqual(model.media_key(resolver.resolve(raw, self.tmdb)), 'tv:136315:3:2')
        raw = {'file': '/cloud/Oppenheimer.2023.1080p.mkv'}
        self.assertEqual(model.media_key(resolver.resolve(raw, self.tmdb)), 'movie:872585')

    def test_unidentifiable(self):
        self.assertIsNone(resolver.resolve({'file': '/x/home_video.mkv'}, self.tmdb))


class StoreMixin:
    def make_store(self, name='Box'):
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        os.remove(path)
        store = Store(path, device_name=name)
        self.addCleanup(lambda: (store.close(), os.remove(path)))
        return store


class StateAndNextUpTests(StoreMixin, unittest.TestCase):
    def ep(self, s, e):
        return model.episode_media(136315, 'The Bear', s, e)

    def test_watch_unwatch_and_nextup(self):
        store = self.make_store()
        for e in range(1, 9):
            store.add_event('watched', model.media_key(self.ep(1, e)), media=self.ep(1, e))
        store.add_event('watched', 'tv:136315:2:1', media=self.ep(2, 1))
        store.add_event('watched', 'tv:136315:2:2', media=self.ep(2, 2))
        store.add_event('unwatched', 'tv:136315:2:2', media=self.ep(2, 2))
        state = State.build(store.events())
        self.assertTrue(state.is_watched('tv:136315:2:1'))
        self.assertFalse(state.is_watched('tv:136315:2:2'))
        entry = nextup.compute(state, FakeTmdb())[0]
        self.assertEqual((entry['status'], entry['next'], entry['next_title']), ('available', (2, 2), 'Ep 2'))

    def test_season_rollover_and_caught_up(self):
        store = self.make_store()
        store.add_event('watched', 'tv:136315:1:8', media=self.ep(1, 8))
        self.assertEqual(nextup.compute(State.build(store.events()), FakeTmdb())[0]['next'], (2, 1))
        store.add_event('watched', 'tv:136315:3:10', media=self.ep(3, 10))
        entry = nextup.compute(State.build(store.events()), FakeTmdb())[0]
        self.assertEqual(entry['status'], 'caught_up')

    def test_hide_show(self):
        store = self.make_store()
        store.add_event('watched', 'tv:136315:1:1', media=self.ep(1, 1))
        store.add_event('hide', model.show_key(136315))
        self.assertEqual(nextup.compute(State.build(store.events()), FakeTmdb()), [])
        store.add_event('unhide', model.show_key(136315))
        self.assertEqual(len(nextup.compute(State.build(store.events()), FakeTmdb())), 1)

    def test_followed_show_without_watches(self):
        store = self.make_store()
        store.add_event('follow', model.show_key(136315), media={'kind': 'show', 'show_tmdb': 136315,
                                                                 'show_title': 'The Bear'})
        entry = nextup.compute(State.build(store.events()), FakeTmdb())[0]
        self.assertEqual((entry['status'], entry['next'], entry['last']), ('available', (1, 1), None))
        store.add_event('hide', model.show_key(136315))
        self.assertEqual(nextup.compute(State.build(store.events()), FakeTmdb()), [])

    def test_resolve_merges_unidentified(self):
        store = self.make_store()
        raw = {'file': '/x/weird_name.mkv'}
        store.add_event('watched', model.raw_key(raw), raw=raw)
        state = State.build(store.events())
        self.assertEqual(len(state.unidentified()), 1)
        store.add_event('resolve', model.raw_key(raw), media=self.ep(2, 3))
        state = State.build(store.events())
        self.assertEqual(state.unidentified(), [])
        self.assertTrue(state.is_watched('tv:136315:2:3'))


class TrackerTests(StoreMixin, unittest.TestCase):
    def test_threshold_and_progress(self):
        store = self.make_store()
        tracker = Tracker(Recorder(store, FakeTmdb()), threshold=85, min_seconds=300)
        raw = {'mediatype': 'episode', 'showtitle': 'The Bear', 'season': 2, 'episode': 5}
        tracker.start(raw)
        tracker.tick(600, 1800)
        tracker.finish()  # stopped at 33% -> progress
        tracker.start(raw)
        tracker.tick(1600, 1800)  # 89% -> watched immediately
        self.assertTrue(tracker.pop_recorded())
        tracker.finish()
        tracker.start({'file': 'trailer.mkv'})
        tracker.tick(100, 120)  # too short, ignored
        tracker.finish()
        types = [e['type'] for e in store.events()]
        self.assertEqual(types, ['progress', 'watched'])
        self.assertTrue(State.build(store.events()).is_watched('tv:136315:2:5'))


class FakeDrive:
    """In-memory stand-in for GoogleDrive, shared between two simulated boxes."""

    def __init__(self):
        self.files = {}
        self.counter = 0

    def list_event_files(self):
        return [{'id': fid, 'modifiedTime': f['mt'], 'appProperties': f['props']} for fid, f in self.files.items()]

    def create_file(self, name, content, props):
        self.counter += 1
        fid = 'f%d' % self.counter
        self.files[fid] = {'content': content, 'props': props, 'mt': str(self.counter)}
        return {'id': fid}

    def update_file(self, fid, content):
        self.counter += 1
        self.files[fid].update(content=content, mt=str(self.counter))

    def download(self, fid):
        return self.files[fid]['content']


class SyncTests(StoreMixin, unittest.TestCase):
    def test_two_devices_converge(self):
        drive = FakeDrive()
        living, bedroom = self.make_store('Living room'), self.make_store('Bedroom')
        living.add_event('watched', 'movie:1', media=model.movie_media(1, 'A'))
        sync(living, drive)
        bedroom.add_event('watched', 'movie:2', media=model.movie_media(2, 'B'))
        self.assertEqual(sync(bedroom, drive)['imported'], 1)
        self.assertEqual(sync(living, drive)['imported'], 1)
        self.assertEqual(sync(living, drive)['imported'], 0)  # nothing new
        for store in (living, bedroom):
            state = State.build(store.events())
            self.assertTrue(state.is_watched('movie:1') and state.is_watched('movie:2'))
        self.assertEqual(len(drive.files), 2)  # one file per device


if __name__ == '__main__':
    unittest.main()


class SerenTests(StoreMixin, unittest.TestCase):
    @staticmethod
    def seren_parse(url):
        """Decode a link exactly like Seren does (globals.init_request + tools.deconstruct_action_args)."""
        import json
        from urllib.parse import parse_qsl, unquote
        params = dict(parse_qsl(url.split('?', 1)[1]))
        args = unquote(params['action_args'])
        try:
            args = json.loads(args)
        except ValueError:
            pass
        return params['action'], args

    def test_links_round_trip(self):
        from tms import seren
        self.assertEqual(self.seren_parse(seren.episode_url(1393, 2, 5)),
                         ('getSources', {'item_type': 'episode', 'trakt_id': 1393, 'season': 2, 'episode': 5}))
        self.assertEqual(self.seren_parse(seren.show_url(1393)),
                         ('showSeasons', {'mediatype': 'tvshow', 'trakt_id': 1393}))
        self.assertEqual(self.seren_parse(seren.show_search_url('Law & Order: 100% "SVU"')),
                         ('showsSearchResults', 'Law & Order: 100% "SVU"'))

    def test_trakt_ids_from_seren_playback(self):
        from tms import seren
        raw = {'mediatype': 'episode', 'showtitle': 'The Bear', 'season': 2, 'episode': 5,
               'uniqueid': {'tvshow.tmdb': '136315', 'tvshow.trakt': '190000', 'trakt': '555'}}
        self.assertEqual(seren.ids_from_raw(raw), (190000, 136315, 555))
        media = Recorder(self.make_store(), FakeTmdb()).identify(raw)[1]
        self.assertEqual((media['show_tmdb'], media['show_trakt']), (136315, 190000))

    def test_stale_window_property_ignored(self):
        from tms.recorder import attach_trakt
        # Window property left over from an earlier Seren show must not label a different show.
        raw = {'seren_ids': {'trakt': 1, 'tmdb': 999}}
        media = attach_trakt(model.episode_media(136315, 'The Bear', 1, 1), raw)
        self.assertNotIn('show_trakt', media)
        raw = {'seren_ids': {'trakt': 190000, 'tmdb': 136315}}
        self.assertEqual(attach_trakt(model.episode_media(136315, 'The Bear', 1, 1), raw)['show_trakt'], 190000)

    def test_show_trakt_reaches_state_and_seren_db_lookup(self):
        import sqlite3
        from tms import seren
        store = self.make_store()
        media = dict(model.episode_media(136315, 'The Bear', 1, 1), show_trakt=190000)
        store.add_event('watched', model.media_key(media), media=media)
        self.assertEqual(State.build(store.events()).shows()[136315]['trakt'], 190000)
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        conn = sqlite3.connect(path)
        conn.execute('CREATE TABLE shows (trakt_id INTEGER PRIMARY KEY, tmdb_id INTEGER)')
        conn.execute('INSERT INTO shows VALUES (190000, 136315)')
        conn.commit()
        conn.close()
        self.addCleanup(os.remove, path)
        self.assertEqual(seren.lookup_show_trakt(path, 136315), 190000)
        self.assertIsNone(seren.lookup_show_trakt(path, 1))
        self.assertIsNone(seren.lookup_show_trakt(path + '.missing', 136315))


class PremiumizeTests(StoreMixin, unittest.TestCase):
    ROOT = 'Night Patrol 1982 Complete Series 720p'

    def fake_cloud(self, tree):
        """tree: {folder_id: (name, parent_id, [items])}; returns a fake http() for Premiumize."""
        calls = []

        def http(method, url, data=None, params=None, headers=None):
            calls.append(url)
            folder_id = (data or {}).get('id')
            if url.endswith('/folder/search'):
                return {'status': 'success', 'content': [
                    {'id': fid, 'name': n, 'type': 'folder'} for fid, (n, _, _) in tree.items()
                    if params['q'].lower() in n.lower()]}
            name, parent, items = tree[folder_id]
            return {'status': 'success', 'name': name, 'parent_id': parent, 'content': items}
        return http, calls

    @staticmethod
    def files(*names):
        return [{'id': n, 'name': n, 'type': 'file', 'link': 'https://pm/' + n} for n in names]

    def test_complete_series_folder_with_season_subfolders(self):
        from tms.premiumize import Premiumize
        tree = {
            'root': (self.ROOT, None, [{'id': 's%d' % s, 'name': 'Season %d' % s, 'type': 'folder'} for s in (1, 2, 3, 4)]
                     + self.files('Night Patrol 1982 Sample.mkv')),
            's1': ('Season 1', 'root', self.files('Night Patrol S01E01 Pilot.mkv')),
            's2': ('Season 2', 'root', self.files('Night Patrol S02E04 Crossroads.mkv',
                                                    'Night Patrol S02E05 The Long Night.mkv')),
            's3': ('Season 3', 'root', []), 's4': ('Season 4', 'root', []),
        }
        http, calls = self.fake_cloud(tree)
        hit = Premiumize('tok', http).find_episode('root', 2, 5, self.ROOT)
        self.assertEqual(hit['link'], "https://pm/Night Patrol S02E05 The Long Night.mkv")
        self.assertEqual(len(calls), 2)  # root, then straight to "Season 2"
        self.assertIsNone(Premiumize('tok', http).find_episode('root', 2, 9, self.ROOT))

    def test_flat_folder_and_episode_only_names(self):
        from tms.premiumize import Premiumize
        tree = {'root': (self.ROOT, None, self.files('Knight.Rider.1982.S03E07.720p.mkv') +
                         [{'id': 'f4', 'name': 'Knight.Rider.S04.720p', 'type': 'folder'}]),
                'f4': ('Knight.Rider.S04.720p', 'root', self.files('E02.mkv', 'E03.mkv'))}
        http, _ = self.fake_cloud(tree)
        self.assertEqual(Premiumize('tok', http).find_episode('root', 3, 7)['id'], 'Knight.Rider.1982.S03E07.720p.mkv')
        self.assertEqual(Premiumize('tok', http).find_episode('root', 4, 3)['id'], 'E03.mkv')

    def test_linked_season_folder_falls_back_to_parent(self):
        from tms.premiumize import Premiumize
        tree = {'root': ('Night Patrol', None, [{'id': 's1', 'name': 'Season 1', 'type': 'folder'},
                                                {'id': 's2', 'name': 'Season 2', 'type': 'folder'}]),
                's1': ('Season 1', 'root', self.files('Night Patrol S01E03.mkv')),
                's2': ('Season 2', 'root', self.files('Night Patrol S02E01.mkv'))}
        http, _ = self.fake_cloud(tree)
        self.assertEqual(Premiumize('tok', http).find_episode('s1', 2, 1, 'Season 1')['id'], 'Night Patrol S02E01.mkv')

    def test_not_signed_in(self):
        from tms.premiumize import Premiumize, PremiumizeError
        with self.assertRaises(PremiumizeError):
            Premiumize('').find_episode('x', 1, 1)

    def test_seren_folder_path_and_links_in_state(self):
        import json
        from urllib.parse import quote
        from tms import seren
        args = {'debrid_provider': 'premiumize', 'id': 'abc123', 'name': self.ROOT, 'type': 'folder'}
        path = seren.BASE + '?action=myFilesFolder&action_args=' + quote(quote(json.dumps(args), safe=''), safe='')
        self.assertEqual(seren.pm_folder_from_path(path), {'id': 'abc123', 'name': self.ROOT})
        self.assertIsNone(seren.pm_folder_from_path('plugin://plugin.video.seren/?action=showsHome'))

        store = self.make_store()
        auto = dict(model.episode_media(9001, 'Night Patrol', 1, 1), pm_folder={'id': 'auto', 'name': 'x'})
        store.add_event('watched', model.media_key(auto), media=auto)
        self.assertEqual(State.build(store.events()).shows()[9001]['pm']['id'], 'auto')
        store.add_event('link', model.show_key(9001), pm={'id': 'abc123', 'name': self.ROOT})
        self.assertEqual(State.build(store.events()).shows()[9001]['pm']['id'], 'abc123')
        store.add_event('unlink', model.show_key(9001))
        state = State.build(store.events())
        self.assertIsNone(state.shows()[9001]['pm'])
        self.assertEqual(state.unidentified(), [])  # show-level events never look like unknown videos


class RemoteTests(unittest.TestCase):
    def cmd(self, **kw):
        import json
        base = {'id': 'c1', 'ts': 1000.0, 'target': 'box1', 'action': 'play', 'show_tmdb': 9001,
                'season': 1, 'episode': 16, 'title': 'Night Patrol', 'mode': 'auto'}
        base.update(kw)
        return json.dumps(base).encode('utf-8')

    def test_parse_and_targeting(self):
        from tms import remote
        c = remote.parse_command(self.cmd())
        self.assertTrue(remote.should_run(c, 'box1', [], now=1010))
        self.assertFalse(remote.should_run(c, 'box2', [], now=1010))           # other TV
        self.assertFalse(remote.should_run(c, 'box1', ['c1'], now=1010))       # already done
        self.assertFalse(remote.should_run(c, 'box1', [], now=1000 + 600))     # stale (Kodi was closed)
        self.assertTrue(remote.should_run(remote.parse_command(self.cmd(target='any')), 'box2', [], now=1001))
        self.assertIsNone(remote.parse_command(b'not json'))
        self.assertEqual(remote.parse_command(self.cmd(mode='weird'))['mode'], 'auto')

    def test_launch_target_prefers_premiumize_then_seren(self):
        from tms import remote
        c = remote.parse_command(self.cmd())
        kind, url = remote.launch_target({'pm': {'id': 'f1'}}, c, True, 1234)
        self.assertEqual(kind, 'smart')  # TrackMyShows decides: Premiumize first, then Seren
        self.assertIn('action=smartplay', url)
        self.assertIn('show=9001', url)
        kind, url = remote.launch_target({'pm': None}, remote.parse_command(self.cmd(mode='pick')), True, 1234)
        self.assertEqual(kind, 'smart')
        self.assertIn('mode=pick', url)  # smartplay asks Seren for source selection
        seren_only = remote.parse_command(self.cmd(mode='seren'))
        self.assertEqual(remote.launch_target(None, seren_only, False, None), (None, 'Seren is not installed on this box'))


class SerenStaleTests(unittest.TestCase):
    def test_episode_check_and_refresh_flag(self):
        import sqlite3
        from tms import seren
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self.addCleanup(os.remove, path)
        conn = sqlite3.connect(path)
        conn.execute('CREATE TABLE shows (trakt_id INTEGER PRIMARY KEY, tmdb_id INTEGER, needs_milling BOOLEAN DEFAULT 0)')
        conn.execute('CREATE TABLE episodes (trakt_id INTEGER, trakt_show_id INTEGER, season INTEGER, number INTEGER)')
        conn.execute('INSERT INTO shows VALUES (5001, 7001, 0)')
        conn.executemany('INSERT INTO episodes VALUES (?, 5001, 13, ?)', [(i, i) for i in range(1, 19)])
        conn.commit()
        conn.close()
        self.assertTrue(seren.episode_known(path, 5001, 13, 18))
        self.assertFalse(seren.episode_known(path, 5001, 13, 19))   # Seren's copy is stale
        self.assertIsNone(seren.episode_known(path + '.missing', 5001, 13, 19))
        self.assertTrue(seren.request_refresh(path, 5001))
        check = sqlite3.connect(path)
        flag = check.execute('SELECT needs_milling FROM shows WHERE trakt_id=5001').fetchone()[0]
        check.close()
        self.assertEqual(flag, 1)


class DiscoveryTests(PremiumizeTests):
    def test_discover_by_title_and_year(self):
        from tms.premiumize import Premiumize, folder_from_path
        tree = {
            'kr08': ('Night Patrol 2008 S01 1080p WEB', None, self.files('Night Patrol 2008 S01E16.mkv')),
            'kr82': (self.ROOT, None, [{'id': 's1', 'name': 'Season 1', 'type': 'folder'}]),
            's1': ('Season 1', 'kr82', self.files('Night Patrol S01E16 Midnight Run.mkv')),
            'other': ('Day Patrol Documentary', None, self.files('Day Patrol S01E16.mkv')),
        }
        http, _ = self.fake_cloud(tree)
        folder, hit = Premiumize('tok', http).discover('Night Patrol', 1982, 1, 16)
        self.assertEqual(folder['id'], 'kr82')                 # the 2008 remake's folder is skipped by year
        self.assertEqual(hit['name'], 'Night Patrol S01E16 Midnight Run.mkv')
        self.assertIsNone(Premiumize('tok', http).discover('Night Patrol', 1982, 9, 1))
        self.assertIsNone(Premiumize('tok', http).discover('Sky Patrol', 1984, 1, 1))
        # the official Premiumize add-on's folder URLs are recognised for automatic linking
        self.assertEqual(folder_from_path('plugin://plugin.video.premiumizemetv/?folder_id=abc&mode=file_list', 'Season 1'),
                         {'id': 'abc', 'name': 'Season 1'})
        self.assertIsNone(folder_from_path('plugin://plugin.video.premiumizemetv/?mode=transfer_list'))

    def test_apikey_login(self):
        from tms.premiumize import Premiumize
        seen = {}

        def http(method, url, data=None, params=None, headers=None):
            seen.update(params=params, headers=headers)
            return {'status': 'success', 'content': []}
        Premiumize('', http, apikey='pin123').search('x')
        self.assertEqual(seen['params']['apikey'], 'pin123')
        self.assertNotIn('Authorization', seen['headers'])


class MovieListTests(PremiumizeTests):
    def test_watchlist_in_state(self):
        store = self.make_store()
        dune = model.movie_media(438631, 'Dune', 2021)
        heat = model.movie_media(949, 'Heat', 1995)
        store.add_event('watchlist', 'movie:438631', media=dune)
        store.add_event('watchlist', 'movie:949', media=heat)
        state = State.build(store.events())
        self.assertEqual([w['media']['title'] for w in state.movies_to_watch()], ['Dune', 'Heat'])
        self.assertEqual(state.unidentified(), [])
        store.add_event('watched', 'movie:438631', media=dune)
        store.add_event('unwatchlist', 'movie:949')
        state = State.build(store.events())
        self.assertEqual(state.movies_to_watch(), [])
        self.assertEqual([m.media['title'] for m in state.movies()], ['Dune'])

    def test_find_movie_in_cloud(self):
        from tms.premiumize import Premiumize
        tree = {
            'f1': ('Dune.Part.Two.2024.2160p.WEB', None, []),
            'f2': ('Dune 2021 1080p BluRay', None, [
                {'id': 's', 'name': 'Sample.mkv', 'type': 'file', 'size': 10, 'link': 'x'},
                {'id': 'm', 'name': 'Dune.2021.1080p.BluRay.x264.mkv', 'type': 'file', 'size': 9000, 'link': 'https://pm/dune'}]),
            'f3': ('Dune 1984 Remastered', None, self.files('Dune.1984.mkv')),
        }
        results = [{'id': fid, 'name': n, 'type': 'folder'} for fid, (n, _, _) in tree.items()]

        def http(method, url, data=None, params=None, headers=None):
            if url.endswith('/folder/search'):
                return {'status': 'success', 'content': results}
            name, parent, items = tree[(data or {}).get('id')]
            return {'status': 'success', 'name': name, 'content': items}
        pm = Premiumize('tok', http)
        self.assertEqual(pm.find_movie('Dune', 2021)['link'], 'https://pm/dune')  # not Part Two, not 1984, not the sample
        self.assertEqual(pm.find_movie('Dune', 1984)['name'], 'Dune.1984.mkv')
        self.assertIsNone(pm.find_movie('Heat', 1995))

    def test_movie_remote_command(self):
        import json
        from tms import remote
        cmd = remote.parse_command(json.dumps({'id': 'm1', 'ts': 1000, 'target': 'box1', 'action': 'play', 'kind': 'movie',
                                               'movie_tmdb': 438631, 'title': 'Dune', 'year': 2021}).encode())
        kind, url = remote.launch_target(None, cmd, True, None)
        self.assertEqual(kind, 'smart')
        self.assertIn('action=smartmovie', url)
        self.assertIn('movie=438631', url)
        self.assertIn('year=2021', url)


class LaunchAppTests(unittest.TestCase):
    def test_open_streaming_app(self):
        import json
        from tms import remote
        cmd = remote.parse_command(json.dumps({'id': 'l1', 'ts': 1000, 'target': 'box1', 'action': 'launch',
                                               'app': 'netflix', 'service_id': '80057281', 'title': 'Stranger Things'}).encode())
        self.assertTrue(remote.should_run(cmd, 'box1', [], now=1001))
        installed = ['com.netflix.ninja', 'com.amazon.firebat', 'ca.bellmedia.cravetv']
        self.assertEqual(remote.launch_app(cmd, installed), ('com.netflix.ninja', 'https://www.netflix.com/title/80057281'))
        self.assertEqual(remote.android_builtin('com.netflix.ninja', 'https://www.netflix.com/title/80057281'),
                         'StartAndroidActivity("com.netflix.ninja","android.intent.action.VIEW","","https://www.netflix.com/title/80057281")')
        prime = dict(cmd, app='prime', service_id='B07QQQ52B3')
        self.assertEqual(remote.launch_app(prime, installed)[1], 'https://app.primevideo.com/detail?asin=B07QQQ52B3')
        # no ID known: open the app's search (BritBox/Crave channels arrive here as app='prime')
        self.assertEqual(remote.launch_app(dict(cmd, app='prime', service_id='', title='Sherlock'), installed)[1],
                         'https://app.primevideo.com/search?phrase=Sherlock')
        self.assertEqual(remote.launch_app(dict(cmd, service_id='', title="The Queen's Gambit"), installed)[1],
                         'nflx://www.netflix.com/search?q=The%20Queen%27s%20Gambit')
        self.assertEqual(remote.launch_app(dict(cmd, app='crave', service_id=''), installed), ('ca.bellmedia.cravetv', None))
        self.assertEqual(remote.android_builtin('ca.bellmedia.cravetv'), 'StartAndroidActivity("ca.bellmedia.cravetv")')
        self.assertEqual(remote.launch_app(dict(cmd, app='britbox'), installed), (None, 'BritBox is not installed on this TV'))
        self.assertIsNone(remote.parse_command(json.dumps({'id': 'x', 'ts': 1, 'action': 'launch', 'app': 'hbo'}).encode()))

    def test_installed_app_listing(self):
        from tms import remote
        files = [{'file': 'androidapp://sources/apps/com.netflix.ninja.png', 'label': 'Netflix'},
                 {'file': 'androidapp://sources/apps/ca.bellmedia.cravetv/', 'label': 'Crave'}]
        self.assertEqual(remote.packages_from_listing(files), ['com.netflix.ninja', 'ca.bellmedia.cravetv'])
