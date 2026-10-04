// Phone app logic tests. Run: node --test tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as S from '../phone/js/state.js';

const BEAR = {
  id: 136315, name: 'The Bear', status: 'Returning Series', poster_path: '/p.jpg',
  seasons: [{ season_number: 0, episode_count: 2 }, { season_number: 1, episode_count: 8, air_date: '2022-06-23' },
            { season_number: 2, episode_count: 10, air_date: '2023-06-22' },
            { season_number: 3, episode_count: 10, air_date: '2026-09-20' }],
  last_episode_to_air: { season_number: 3, episode_number: 4, air_date: '2026-09-28' },
  next_episode_to_air: { season_number: 3, episode_number: 5, air_date: '2026-10-05' },
};
const TODAY = Date.parse('2026-10-03T12:00:00');
let n = 0;
const ev = (type, key, extra = {}) => ({ id: `e${++n}`, device: 'd1', ts: ++n, type, key, ...extra });
const ep = (s, e) => ev('watched', S.epKey(136315, s, e), { media: S.episodeMedia(136315, 'The Bear', s, e) });

test('follow without watches starts at S01E01', () => {
  const state = S.buildState([ev('follow', 'show:136315', { media: { kind: 'show', show_tmdb: 136315, show_title: 'The Bear' } })]);
  const show = S.collectShows(state).get(136315);
  assert.ok(show.active);
  const nu = S.nextUp(show, BEAR);
  assert.equal(nu.status, 'available');
  assert.deepEqual(nu.next, [1, 1]);
  assert.equal(nu.airedCount, 22);
});

test('watched episodes advance and roll over seasons; unwatched undoes', () => {
  const events = [];
  for (let e = 1; e <= 8; e++) events.push(ep(1, e));
  let show = S.collectShows(S.buildState(events)).get(136315);
  assert.deepEqual(S.nextUp(show, BEAR).next, [2, 1]);
  events.push(ep(2, 1), ev('unwatched', S.epKey(136315, 2, 1)));
  show = S.collectShows(S.buildState(events)).get(136315);
  assert.deepEqual(S.nextUp(show, BEAR).next, [2, 1]);
});

test('caught up shows next air date', () => {
  const events = [];
  for (let e = 1; e <= 4; e++) events.push(ep(3, e));
  const show = S.collectShows(S.buildState(events)).get(136315);
  const nu = S.nextUp(show, BEAR);
  assert.equal(nu.status, 'caught_up');
  assert.equal(nu.nextAirDate, '2026-10-05');
});

test('hide deactivates; releases only for active shows', () => {
  const tvMap = new Map([[136315, BEAR]]);
  const events = [ep(2, 10)];
  let shows = S.collectShows(S.buildState(events));
  let r = S.releases(shows, tvMap, TODAY);
  assert.equal(r.fresh[0].kind, 'season');
  assert.equal(r.fresh[0].season, 3);
  assert.equal(r.upcoming[0].episode, 5);
  events.push(ev('hide', 'show:136315'));
  shows = S.collectShows(S.buildState(events));
  assert.equal(shows.get(136315).active, false);
  r = S.releases(shows, tvMap, TODAY);
  assert.equal(r.fresh.length + r.upcoming.length, 0);
});

test('new episode in current season is flagged', () => {
  const shows = S.collectShows(S.buildState([ep(3, 2)]));
  const r = S.releases(shows, new Map([[136315, BEAR]]), TODAY);
  assert.equal(r.fresh[0].kind, 'episode');
  assert.deepEqual([r.fresh[0].season, r.fresh[0].episode], [3, 4]);
});

test('resolve events relabel unidentified Kodi items', () => {
  const state = S.buildState([
    ev('watched', 'raw:abc', { raw: { file: 'x.mkv' } }),
    ev('resolve', 'raw:abc', { media: S.episodeMedia(136315, 'The Bear', 1, 2) }),
  ]);
  assert.ok(S.isWatched(state, S.epKey(136315, 1, 2)));
});

test('movie watch list: add, watch, remove', () => {
  const dune = S.movieMedia(438631, 'Dune', 2021);
  const heat = S.movieMedia(949, 'Heat', 1995);
  const events = [ev('watchlist', 'movie:438631', { media: dune }), ev('watchlist', 'movie:949', { media: heat })];
  let state = S.buildState(events);
  assert.deepEqual(S.moviesToWatch(state).map((m) => m.media.title), ['Dune', 'Heat']);
  events.push(ev('watched', 'movie:438631', { media: dune }), ev('unwatchlist', 'movie:949'));
  state = S.buildState(events);
  assert.equal(S.moviesToWatch(state).length, 0);
  assert.deepEqual(S.moviesWatched(state).map((it) => it.media.title), ['Dune']);
  assert.equal(S.collectShows(state).size, 0); // movie events never create shows
});

test('discover hides followed, listed, watched and dismissed titles', () => {
  const events = [
    ev('follow', 'show:136315', { media: { kind: 'show', show_tmdb: 136315, show_title: 'The Bear' } }),
    ev('watchlist', 'movie:949', { media: S.movieMedia(949, 'Heat', 1995) }),
    ev('watched', 'movie:603', { media: S.movieMedia(603, 'The Matrix', 1999) }),
    ev('dismiss', 'show:1399', { reason: 'seen' }),
    ev('dismiss', 'movie:550', { reason: 'no' }),
  ];
  let state = S.buildState(events);
  let shows = S.collectShows(state);
  const known = (kind, id) => S.alreadyKnown(state, shows, kind, id);
  assert.ok(known('tv', 136315) && known('tv', 1399));
  assert.ok(known('movie', 949) && known('movie', 603) && known('movie', 550));
  assert.ok(!known('tv', 1) && !known('movie', 1));
  events.push(ev('undismiss', 'show:1399'));
  state = S.buildState(events);
  shows = S.collectShows(state);
  assert.ok(!S.alreadyKnown(state, shows, 'tv', 1399));
  assert.equal(shows.size, 1); // dismiss never creates a show
});
