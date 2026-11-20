"""Group native display updates for ephemeral Review objects."""

from contextlib import contextmanager, nullcontext


@contextmanager
def display_updates(api):
    pause = getattr(api, "UndoPauseCM", nullcontext)
    was_flushing = getattr(api, "lock_api_allow_flush", False)
    if was_flushing:
        api.block_flush()
    try:
        with pause():
            yield
    finally:
        if was_flushing:
            api.unblock_flush()
