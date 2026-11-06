"""Deliver external execution facts without running position/chase observation."""


def worker(store, token, dry_run, stop):
    if dry_run or not token:
        return
    from chasing_runtime import deliver_pending
    from postgres_store import assert_worker
    while not stop.is_set():
        assert_worker(store)
        deliver_pending(store, token, execution_only=True)
        stop.wait(1)
