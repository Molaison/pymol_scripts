"""Run candidate views after loading, with molecular calculations off the Qt thread."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor

from pymol.Qt import QtCore

class _CavityCompletion(QtCore.QObject):
    ready = QtCore.Signal(object)


class CavityJobs:
    def __init__(self, parent, is_current, status, *, on_finished=None):
        self.is_current, self._cavity_status = is_current, status
        self.on_finished = on_finished
        self.cavity_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pymol-cavity")
        self._cavity_completion = _CavityCompletion(parent)
        connection = getattr(QtCore.Qt, "ConnectionType", QtCore.Qt)
        self._cavity_completion.ready.connect(self._advance_cavity, connection.QueuedConnection)
        self.cavity_job = None

    def cancel_cavity(self):
        job, self.cavity_job = self.cavity_job, None
        if job is not None:
            job[0].cancel()

    def close_cavity(self):
        self.cancel_cavity()
        self.cavity_pool.shutdown(wait=False, cancel_futures=True)

    def start_cavity(self, title, compute, draw):
        self.cancel_cavity()
        future = self.cavity_pool.submit(compute)
        self.cavity_job = (future, draw, title)
        future.add_done_callback(self._notify_cavity_ready)
        self._cavity_status(f"{title} · calculating…")

    def _notify_cavity_ready(self, future):
        try:
            self._cavity_completion.ready.emit(future)
        except RuntimeError:
            # The parent Qt widget can close before a running worker finishes.
            pass

    def _advance_cavity(self, future):
        if self.cavity_job is None or future is not self.cavity_job[0]:
            return
        _, draw, title = self.cavity_job
        self.cavity_job = None
        if not self.is_current(self.identity):
            if self.on_finished is not None:
                self.on_finished()
            return
        try:
            self._cavity_status(*draw(future.result()))
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            self._cavity_status(f"{title} skipped · {detail.splitlines()[0]}")
        if self.on_finished is not None:
            self.on_finished()


class ViewJobs(CavityJobs):
    def __init__(self, panel, is_current, prepare, draw):
        super().__init__(panel, is_current, self._status)
        self.prepare, self.draw = prepare, draw
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
        self.notice = None
        self.queue.extend(recipes)
        if recipes:
            self._status(f"{title or 'Saved view'} · preparing…")
            self.timer.start()
        else:
            self._status(self.warnings[-1] if self.warnings else "")

    def cancel(self):
        self.timer.stop()
        self.cancel_cavity()
        self.queue.clear()
        if self.job is not None:
            self.job[0].cancel()
            self.job = None

    def append(self, identity, recipe, *, title):
        if getattr(self, "identity", None) != identity:
            self.start(identity, [recipe], title=title)
            return
        if recipe not in self.queue:
            self.queue.append(recipe)
        if not self.timer.isActive():
            self.title = title
            self._status(f"{title} · preparing…")
            self.timer.start()

    def close(self):
        self.cancel()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.close_cavity()

    def _status(self, message, tooltip=""):
        self.panel.quick_view_status.setText(message)
        self.panel.quick_view_status.setToolTip(tooltip or message)

    def _advance(self):
        if not self.is_current(self.identity):
            self.cancel()
            self._status("")
            return
        try:
            notice = None
            if self.job is not None:
                future, owner, source, action, draw = self.job
                if not future.done():
                    return
                self.job = None
                notice = self.draw(owner, source, action, draw, future.result(), self.exclusive)
            elif self.queue:
                owner, line = self.queue.popleft()
                source, action, compute, draw = self.prepare(owner, line)
                if compute is None:
                    notice = self.draw(owner, source, action, draw, None, self.exclusive)
                else:
                    self.job = (self.pool.submit(compute), owner, source, action, draw)
            if notice is not None:
                self.notice = notice
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            message = f"{self.title or 'Saved view'} skipped · {detail.splitlines()[0]}"
            print(f"Structure Annotator: {message}")
            self.warnings.append(message)
        if self.job is None and not self.queue:
            self.timer.stop()
            if self.cavity_job is None:
                message = self.warnings[-1] if self.warnings else ""
                if self.notice is not None:
                    text = "\n".join(filter(None, (self.notice.text, message)))
                    tooltip = "\n".join(filter(None, (self.notice.tooltip, message)))
                    self._status(text, tooltip)
                else:
                    self._status(message)
