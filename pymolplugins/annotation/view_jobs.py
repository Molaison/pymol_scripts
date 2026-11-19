"""Run candidate views after loading, with molecular calculations off the Qt thread."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor

from pymol.Qt import QtCore

class ViewJobs:
    def __init__(self, panel, is_current, prepare, draw):
        self.is_current, self.prepare, self.draw = is_current, prepare, draw
        self.panel = panel
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pymol-review-view")
        self.timer = QtCore.QTimer(panel)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self._advance)
        self.queue = deque()
        self.job = None

    def start(self, identity, recipes, *, title="", warnings=(), exclusive=False):
        self.cancel()
        self.identity, self.title, self.exclusive = identity, title, exclusive
        self.warnings = list(warnings)
        self.queue.extend(recipes)
        if recipes:
            self._status(f"{title or 'Saved view'} · preparing…")
            self.timer.start()
        else:
            self._status(self.warnings[-1] if self.warnings else "")

    def cancel(self):
        self.timer.stop()
        self.queue.clear()
        if self.job is not None:
            self.job[0].cancel()
            self.job = None

    def close(self):
        self.cancel()
        self.pool.shutdown(wait=False, cancel_futures=True)

    def _status(self, message):
        self.panel.quick_view_status.setText(message)
        self.panel.quick_view_status.setToolTip(message)

    def _advance(self):
        if not self.is_current(self.identity):
            self.cancel()
            self._status("")
            return
        try:
            if self.job is not None:
                future, owner, source, action, draw = self.job
                if not future.done():
                    return
                self.job = None
                self.draw(owner, source, action, draw, future.result(), self.exclusive)
            elif self.queue:
                owner, line = self.queue.popleft()
                source, action, compute, draw = self.prepare(owner, line)
                if compute is None:
                    self.draw(owner, source, action, draw, None, self.exclusive)
                else:
                    self.job = (self.pool.submit(compute), owner, source, action, draw)
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            message = f"{self.title or 'Saved view'} skipped · {detail.splitlines()[0]}"
            print(f"Structure Annotator: {message}")
            self.warnings.append(message)
        if self.job is None and not self.queue:
            self.timer.stop()
            self._status(self.warnings[-1] if self.warnings else "")
