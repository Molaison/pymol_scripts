"""Serialize captured settings writes without blocking Qt or losing errors."""

from concurrent.futures import ThreadPoolExecutor
from queue import Empty, Queue
import threading
import traceback


_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="annotator-settings")
_pending = {}
_lock = threading.Lock()
_errors = Queue()


def _finished(future):
    if future.cancelled():
        return
    error = future.exception()
    if error is not None:
        traceback.print_exception(type(error), error, error.__traceback__)
        _errors.put(str(error))


def submit_write(key, function, *args, **kwargs):
    """Keep the newest queued write per destination; never cancel a running write."""
    with _lock:
        previous = _pending.get(key)
        if previous is not None:
            previous.cancel()
        future = _executor.submit(function, *args, **kwargs)
        _pending[key] = future
        future.add_done_callback(_finished)
    return future


def pending_errors():
    errors = []
    while True:
        try:
            errors.append(_errors.get_nowait())
        except Empty:
            return errors


def flush_writes(timeout=30):
    """Wait at test/process handoff, never from a navigation click handler."""
    with _lock:
        futures = list(_pending.values())
    for future in futures:
        if not future.cancelled():
            future.result(timeout=timeout)
