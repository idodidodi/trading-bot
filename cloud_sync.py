"""Retired: runtime Postgres is authoritative; no mirroring is supported."""

def worker(*args, **kwargs):
    raise RuntimeError("Cloud mirroring was retired; use the Postgres runtime")
