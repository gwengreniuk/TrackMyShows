"""Follow one playback session and decide when it counts as watched."""


class Tracker:
    MIN_PROGRESS_SECONDS = 60
    MIN_PROGRESS_PCT = 2.0

    def __init__(self, recorder, threshold=85, min_seconds=300, log=None):
        self.recorder = recorder
        self.log = log or (lambda msg: None)
        self.session = None
        self._recorded = False
        self.configure(threshold, min_seconds)

    def configure(self, threshold=85, min_seconds=300):
        self.threshold = max(1, min(100, threshold))
        self.min_seconds = max(0, min_seconds)

    @property
    def active(self):
        return self.session is not None

    def start(self, raw):
        self.finish()
        self.session = {'raw': raw, 'pos': 0.0, 'total': 0.0, 'watched': False}

    def _eligible(self, s):
        return s['total'] > 0 and s['total'] >= self.min_seconds

    def tick(self, position, total):
        s = self.session
        if not s:
            return
        if position and position > 0:
            s['pos'] = float(position)
        if total and total > 0:
            s['total'] = float(total)
        if not s['watched'] and self._eligible(s) and s['pos'] / s['total'] * 100 >= self.threshold:
            s['watched'] = True  # record now, not at stop: the box may be switched off during credits
            self._record('watched', s['raw'])

    def finish(self):
        s, self.session = self.session, None
        if not s or s['watched'] or not self._eligible(s):
            return
        pct = s['pos'] / s['total'] * 100
        if s['pos'] >= self.MIN_PROGRESS_SECONDS and pct >= self.MIN_PROGRESS_PCT:
            self._record('progress', s['raw'], {'position': round(s['pos']), 'total': round(s['total']),
                                                'pct': round(pct, 1)})

    def _record(self, type_, raw, progress=None):
        try:
            self.recorder.record(type_, raw, progress)
        except Exception as e:
            self.log('failed to record %s: %s' % (type_, e))
        self._recorded = True

    def pop_recorded(self):
        recorded, self._recorded = self._recorded, False
        return recorded
