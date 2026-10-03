"""Local SQLite storage: the merged event log, small settings/meta values, and an HTTP cache.

History is an append-only log of events. Every device keeps all events
(its own plus those imported from other devices) and uploads only its own.
"""
import json
import os
import sqlite3
import threading
import time
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    device TEXT NOT NULL,
    ts REAL NOT NULL,
    body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_device ON events(device);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, ts REAL NOT NULL, v TEXT NOT NULL);
"""

REQUIRED_FIELDS = ('id', 'device', 'ts', 'type', 'key')


class Store:
    def __init__(self, path, device_name=''):
        folder = os.path.dirname(path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        self.device_name = device_name
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
        self._db.executescript(SCHEMA)

    def close(self):
        with self._lock:
            self._db.close()

    def _exec(self, sql, args=()):
        with self._lock:
            return self._db.execute(sql, args)

    # ---- meta ---------------------------------------------------------
    def meta_get(self, key, default=None):
        row = self._exec('SELECT v FROM meta WHERE k=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def meta_set(self, key, value):
        self._exec('INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)', (key, json.dumps(value)))

    def meta_delete(self, key):
        self._exec('DELETE FROM meta WHERE k=?', (key,))

    def device_id(self):
        value = self.meta_get('device_id')
        if not value:
            value = uuid.uuid4().hex
            self.meta_set('device_id', value)
        return value

    def change_seq(self):
        return self.meta_get('change_seq', 0)

    def _bump(self):
        self._exec("INSERT INTO meta(k, v) VALUES('change_seq', '1') "
                   "ON CONFLICT(k) DO UPDATE SET v = CAST(v AS INTEGER) + 1")

    # ---- events -------------------------------------------------------
    def new_event(self, type_, key, **fields):
        event = {'v': 1, 'id': uuid.uuid4().hex, 'device': self.device_id(), 'ts': time.time(),
                 'type': type_, 'key': key}
        if self.device_name:
            event['device_name'] = self.device_name
        event.update({k: v for k, v in fields.items() if v is not None})
        return event

    def add_event(self, type_, key, **fields):
        event = self.new_event(type_, key, **fields)
        self.add_events([event])
        return event

    def add_events(self, events):
        with self._lock:
            self._db.execute('BEGIN')
            try:
                for e in events:
                    self._db.execute('INSERT OR IGNORE INTO events(id, device, ts, body) VALUES(?, ?, ?, ?)',
                                     (e['id'], e['device'], e['ts'], json.dumps(e, separators=(',', ':'))))
                self._db.execute('COMMIT')
            except Exception:
                self._db.execute('ROLLBACK')
                raise
            self._bump()

    def import_events(self, events):
        """Insert events from another device; returns how many were new."""
        added = 0
        with self._lock:
            self._db.execute('BEGIN')
            try:
                for e in events:
                    if not all(f in e for f in REQUIRED_FIELDS):
                        continue
                    cur = self._db.execute('INSERT OR IGNORE INTO events(id, device, ts, body) VALUES(?, ?, ?, ?)',
                                           (str(e['id']), str(e['device']), float(e['ts']),
                                            json.dumps(e, separators=(',', ':'))))
                    added += cur.rowcount
                self._db.execute('COMMIT')
            except Exception:
                self._db.execute('ROLLBACK')
                raise
        return added

    def events(self):
        rows = self._exec('SELECT body FROM events ORDER BY ts, id').fetchall()
        return [json.loads(r[0]) for r in rows]

    def own_events_jsonl(self):
        rows = self._exec('SELECT body FROM events WHERE device=? ORDER BY ts, id', (self.device_id(),)).fetchall()
        return ('\n'.join(r[0] for r in rows) + ('\n' if rows else '')).encode('utf-8')

    # ---- cache --------------------------------------------------------
    def cache_get(self, key, max_age=None):
        row = self._exec('SELECT ts, v FROM cache WHERE k=?', (key,)).fetchone()
        if not row:
            return None
        if max_age is not None and time.time() - row[0] > max_age:
            return None
        return json.loads(row[1])

    def cache_set(self, key, value):
        self._exec('INSERT OR REPLACE INTO cache(k, ts, v) VALUES(?, ?, ?)', (key, time.time(), json.dumps(value)))
