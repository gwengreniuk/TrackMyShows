"""Two-way sync with Google Drive.

Each device owns exactly one file, events-<device id>.jsonl, and only ever
writes that file. Every device reads all the other files and merges them
(events have unique ids, so merging is a plain union). No conflicts possible.
"""
import json
import time

from .net import HttpError


def parse_jsonl(data):
    events = []
    for line in data.decode('utf-8', 'replace').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def sync(store, drive, log=lambda msg: None):
    me = store.device_id()
    files = drive.list_event_files()
    own = sorted((f for f in files if (f.get('appProperties') or {}).get('device') == me),
                 key=lambda f: f.get('modifiedTime', ''), reverse=True)

    uploaded = False
    seq = store.change_seq()
    if seq != store.meta_get('synced_seq') or not own:
        content = store.own_events_jsonl()
        file_id = own[0]['id'] if own else None
        if file_id:
            try:
                drive.update_file(file_id, content)
            except HttpError as e:
                if e.status != 404:
                    raise
                file_id = None
        if not file_id:
            drive.create_file('events-%s.jsonl' % me, content,
                              {'tms': 'events', 'device': me, 'device_name': (store.device_name or '')[:60]})
        store.meta_set('synced_seq', seq)
        uploaded = True

    seen = store.meta_get('drive_seen') or {}
    imported = 0
    others = 0
    for f in files:
        if (f.get('appProperties') or {}).get('device') == me:
            continue
        others += 1
        if seen.get(f['id']) == f.get('modifiedTime'):
            continue
        imported += store.import_events(parse_jsonl(drive.download(f['id'])))
        seen[f['id']] = f.get('modifiedTime')
    store.meta_set('drive_seen', seen)
    store.meta_set('last_sync_ts', time.time())
    log('sync: uploaded=%s imported=%d other_devices=%d' % (uploaded, imported, others))
    return {'uploaded': uploaded, 'imported': imported, 'other_devices': others}
